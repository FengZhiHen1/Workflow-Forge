"""status 命令的聚合视图构建。"""

from pathlib import Path

from core.dag import build_adjacency
from core.project import find_root
from core.schema.loader import load_workflow


def build_project_status() -> dict:
    """扫描所有 instance.json，返回项目级聚合摘要。"""
    root = find_root()
    instances_dir = root / ".agent" / "instances"
    if not instances_dir.exists():
        return {"active_instances": [], "paused_instances": [], "recent_completed": [], "recent_failed": []}

    active_instances: list[dict] = []
    paused_instances: list[dict] = []
    completed: list[tuple[str, float]] = []
    failed: list[tuple[str, float]] = []

    for inst_dir in sorted(instances_dir.iterdir()):
        if not inst_dir.is_dir():
            continue
        json_file = inst_dir / "instance.json"
        if not json_file.exists():
            continue
        try:
            import json
            data = json.loads(json_file.read_text(encoding="utf-8"))
        except Exception:
            continue

        status = data.get("status", "ACTIVE")
        stages = data.get("stages", [])
        total = len(stages)
        done = sum(1 for s in stages if s.get("status") == "DONE")

        if status == "PAUSED":
            paused_instances.append({
                "instance_id": data.get("instance_id"),
                "workflow_id": data.get("workflow_id"),
                "status": status,
                "stages_done": done,
                "stages_total": total,
            })
        elif status == "ACTIVE":
            blocked_by = []
            for s in stages:
                s_status = s.get("status", "PENDING")
                if s_status in ("AWAITING_CONFIRM", "ERROR", "CONFLICT"):
                    blocked_by.append({
                        "stage_id": s.get("stage_id"),
                        "status": s_status,
                        "output_message_id": s.get("output_message_id"),
                    })
                elif s_status == "PENDING":
                    # 仅依赖未满足的 PENDING 才视为阻塞
                    waiting = _compute_waiting_for(data, s.get("stage_id"))
                    if waiting:
                        blocked_by.append({
                            "stage_id": s.get("stage_id"),
                            "status": s_status,
                            "output_message_id": s.get("output_message_id"),
                        })

            active_instances.append({
                "instance_id": data.get("instance_id"),
                "workflow_id": data.get("workflow_id"),
                "status": status,
                "stages_done": done,
                "stages_total": total,
                "blocked_by": blocked_by,
            })
        elif status == "COMPLETED":
            ts = _get_final_timestamp(data)
            completed.append((data.get("instance_id"), ts))
        elif status == "FAILED":
            ts = _get_final_timestamp(data)
            failed.append((data.get("instance_id"), ts))

    completed.sort(key=lambda x: x[1], reverse=True)
    failed.sort(key=lambda x: x[1], reverse=True)

    return {
        "active_instances": active_instances,
        "paused_instances": paused_instances,
        "recent_completed": [x[0] for x in completed[:5]],
        "recent_failed": [x[0] for x in failed[:5]],
    }


def build_instance_status(instance_id: str) -> dict:
    """返回指定实例摘要。"""
    root = find_root()
    inst_dir = root / ".agent" / "instances" / instance_id
    json_file = inst_dir / "instance.json"
    if not json_file.exists():
        return {"status": "error", "reason": f"Instance not found: {instance_id}"}

    import json
    data = json.loads(json_file.read_text(encoding="utf-8"))

    stages = data.get("stages", [])
    summary = {
        "total": len(stages),
        "pending": sum(1 for s in stages if s.get("status") == "PENDING"),
        "running": sum(1 for s in stages if s.get("status") == "RUNNING"),
        "awaiting_confirm": sum(1 for s in stages if s.get("status") == "AWAITING_CONFIRM"),
        "done": sum(1 for s in stages if s.get("status") == "DONE"),
        "error": sum(1 for s in stages if s.get("status") == "ERROR"),
        "conflict": sum(1 for s in stages if s.get("status") == "CONFLICT"),
    }

    active_worktrees: list[str] = []
    conflict_worktrees: list[str] = []
    stage_details: list[dict] = []

    for s in stages:
        status = s.get("status")
        if status == "DONE":
            continue
        detail: dict = {
            "stage_id": s.get("stage_id"),
            "status": status,
        }
        if s.get("output_message_id"):
            detail["output_message_id"] = s["output_message_id"]
        if status == "PENDING":
            detail["waiting_for"] = _compute_waiting_for(data, s.get("stage_id"))
        if status == "AWAITING_CONFIRM":
            detail["confirm_questions"] = s.get("confirm_questions", [])
        if status == "ERROR":
            detail["attempt_count"] = s.get("attempt_count", 0)
        if status == "RUNNING" and s.get("child_instance_id"):
            child = _get_child_summary(s["child_instance_id"], inst_dir)
            if child:
                detail["child_instance"] = child
        stage_details.append(detail)

    # 扫描 worktree
    wt_dir = root / ".tmp" / "worktrees"
    if wt_dir.exists():
        for d in wt_dir.iterdir():
            if d.is_dir() and instance_id in d.name:
                if "stage-" in d.name:
                    # 检查是否对应 CONFLICT stage
                    # 简化：通过目录名判断
                    active_worktrees.append(str(d.relative_to(root)))
                else:
                    active_worktrees.append(str(d.relative_to(root)))

    return {
        "instance_id": instance_id,
        "goal": data.get("goal", ""),
        "status": data.get("status", "ACTIVE"),
        "stages_summary": summary,
        "active_worktrees": active_worktrees,
        "conflict_worktrees": conflict_worktrees,
        "stages": stage_details,
    }


def _compute_waiting_for(instance_data: dict, stage_id: str) -> list[str]:
    """计算 PENDING stage 的 waiting_for。

    加载 WORKFLOW.yaml，找出所有指向该 stage 的上游 edge，
    返回其中状态不是 DONE 的上游 stage_id 列表。
    """
    root = find_root()
    workflow_id = instance_data.get("workflow_id")
    if not workflow_id:
        return []

    from services.resolver import find_workflow_dir
    version = instance_data.get("version", "")
    try:
        wf_dir = find_workflow_dir(workflow_id, version if version else None)
    except Exception:
        return []
    yaml_file = wf_dir / "WORKFLOW.yaml"

    try:
        spec = load_workflow(yaml_file)
    except Exception:
        return []

    adj = build_adjacency(spec)
    upstream_edges = adj.incoming.get(stage_id, [])
    if not upstream_edges:
        return []

    stage_map = {s["stage_id"]: s for s in instance_data.get("stages", [])}
    waiting: list[str] = []
    for edge in upstream_edges:
        upstream = stage_map.get(edge.from_stage)
        if upstream and upstream.get("status") != "DONE":
            waiting.append(edge.from_stage)

    return waiting


def _get_child_summary(child_id: str, parent_inst_dir: Path) -> dict | None:
    """读取子实例状态快照。"""
    root = find_root()
    child_dir = root / ".agent" / "instances" / child_id
    json_file = child_dir / "instance.json"
    if not json_file.exists():
        return None
    try:
        import json
        data = json.loads(json_file.read_text(encoding="utf-8"))
        stages = data.get("stages", [])
        blocked = [s for s in stages if s.get("status") in ("AWAITING_CONFIRM", "ERROR", "CONFLICT")]
        return {
            "instance_id": child_id,
            "status": data.get("status", "ACTIVE"),
            "stages_summary": {
                "done": sum(1 for s in stages if s.get("status") == "DONE"),
                "running": sum(1 for s in stages if s.get("status") == "RUNNING"),
                "awaiting_confirm": sum(1 for s in stages if s.get("status") == "AWAITING_CONFIRM"),
                "pending": sum(1 for s in stages if s.get("status") == "PENDING"),
            },
            "blocked_stages": [
                {
                    "stage_id": s.get("stage_id"),
                    "status": s.get("status"),
                    "output_message_id": s.get("output_message_id"),
                }
                for s in blocked
            ],
        }
    except Exception:
        return None


def _get_final_timestamp(data: dict) -> float:
    """获取实例最终时间戳（简化：用 instance_id 的时间部分）。"""
    inst_id = data.get("instance_id", "")
    # 格式如 20260517-001
    try:
        from datetime import datetime
        date_str = inst_id.split("-")[0]
        dt = datetime.strptime(date_str, "%Y%m%d")
        return dt.timestamp()
    except Exception:
        return 0.0
