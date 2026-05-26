"""Dashboard HTML 生成器。

每次实例状态保存后自动调用，生成项目全局首页和单实例详情页。
零外部依赖，HTML 完全自包含。
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from infrastructure.project import find_root
from infrastructure.temp_files import is_temp_file
from infrastructure.timestamp import iso_timestamp
from services.resolver import find_workflow_dir
from compat.workflow.registry import load_workflow
from domain.dag.graph import build_adjacency



# ── 状态颜色映射 ──
_STATUS_COLORS = {
    "PENDING": "#757575",
    "RUNNING": "#ff9800",
    "AWAITING_CONFIRM": "#e91e63",
    "DONE": "#4caf50",
    "ERROR": "#f44336",
    "CONFLICT": "#9c27b0",
}

_STATUS_LABELS = {
    "PENDING": "PENDING",
    "RUNNING": "RUNNING",
    "AWAITING_CONFIRM": "WAITING",
    "DONE": "DONE",
    "ERROR": "ERROR",
    "CONFLICT": "CONFLICT",
}

_FILE_STATUS_ICONS = {
    "M": "M",
    "A": "A",
    "D": "D",
    "R": "R",
    "C": "C",
    "?": "?",
}

_FILE_STATUS_COLORS = {
    "M": "#ff9800",
    "A": "#4caf50",
    "D": "#f44336",
    "R": "#2196f3",
    "C": "#9c27b0",
    "?": "#757575",
}

_MMD_STATUS_CLASS = {
    "PENDING": "pending",
    "RUNNING": "running",
    "AWAITING_CONFIRM": "awaiting",
    "DONE": "done",
    "ERROR": "error",
    "CONFLICT": "conflict",
}


def _normalize_modified_files(raw: list | None) -> list[dict]:
    """兼容旧格式（字符串列表）和新格式（对象列表）。"""
    if not raw:
        return []
    if isinstance(raw[0], str):
        return [{"path": p, "status": "M"} for p in raw]
    return raw


def _status_color(status: str) -> str:
    return _STATUS_COLORS.get(status, "#757575")


def _build_file_link(instance_id: str, stage_instance_id: str, file_path: str) -> tuple[str, str]:
    """构建 vscode://file/ 链接，返回 (url, badge)。"""
    root = find_root()
    stage_wt = root / ".tmp" / "worktrees" / f"stage-{instance_id}-{stage_instance_id}"
    inst_wt = root / ".tmp" / "worktrees" / f"instance-{instance_id}"

    if stage_wt.exists():
        base = stage_wt
        badge = "worktree"
    elif inst_wt.exists():
        base = inst_wt
        badge = "merged"
    else:
        base = root
        badge = "main"

    abs_path = (base / file_path).resolve()

    # 兜底：记录的路径不存在时，尝试带点/不带点的变体
    if not abs_path.exists():
        p = Path(file_path)
        alt_name = f".{p.name}" if not p.name.startswith(".") else p.name[1:]
        alt_path = (base / p.parent / alt_name).resolve()
        if alt_path.exists():
            abs_path = alt_path

    # Windows 路径转 URI：反斜杠 -> 正斜杠
    path_str = str(abs_path).replace("\\", "/")
    if path_str[1:2] == ":":
        # 保留盘符中的 ASCII 冒号，编码其余特殊字符（中文等）
        drive = path_str[:2]  # e.g. "E:"
        rest = quote(path_str[2:], safe="/")
        path_str = f"/{drive}{rest}"
    else:
        path_str = quote(path_str, safe="/")

    return f"vscode://file{path_str}", badge


def _load_instance_json(instance_id: str) -> dict | None:
    root = find_root()
    path = root / ".agent" / "instances" / instance_id / "instance.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _load_messages(instance_id: str) -> dict[str, dict]:
    """加载实例的所有消息，返回 message_id -> msg dict。"""
    root = find_root()
    messages_dir = root / ".agent" / "instances" / instance_id / "messages"
    if not messages_dir.exists():
        return {}

    messages: dict[str, dict] = {}
    for msg_file in messages_dir.glob("msg-*.json"):
        try:
            data = json.loads(msg_file.read_text(encoding="utf-8"))
            mid = data.get("message_id")
            if mid:
                messages[mid] = data
        except Exception:
            continue
    return messages


def _load_timeline(instance_id: str, limit: int = 20) -> list[dict]:
    root = find_root()
    timeline_path = root / ".agent" / "instances" / instance_id / "logs" / "timeline.jsonl"
    if not timeline_path.exists():
        return []

    lines = timeline_path.read_text(encoding="utf-8").strip().splitlines()
    events = []
    for line in lines[-limit:]:
        try:
            events.append(json.loads(line))
        except Exception:
            continue
    return events


def _collect_instance_data(instance_id: str) -> dict | None:
    """收集单个实例的完整数据，用于渲染 dashboard。"""
    data = _load_instance_json(instance_id)
    if data is None:
        return None

    messages = _load_messages(instance_id)
    timeline = _load_timeline(instance_id, limit=30)

    stages = data.get("stages", [])
    stages_summary = {
        "total": len(stages),
        "pending": 0,
        "running": 0,
        "awaiting_confirm": 0,
        "done": 0,
        "error": 0,
        "conflict": 0,
    }
    for s in stages:
        status = s.get("status", "PENDING")
        key = status.lower()
        if key in stages_summary:
            stages_summary[key] += 1

    # 构建 stage 详情列表（包含文件信息）
    stage_details = []
    all_files = []
    for s in stages:
        sid = s.get("stage_id", "")
        s_inst_id = s.get("stage_instance_id", sid)
        status = s.get("status", "PENDING")
        msg_id = s.get("output_message_id")

        files = []
        if msg_id and msg_id in messages:
            msg = messages[msg_id]
            raw_files = msg.get("modified_files", [])
            msg_ts = msg.get("timestamp", "")
            for entry in _normalize_modified_files(raw_files):
                if is_temp_file(entry["path"]):
                    continue
                link, badge = _build_file_link(instance_id, s_inst_id, entry["path"])
                files.append({
                    "path": entry["path"],
                    "filename": Path(entry["path"]).name,
                    "status": entry.get("status", "M"),
                    "link": link,
                    "badge": badge,
                    "stage_id": sid,
                    "stage_status": status,
                    "timestamp": msg_ts,
                })
                all_files.append({
                    "path": entry["path"],
                    "filename": Path(entry["path"]).name,
                    "status": entry.get("status", "M"),
                    "link": link,
                    "badge": badge,
                    "stage_id": sid,
                    "stage_status": status,
                    "timestamp": msg_ts,
                })

        agent_id = s.get("agent_id")
        system_agent_id = s.get("system_agent_id")

        stage_details.append({
            "stage_id": sid,
            "stage_instance_id": s_inst_id,
            "status": status,
            "status_color": _status_color(status),
            "agent_id": agent_id,
            "system_agent_id": system_agent_id,
            "attempt_count": s.get("attempt_count", 0),
            "loop_counter": s.get("loop_counter", 0),
            "output_message_id": msg_id,
            "child_instance_id": s.get("child_instance_id"),
            "files": files,
        })

    # 去重：同一文件只保留最后一次出现（后出现的 stage 覆盖前面的）
    seen_files: dict[str, dict] = {}
    for f in all_files:
        seen_files[f["path"]] = f
    all_files = list(seen_files.values())

    # 按时间倒序排列文件：越新的越在上面
    all_files.sort(key=lambda f: f.get("timestamp", ""), reverse=True)

    # 阻塞点
    blocked_by = []
    for s in stages:
        status = s.get("status", "PENDING")
        if status in ("AWAITING_CONFIRM", "ERROR", "CONFLICT"):
            blocked_by.append({
                "stage_id": s.get("stage_id"),
                "status": status,
                "output_message_id": s.get("output_message_id"),
            })

    # 推断创建时间（从 instance_id 前缀）
    inst_id = data.get("instance_id", "")
    created_at = ""
    try:
        date_str = inst_id.split("-")[0]
        dt = datetime.strptime(date_str, "%Y%m%d")
        created_at = dt.strftime("%Y-%m-%d")
    except Exception:
        pass

    return {
        "instance_id": instance_id,
        "workflow_id": data.get("workflow_id", ""),
        "version": data.get("version", ""),
        "goal": data.get("goal", ""),
        "status": data.get("status", "ACTIVE"),
        "parent_instance_id": data.get("parent_instance_id"),
        "stages_summary": stages_summary,
        "stage_details": stage_details,
        "all_files": all_files,
        "recent_files": all_files[:5],
        "more_files_count": max(0, len(all_files) - 5),
        "blocked_by": blocked_by,
        "timeline": timeline,
        "created_at": created_at,
        "generated_at": iso_timestamp(),
    }


def _scan_instances() -> list[str]:
    root = find_root()
    instances_dir = root / ".agent" / "instances"
    if not instances_dir.exists():
        return []
    return [d.name for d in instances_dir.iterdir() if d.is_dir()]


def _generate_mermaid_for_instance(instance_data: dict) -> str:
    """为单实例生成染色 Mermaid 流程图。"""
    workflow_id = instance_data.get("workflow_id")
    version = instance_data.get("version", "")
    if not workflow_id:
        return ""

    try:
        wf_dir = find_workflow_dir(workflow_id, version if version else None)
        spec = load_workflow(wf_dir / "WORKFLOW.yaml")
    except Exception:
        return ""

    adj = build_adjacency(spec)
    state_map = {s["stage_id"]: s["status"] for s in instance_data.get("stage_details", [])}

    lines = ["graph TD"]

    # 定义节点样式（带状态类）
    for stage in spec.stages:
        sid = stage.stage_id
        label = stage.name or sid
        status = state_map.get(sid, "PENDING")
        cls = _MMD_STATUS_CLASS.get(status, "pending")

        shape = ("[", "]")
        if stage.target_type.value == "virtual":
            shape = "((", "))"
        elif stage.target_type.value == "workflow":
            shape = ("[[", "]]")

        lines.append(f"    {sid}{shape[0]}{label}{shape[1]}")
        lines.append(f"    class {sid} {cls}")

    # 定义边
    for edge in spec.edges:
        style = "-->"
        if edge.condition.value in ("failure", "loop_exceeded"):
            style = "-.->"
        lines.append(f"    {edge.from_stage} {style} {edge.to_stage}")

    # 样式类定义
    lines.append("    classDef pending fill:#757575,stroke:#555,color:#fff")
    lines.append("    classDef running fill:#ff9800,stroke:#e65100,color:#fff")
    lines.append("    classDef awaiting fill:#e91e63,stroke:#c2185b,color:#fff")
    lines.append("    classDef done fill:#4caf50,stroke:#2e7d32,color:#fff")
    lines.append("    classDef error fill:#f44336,stroke:#c62828,color:#fff")
    lines.append("    classDef conflict fill:#9c27b0,stroke:#6a1b9a,color:#fff")

    return "\n".join(lines)


# ── HTML 模板 ──

_INDEX_HEAD = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="refresh" content="30">
<title>Workflow Dashboard</title>
<style>
:root {
  --bg: #0f0f1a;
  --surface: #1a1a2e;
  --surface-hover: #222240;
  --border: #2a2a45;
  --text: #e0e0e0;
  --text-secondary: #8888aa;
  --accent: #6366f1;
  --done: #4caf50;
  --running: #ff9800;
  --awaiting: #e91e63;
  --error: #f44336;
  --conflict: #9c27b0;
  --pending: #757575;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  background: var(--bg);
  color: var(--text);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  line-height: 1.5;
  padding: 24px;
}
header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 24px;
  padding-bottom: 16px;
  border-bottom: 1px solid var(--border);
}
h1 { font-size: 1.5rem; font-weight: 600; letter-spacing: -0.02em; }
.meta { color: var(--text-secondary); font-size: 0.875rem; }
.stats {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 12px;
  margin-bottom: 24px;
}
.stat-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 16px;
  text-align: center;
}
.stat-card .value { font-size: 1.75rem; font-weight: 700; }
.stat-card .label { font-size: 0.75rem; color: var(--text-secondary); text-transform: uppercase; letter-spacing: 0.05em; margin-top: 4px; }
.instances {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(360px, 1fr));
  gap: 16px;
}
.instance-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 20px;
  transition: background 0.15s, border-color 0.15s;
  color: inherit;
  display: block;
  cursor: pointer;
}
.instance-card:hover {
  background: var(--surface-hover);
  border-color: var(--accent);
}
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 12px;
  text-decoration: none;
  color: inherit;
}
.card-title {
  font-size: 1rem;
  font-weight: 600;
}
.card-title .wf { color: var(--text-secondary); font-size: 0.8rem; font-weight: 400; display: block; margin-top: 2px; }
.badge {
  font-size: 0.65rem;
  font-weight: 700;
  text-transform: uppercase;
  padding: 3px 8px;
  border-radius: 999px;
  letter-spacing: 0.05em;
}
.badge-active { background: rgba(99,102,241,0.15); color: var(--accent); border: 1px solid rgba(99,102,241,0.3); }
.badge-paused { background: rgba(255,152,0,0.15); color: var(--running); border: 1px solid rgba(255,152,0,0.3); }
.badge-completed { background: rgba(76,175,80,0.15); color: var(--done); border: 1px solid rgba(76,175,80,0.3); }
.badge-failed { background: rgba(244,67,54,0.15); color: var(--error); border: 1px solid rgba(244,67,54,0.3); }
.progress { margin-bottom: 12px; }
.progress-bar {
  height: 6px;
  background: rgba(255,255,255,0.06);
  border-radius: 3px;
  overflow: hidden;
}
.progress-fill {
  height: 100%;
  background: linear-gradient(90deg, var(--accent), #8b5cf6);
  border-radius: 3px;
  transition: width 0.3s ease;
}
.progress-text { font-size: 0.75rem; color: var(--text-secondary); margin-top: 6px; }
.stage-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-bottom: 14px;
}
.stage-dot {
  width: 10px;
  height: 10px;
  border-radius: 2px;
}
.file-section { margin-top: 4px; }
.file-section-title {
  font-size: 0.75rem;
  color: var(--text-secondary);
  margin-bottom: 6px;
  display: flex;
  align-items: center;
  gap: 6px;
}
.file-list { display: flex; flex-direction: column; gap: 4px; }
.file-item {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 0.8rem;
  padding: 3px 6px;
  border-radius: 4px;
  background: rgba(255,255,255,0.03);
}
.file-item a { color: var(--text); text-decoration: none; }
.file-item a:hover { text-decoration: underline; color: var(--accent); }
.file-status {
  font-size: 0.65rem;
  font-weight: 700;
  width: 18px;
  height: 18px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 3px;
  flex-shrink: 0;
}
.file-badge {
  font-size: 0.6rem;
  color: var(--text-secondary);
  background: rgba(255,255,255,0.05);
  padding: 1px 5px;
  border-radius: 3px;
  margin-left: auto;
  flex-shrink: 0;
}
.file-more {
  font-size: 0.75rem;
  color: var(--accent);
  cursor: pointer;
  margin-top: 4px;
  user-select: none;
}
.file-more:hover { text-decoration: underline; }
.file-extra { display: none; margin-top: 4px; flex-direction: column; gap: 4px; }
.file-extra.open { display: flex; }
.card-footer {
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px solid var(--border);
  font-size: 0.7rem;
  color: var(--text-secondary);
  display: flex;
  justify-content: space-between;
}
.empty { text-align: center; color: var(--text-secondary); padding: 60px 20px; }
.blocked-list { margin-top: 8px; }
.blocked-item {
  font-size: 0.75rem;
  color: var(--error);
  background: rgba(244,67,54,0.08);
  padding: 4px 8px;
  border-radius: 4px;
  margin-top: 4px;
}
</style>
</head>
<body>
<header>
  <div>
    <h1>Workflow Dashboard</h1>
    <div class="meta">{generated_at}</div>
  </div>
  <div class="meta">{total_instances} instances</div>
</header>

<section class="stats">
  <div class="stat-card">
    <div class="value" style="color:var(--accent)">{stats_active}</div>
    <div class="label">Active</div>
  </div>
  <div class="stat-card">
    <div class="value" style="color:var(--running)">{stats_paused}</div>
    <div class="label">Paused</div>
  </div>
  <div class="stat-card">
    <div class="value" style="color:var(--done)">{stats_completed}</div>
    <div class="label">Completed</div>
  </div>
  <div class="stat-card">
    <div class="value" style="color:var(--error)">{stats_failed}</div>
    <div class="label">Failed</div>
  </div>
</section>

{instances_section}

<script>
function toggleExtra(id) {
  var el = document.getElementById('fe-' + id);
  if (el) el.classList.toggle('open');
}
</script>
</body>
</html>
"""

_INDEX_INSTANCE_CARD = """<div class="instance-card" data-href="instances/{instance_id}.html">
  <a class="card-header" href="instances/{instance_id}.html">
    <div class="card-title">
      {instance_id}
      <span class="wf">{workflow_id}</span>
    </div>
    <span class="badge badge-{status_class}">{status}</span>
  </a>

  <div class="progress">
    <div class="progress-bar"><div class="progress-fill" style="width:{progress_pct}%"></div></div>
    <div class="progress-text">{done}/{total} stages done</div>
  </div>

  <div class="stage-grid">
    {stage_dots}
  </div>

  {file_section}

  {blocked_section}

  <div class="card-footer">
    <span>Created {created_at}</span>
    <span>{goal}</span>
  </div>
</div>
"""



_INSTANCE_HEAD = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="refresh" content="30">
<title>{instance_id} - Workflow Instance</title>
<script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
<style>
:root {
  --bg: #0f0f1a;
  --surface: #1a1a2e;
  --surface-hover: #222240;
  --border: #2a2a45;
  --text: #e0e0e0;
  --text-secondary: #8888aa;
  --accent: #6366f1;
  --done: #4caf50;
  --running: #ff9800;
  --awaiting: #e91e63;
  --error: #f44336;
  --conflict: #9c27b0;
  --pending: #757575;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  background: var(--bg);
  color: var(--text);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  line-height: 1.5;
  padding: 24px;
  max-width: 1200px;
  margin: 0 auto;
}
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
.back { font-size: 0.875rem; margin-bottom: 16px; display: inline-block; }
header {
  margin-bottom: 24px;
  padding-bottom: 16px;
  border-bottom: 1px solid var(--border);
}
h1 { font-size: 1.5rem; font-weight: 600; }
.meta { color: var(--text-secondary); font-size: 0.875rem; margin-top: 4px; }
.progress { margin: 16px 0; }
.progress-bar {
  height: 8px;
  background: rgba(255,255,255,0.06);
  border-radius: 4px;
  overflow: hidden;
}
.progress-fill {
  height: 100%;
  background: linear-gradient(90deg, var(--accent), #8b5cf6);
  border-radius: 4px;
  transition: width 0.3s ease;
}
.stats-row {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-bottom: 24px;
}
.stat-pill {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 6px 14px;
  font-size: 0.8rem;
}
.section {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 20px;
  margin-bottom: 20px;
}
.section h2 {
  font-size: 1rem;
  font-weight: 600;
  margin-bottom: 16px;
  color: var(--text-secondary);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  font-size: 0.8rem;
}
.mermaid {
  background: rgba(255,255,255,0.02);
  border-radius: 8px;
  padding: 16px;
  overflow-x: auto;
}
table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.85rem;
}
th, td {
  text-align: left;
  padding: 10px 12px;
  border-bottom: 1px solid var(--border);
}
th {
  color: var(--text-secondary);
  font-weight: 600;
  font-size: 0.75rem;
  text-transform: uppercase;
  letter-spacing: 0.03em;
}
tr:hover td { background: rgba(255,255,255,0.02); }
.status-dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  margin-right: 6px;
}
.file-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  background: rgba(255,255,255,0.05);
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 0.75rem;
  margin: 2px 4px 2px 0;
}
.file-chip a { color: var(--text); }
.timeline {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.timeline-item {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  font-size: 0.8rem;
  padding: 8px 12px;
  border-radius: 6px;
  background: rgba(255,255,255,0.02);
}
.timeline-time {
  color: var(--text-secondary);
  font-size: 0.75rem;
  white-space: nowrap;
  min-width: 80px;
}
</style>
</head>
<body>
<a class="back" href="../index.html">← Back to Dashboard</a>

<header>
  <h1>{instance_id}</h1>
  <div class="meta">
    {workflow_id}{version_tag} · {status} · {goal}
  </div>
  <div class="progress">
    <div class="progress-bar"><div class="progress-fill" style="width:{progress_pct}%"></div></div>
  </div>
  <div class="stats-row">
    <div class="stat-pill">{total_stages} stages</div>
    <div class="stat-pill" style="color:var(--done)">{done_stages} done</div>
    <div class="stat-pill" style="color:var(--running)">{running_stages} running</div>
    <div class="stat-pill" style="color:var(--awaiting)">{waiting_stages} waiting</div>
    <div class="stat-pill" style="color:var(--error)">{error_stages} error</div>
    <div class="stat-pill" style="color:var(--conflict)">{conflict_stages} conflict</div>
    <div class="stat-pill" style="color:var(--pending)">{pending_stages} pending</div>
  </div>
</header>

{mermaid_section}

<div class="section">
  <h2>Stages</h2>
  <table>
    <thead>
      <tr>
        <th>Stage</th>
        <th>Status</th>
        <th>Attempt</th>
        <th>Agent</th>
        <th>Modified Files</th>
      </tr>
    </thead>
    <tbody>
      {stage_rows}
    </tbody>
  </table>
</div>

{timeline_section}

<script>
mermaid.initialize({ startOnLoad: true, theme: 'dark', securityLevel: 'loose' });
</script>
</body>
</html>
"""


# ── 渲染函数 ──

def _render_index(instances_data: list[dict]) -> str:
    """渲染项目全局首页 HTML。"""
    stats = {"active": 0, "paused": 0, "completed": 0, "failed": 0}
    for inst in instances_data:
        status = inst.get("status", "ACTIVE")
        if status == "ACTIVE":
            stats["active"] += 1
        elif status == "PAUSED":
            stats["paused"] += 1
        elif status == "COMPLETED":
            stats["completed"] += 1
        elif status == "FAILED":
            stats["failed"] += 1

    # 生成实例卡片 HTML
    cards_html = ""
    for inst in instances_data:
        cards_html += _render_instance_card(inst)

    if cards_html:
        instances_section = f'<section class="instances">\n{cards_html}</section>'
    else:
        instances_section = '<div class="empty">No active instances found.</div>'

    html = _INDEX_HEAD
    html = html.replace("{generated_at}", instances_data[0].get("generated_at", iso_timestamp()) if instances_data else iso_timestamp())
    html = html.replace("{total_instances}", str(len(instances_data)))
    html = html.replace("{stats_active}", str(stats["active"]))
    html = html.replace("{stats_paused}", str(stats["paused"]))
    html = html.replace("{stats_completed}", str(stats["completed"]))
    html = html.replace("{stats_failed}", str(stats["failed"]))
    html = html.replace("{instances_section}", instances_section)
    return html


def _render_instance_card(inst: dict) -> str:
    """渲染首页的单个实例卡片 HTML。"""
    total = inst["stages_summary"]["total"]
    done = inst["stages_summary"]["done"]
    progress = int((done / total * 100)) if total > 0 else 0
    status = inst.get("status", "ACTIVE").lower()

    # stage 色块
    stage_dots = ""
    for st in inst.get("stage_details", []):
        color = _status_color(st["status"])
        stage_dots += f'<div class="stage-dot" style="background:{color}" title="{st["stage_id"]}: {st["status"]}"></div>\n'

    # 最近文件
    recent_files_html = ""
    for f in inst.get("recent_files", []):
        recent_files_html += _render_file_item(f, show_stage=False)

    # 更多文件
    extra_files_html = ""
    for f in inst.get("extra_files", []):
        extra_files_html += _render_file_item(f, show_stage=True)

    file_section = ""
    if recent_files_html:
        more_toggle = ""
        if inst.get("more_files_count", 0) > 0:
            more_toggle = (
                f'<div class="file-more" onclick="event.preventDefault();toggleExtra(\'{inst["instance_id"]}\')">'
                f'+ {inst["more_files_count"]} more...</div>\n'
                f'<div class="file-extra" id="fe-{inst["instance_id"]}">\n{extra_files_html}</div>\n'
            )
        file_section = (
            f'<div class="file-section">\n'
            f'  <div class="file-section-title">📁 Recent outputs</div>\n'
            f'  <div class="file-list" id="fl-{inst["instance_id"]}">\n{recent_files_html}</div>\n'
            f'{more_toggle}</div>\n'
        )

    # 阻塞点
    blocked_section = ""
    if inst.get("blocked_by"):
        blocked_html = ""
        for b in inst["blocked_by"]:
            blocked_html += f'<div class="blocked-item">⏸ {b["stage_id"]}: {b["status"]}</div>\n'
        blocked_section = f'<div class="blocked-list">\n{blocked_html}</div>\n'

    goal = inst.get("goal", "")[:40]
    if len(inst.get("goal", "")) > 40:
        goal += "..."

    version = inst.get("version", "")
    wf_line = inst["workflow_id"]
    if version:
        wf_line += f"@{version}"

    return _INDEX_INSTANCE_CARD.format(
        instance_id=inst["instance_id"],
        workflow_id=wf_line,
        status_class=status,
        status=inst["status"],
        progress_pct=progress,
        done=done,
        total=total,
        stage_dots=stage_dots,
        file_section=file_section,
        blocked_section=blocked_section,
        created_at=inst.get("created_at", "—"),
        goal=goal,
    )


def _render_file_item(f: dict, show_stage: bool = False) -> str:
    status_icon = _FILE_STATUS_ICONS.get(f.get("status", "M"), "M")
    status_color = _FILE_STATUS_COLORS.get(f.get("status", "M"), "#ff9800")
    badge = f.get("badge", "main")
    stage_info = f" · {f.get('stage_id', '')} · {badge}" if show_stage else f" · {badge}"
    return (
        f'<div class="file-item">\n'
        f'  <span class="file-status" style="background:{status_color}20;color:{status_color}">{status_icon}</span>\n'
        f'  <a href="{f["link"]}" target="_blank">{f["filename"]}</a>\n'
        f'  <span class="file-badge">{stage_info}</span>\n'
        f'</div>\n'
    )


def _render_instance_page(inst: dict) -> str:
    """渲染单实例详情页 HTML。"""
    total = inst["stages_summary"]["total"]
    done = inst["stages_summary"]["done"]
    progress = int((done / total * 100)) if total > 0 else 0

    # stage 表格行
    stage_rows = ""
    for st in inst.get("stage_details", []):
        files_html = ""
        for f in st.get("files", []):
            status_icon = _FILE_STATUS_ICONS.get(f.get("status", "M"), "M")
            status_color = _FILE_STATUS_COLORS.get(f.get("status", "M"), "#ff9800")
            files_html += (
                f'<span class="file-chip">'
                f'<span style="color:{status_color};font-weight:700;font-size:0.65rem;">{status_icon}</span>'
                f'<a href="{f["link"]}" target="_blank">{f["filename"]}</a>'
                f'</span>'
            )
        if not files_html:
            files_html = '<span style="color:var(--text-secondary)">—</span>'

        stage_rows += (
            f'<tr>\n'
            f'  <td><code>{st["stage_id"]}</code></td>\n'
            f'  <td><span class="status-dot" style="background:{st["status_color"]}"></span>{st["status"]}</td>\n'
            f'  <td>{st.get("attempt_count", 0)}</td>\n'
            f'  <td>{st.get("agent_id") or st.get("system_agent_id") or "—"}</td>\n'
            f'  <td>{files_html}</td>\n'
            f'</tr>\n'
        )

    # timeline
    timeline_section = ""
    if inst.get("timeline"):
        timeline_html = ""
        for ev in inst["timeline"]:
            ts = ev.get("timestamp", "")
            time_only = ts[11:16] if len(ts) >= 16 else ts
            reason = ev.get("reason", "")
            reason_html = f" · {reason}" if reason else ""
            timeline_html += (
                f'<div class="timeline-item">\n'
                f'  <div class="timeline-time">{time_only}</div>\n'
                f'  <div>{ev.get("stage_id", "")}: <strong>{ev.get("event", "")}</strong>{reason_html}</div>\n'
                f'</div>\n'
            )
        timeline_section = (
            f'<div class="section">\n'
            f'  <h2>Timeline</h2>\n'
            f'  <div class="timeline">\n{timeline_html}</div>\n'
            f'</div>\n'
        )

    # mermaid
    mermaid_dag = _generate_mermaid_for_instance(inst)
    mermaid_section = ""
    if mermaid_dag:
        mermaid_section = (
            f'<div class="section">\n'
            f'  <h2>Workflow DAG</h2>\n'
            f'  <div class="mermaid">\n{mermaid_dag}\n</div>\n'
            f'</div>\n'
        )

    version = inst.get("version", "")
    version_tag = f"@{version}" if version else ""

    html = _INSTANCE_HEAD
    html = html.replace("{instance_id}", inst["instance_id"])
    html = html.replace("{workflow_id}", inst["workflow_id"])
    html = html.replace("{version_tag}", version_tag)
    html = html.replace("{status}", inst["status"])
    html = html.replace("{goal}", inst.get("goal", ""))
    html = html.replace("{progress_pct}", str(progress))
    html = html.replace("{total_stages}", str(total))
    html = html.replace("{done_stages}", str(done))
    html = html.replace("{running_stages}", str(inst["stages_summary"]["running"]))
    html = html.replace("{waiting_stages}", str(inst["stages_summary"]["awaiting_confirm"]))
    html = html.replace("{error_stages}", str(inst["stages_summary"]["error"]))
    html = html.replace("{conflict_stages}", str(inst["stages_summary"]["conflict"]))
    html = html.replace("{pending_stages}", str(inst["stages_summary"]["pending"]))
    html = html.replace("{mermaid_section}", mermaid_section)
    html = html.replace("{stage_rows}", stage_rows)
    html = html.replace("{timeline_section}", timeline_section)
    return html


# ── 公共 API ──

def update_dashboards(instance_id: str) -> None:
    """更新指定实例的 dashboard 页面和全局首页。

    由 compat/instance/registry.py 的 save_instance_state() 在状态保存成功后自动调用。
    """
    try:
        root = find_root()
        dashboard_dir = root / ".tmp" / "dashboard"
        instances_dir = dashboard_dir / "instances"
        instances_dir.mkdir(parents=True, exist_ok=True)

        # 生成单实例页
        inst_data = _collect_instance_data(instance_id)
        if inst_data:
            inst_html = _render_instance_page(inst_data)
            (instances_dir / f"{instance_id}.html").write_text(inst_html, encoding="utf-8")

        # 生成全局首页
        all_ids = _scan_instances()
        all_data = []
        for iid in all_ids:
            d = _collect_instance_data(iid)
            if d:
                all_data.append(d)

        # 排序：ACTIVE > PAUSED > 其他
        status_order = {"ACTIVE": 0, "PAUSED": 1, "COMPLETED": 2, "FAILED": 3}
        all_data.sort(key=lambda x: status_order.get(x.get("status", ""), 99))

        index_html = _render_index(all_data)
        (dashboard_dir / "index.html").write_text(index_html, encoding="utf-8")
    except Exception:
        # Dashboard 生成失败绝不阻塞主流程
        import traceback
        import sys
        print(f"[wfctl] WARNING: dashboard update failed: {traceback.format_exc()}", file=sys.stderr)


def generate_project_dashboard(output_path: Path | None = None) -> dict:
    """手动生成项目全局首页（供 CLI 调用）。"""
    root = find_root()
    dashboard_dir = root / ".tmp" / "dashboard"
    if output_path:
        dashboard_dir = output_path.parent
    dashboard_dir.mkdir(parents=True, exist_ok=True)

    all_ids = _scan_instances()
    all_data = []
    for iid in all_ids:
        d = _collect_instance_data(iid)
        if d:
            all_data.append(d)

    status_order = {"ACTIVE": 0, "PAUSED": 1, "COMPLETED": 2, "FAILED": 3}
    all_data.sort(key=lambda x: status_order.get(x.get("status", ""), 99))

    index_html = _render_index(all_data)
    out = output_path or (dashboard_dir / "index.html")
    out.write_text(index_html, encoding="utf-8")
    return {"status": "ok", "output": str(out)}


def generate_instance_dashboard(instance_id: str, output_path: Path | None = None) -> dict:
    """手动生成单实例详情页（供 CLI 调用）。"""
    root = find_root()
    inst_data = _collect_instance_data(instance_id)
    if not inst_data:
        return {"status": "error", "reason": f"Instance not found: {instance_id}"}

    dashboard_dir = root / ".tmp" / "dashboard" / "instances"
    if output_path:
        dashboard_dir = output_path.parent
    dashboard_dir.mkdir(parents=True, exist_ok=True)

    inst_html = _render_instance_page(inst_data)
    out = output_path or (dashboard_dir / f"{instance_id}.html")
    out.write_text(inst_html, encoding="utf-8")
    return {"status": "ok", "output": str(out)}
