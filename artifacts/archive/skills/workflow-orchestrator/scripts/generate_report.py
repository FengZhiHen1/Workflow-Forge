#!/usr/bin/env python3
"""
Generate Report

扫描所有活跃工作流实例，生成本轮调度摘要报告。

用法:
    python generate_report.py
    python generate_report.py --instance wf-001
"""

import argparse
import json
from pathlib import Path


def find_paths() -> tuple:
    cwd = Path.cwd()
    candidate_agent = cwd / ".agent"
    if candidate_agent.exists():
        return (
            candidate_agent / "workflows" / "instances",
            candidate_agent / "messages",
            candidate_agent / "workflows" / "sets",
        )
    for parent in [cwd.parent, cwd.parent.parent]:
        a = parent / ".agent"
        if a.exists():
            return (
                a / "workflows" / "instances",
                a / "messages",
                a / "workflows" / "sets",
            )
    return (
        cwd / ".agent" / "workflows" / "instances",
        cwd / ".agent" / "messages",
        cwd / ".agent" / "workflows" / "sets",
    )


def load_instance(instance_id: str, instances_dir: Path) -> dict:
    path = instances_dir / f"{instance_id}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def find_message_path(message_id: str, messages_dir: Path) -> Path:
    if not messages_dir.exists():
        return None
    date_prefix = message_id[:4] + "-" + message_id[4:6] + "-" + message_id[6:8]
    date_dir = messages_dir / date_prefix
    if date_dir.exists():
        path = date_dir / f"{message_id}.json"
        if path.exists():
            return path
    for subdir in messages_dir.iterdir():
        if subdir.is_dir():
            path = subdir / f"{message_id}.json"
            if path.exists():
                return path
    return None


def format_duration(start: str, end: str) -> str:
    """简化格式化两个 ISO 时间戳之间的时长。
    end 为 None 时，计算从 start 到当前时间的已运行时长（前缀加 ~ 表示进行中）。
    """
    if not start:
        return "N/A"
    try:
        from datetime import datetime, timezone
        # 处理带时区的 ISO 格式
        s = datetime.fromisoformat(start.replace("Z", "+00:00"))
        if end:
            e = datetime.fromisoformat(end.replace("Z", "+00:00"))
        else:
            e = datetime.now(timezone.utc).astimezone()
        delta = e - s
        total_seconds = int(delta.total_seconds())
        prefix = "~" if not end else ""
        if total_seconds < 60:
            return f"{prefix}{total_seconds}s"
        elif total_seconds < 3600:
            return f"{prefix}{total_seconds // 60}m {total_seconds % 60}s"
        else:
            return f"{prefix}{total_seconds // 3600}h {(total_seconds % 3600) // 60}m"
    except Exception:
        return "N/A"


def generate_report(instances_dir: Path, messages_dir: Path, sets_dir: Path, filter_instance: str = None, filter_set: str = None) -> dict:
    if not instances_dir.exists():
        return {"report": "[Workflow Orchestrator] 暂无活跃实例", "structured": {}}

    active_instances = []
    completed_instances = []
    failed_instances = []
    new_started = []
    running_stages = []
    ready_stages = []
    blocked_stages = []
    errors_retry = []

    instance_files = list(instances_dir.glob("*.json"))
    if filter_instance:
        instance_files = [f for f in instance_files if f.stem == filter_instance]

    for inst_path in instance_files:
        try:
            instance = json.loads(inst_path.read_text(encoding="utf-8"))
        except Exception:
            continue

        inst_id = instance.get("instance_id", inst_path.stem)
        inst_status = instance.get("status", "UNKNOWN")
        ref = instance.get("reference", {})
        ref_str = f"{ref.get('workflow_id', '?')}@{ref.get('version', '?')}"

        summary = {
            "instance_id": inst_id,
            "status": inst_status,
            "reference": ref_str,
            "current_stage": instance.get("current_stage"),
            "total_stages": len(instance.get("stages", [])),
            "completed": len([s for s in instance.get("stages", []) if s["status"] in ("DONE", "SKIPPED")]),
            "running": [],
            "pending": [],
            "blocked": [],
            "errors": [],
        }

        for stage in instance.get("stages", []):
            sid = stage["stage_id"]
            siid = stage.get("stage_instance_id") or sid
            status = stage["status"]

            if status == "RUNNING":
                entry = {
                    "instance_id": inst_id,
                    "stage_id": sid,
                    "stage_instance_id": siid,
                    "skill_id": stage.get("skill_id"),
                    "agent_id": stage.get("assigned_agent_id"),
                    "start_time": stage.get("start_time"),
                }
                running_stages.append(entry)
                summary["running"].append(siid)
            elif status == "PENDING":
                ready_stages.append({"instance_id": inst_id, "stage_id": sid, "stage_instance_id": siid})
                summary["pending"].append(siid)
            elif status == "BLOCKED":
                blocked_stages.append({"instance_id": inst_id, "stage_id": sid, "stage_instance_id": siid})
                summary["blocked"].append(siid)
            elif status == "ERROR":
                errors_retry.append({
                    "instance_id": inst_id,
                    "stage_id": sid,
                    "stage_instance_id": siid,
                    "attempt_count": stage.get("attempt_count", 0),
                })
                summary["errors"].append(siid)

        if inst_status in ("PLANNING", "EXECUTING", "SUSPENDED"):
            active_instances.append(summary)
            # 检查是否是本轮新启动的（PLANNING 且第一个 stage 为 RUNNING 或第一个 stage 刚完成）
            if inst_status == "PLANNING":
                new_started.append(summary)
        elif inst_status == "COMPLETED":
            completed_instances.append(summary)
        elif inst_status == "FAILED":
            failed_instances.append(summary)

    # 构建纯文本报告
    lines = ["[Workflow Orchestrator] 本轮调度结果"]
    lines.append("")

    if new_started:
        lines.append(f"- 新启动实例: {len(new_started)} 个")
        for ns in new_started:
            lines.append(f"  • {ns['instance_id']} ({ns['reference']}, stage: {ns['current_stage']})")
    else:
        lines.append("- 新启动实例: 0 个")

    lines.append("")
    lines.append(f"- 正在运行: {len(running_stages)} 个 stages")
    for rs in running_stages:
        duration = format_duration(rs.get("start_time"), None)
        siid = rs.get("stage_instance_id", rs["stage_id"])
        lines.append(f"  • [{rs['instance_id']}] {siid} (skill: {rs.get('skill_id', '?')}, agent: {rs.get('agent_id', '?')})")

    lines.append("")
    lines.append(f"- 已就绪待调度: {len(ready_stages)} 个 stages")
    for rs in ready_stages:
        siid = rs.get("stage_instance_id", rs["stage_id"])
        lines.append(f"  • [{rs['instance_id']}] {siid}")

    lines.append("")
    lines.append(f"- 等待确认: {len(blocked_stages)} 个阻塞点")
    for bs in blocked_stages:
        siid = bs.get("stage_instance_id", bs["stage_id"])
        lines.append(f"  • [{bs['instance_id']}] {siid}")

    lines.append("")
    lines.append(f"- 错误/重试: {len(errors_retry)} 个 stages")
    for er in errors_retry:
        siid = er.get("stage_instance_id", er["stage_id"])
        lines.append(f"  • [{er['instance_id']}] {siid} (attempt: {er['attempt_count']})")

    lines.append("")
    lines.append(f"- 活跃实例: {len(active_instances)} 个")
    lines.append(f"- 已完成实例: {len(completed_instances)} 个")
    lines.append(f"- 失败实例: {len(failed_instances)} 个")

    if instance.get("deviation_log", []):
        lines.append("")
        lines.append(f"- 偏差记录: {len(instance.get('deviation_log', []))} 条")

    report_text = "\n".join(lines)

    structured = {
        "new_started": new_started,
        "running_stages": running_stages,
        "ready_stages": ready_stages,
        "blocked_stages": blocked_stages,
        "errors_retry": errors_retry,
        "active_instances_count": len(active_instances),
        "completed_instances_count": len(completed_instances),
        "failed_instances_count": len(failed_instances),
    }

    # 如果指定了 set_id，附加 Set 级汇总
    if filter_set and sets_dir.exists():
        set_path = sets_dir / f"{filter_set}.json"
        if set_path.exists():
            try:
                sd = json.loads(set_path.read_text(encoding="utf-8"))
                summary = sd.get("execution_summary", {})
                set_lines = ["", f"[Instance Set] {filter_set}"]
                set_lines.append(f"- 工作流: {sd.get('workflow_ref', {}).get('workflow_id', '?')}@{sd.get('workflow_ref', {}).get('version', '?')}")
                set_lines.append(f"- 策略: completion={sd.get('policy', {}).get('completion', 'all')}, confirmation={sd.get('policy', {}).get('confirmation_mode', 'batch')}")
                set_lines.append(f"- 实例总数: {summary.get('total', 0)}")
                set_lines.append(f"- 已完成: {summary.get('completed', 0)}")
                set_lines.append(f"- 运行中: {summary.get('running', 0)}")
                set_lines.append(f"- 等待确认: {summary.get('pending_confirm', 0)}")
                set_lines.append(f"- 失败: {summary.get('failed', 0)}")
                set_lines.append(f"- 已取消: {summary.get('cancelled', 0)}")
                set_lines.append(f"- Set 状态: {sd.get('set_status', 'UNKNOWN')}")
                report_text += "\n" + "\n".join(set_lines)
                structured["set_summary"] = {
                    "set_id": filter_set,
                    "workflow_ref": sd.get("workflow_ref", {}),
                    "policy": sd.get("policy", {}),
                    "execution_summary": summary,
                    "set_status": sd.get("set_status", "UNKNOWN"),
                }
            except Exception:
                pass

    return {
        "report": report_text,
        "structured": structured,
    }


def cmd_report(args):
    instances_dir, messages_dir, sets_dir = find_paths()
    result = generate_report(instances_dir, messages_dir, sets_dir, args.instance or None, args.set_id or None)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description="Generate workflow orchestrator report")
    parser.add_argument("--instance", default="", help="Filter to a specific instance")
    parser.add_argument("--set-id", default="", help="Filter to a specific Instance Set")
    args = parser.parse_args()
    cmd_report(args)


if __name__ == "__main__":
    main()
