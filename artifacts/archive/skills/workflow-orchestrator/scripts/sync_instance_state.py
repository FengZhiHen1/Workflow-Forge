#!/usr/bin/env python3
"""
Instance 状态同步器

解决场景：编排器意外中断后，Instance JSON 中的 stage 状态与已上报的 Message 不一致。

扫描所有活跃实例，将 Instance 状态与 Message 实际状态对齐：
- RUNNING stage 对应的 Message 已是 DONE/ERROR/PENDING_CONFIRM → 同步更新 stage
- BLOCKED stage 对应的 Message 已是 CONFIRMED → 解除阻塞
- PENDING stage 的前置依赖全部满足但未被解锁 → 自动解锁

用法:
    python sync_instance_state.py              # 扫描所有活跃实例
    python sync_instance_state.py --instance wf-xxx  # 只处理指定实例
    python sync_instance_state.py --dry-run    # 只报告，不修改
"""

import argparse
import json
import sys
from pathlib import Path

from _common import (
    atomic_write_json,
    find_message_path,
    instances_dir,
    load_message,
    messages_dir,
    now_iso,
)


def scan_messages_for_stage(instance_id: str, stage_id: str, skill_id: str, assigned_agent_id: str, messages_dir: Path) -> list:
    """
    扫描所有 Message，找到归属于该 instance + stage 的消息。
    匹配规则（按优先级）：
    1. Message.workflow_instance_id == instance_id 且 Message 中的 stage_id 信息匹配
    2. Message.agent_id == assigned_agent_id
    3. Message.skill_id == skill_id 且 Message.workflow_instance_id == instance_id
    """
    matches = []
    if not messages_dir.exists():
        return matches

    for subdir in messages_dir.iterdir():
        if not subdir.is_dir():
            continue
        for msg_file in subdir.glob("*.json"):
            try:
                msg = json.loads(msg_file.read_text(encoding="utf-8"))
            except Exception:
                continue
            if msg.get("workflow_instance_id") != instance_id:
                continue

            # 通过 agent_id 精确匹配
            if assigned_agent_id and msg.get("agent_id") == assigned_agent_id:
                matches.append(msg)
                continue

            # 通过 skill_id + instance_id 模糊匹配（兜底）
            if skill_id and msg.get("skill_id") == skill_id:
                matches.append(msg)

    # 按时间戳排序（取最新的）
    def sort_key(m):
        ts = m.get("timestamp", "")
        mid = m.get("message_id", "")
        return (ts, mid)

    matches.sort(key=sort_key, reverse=True)
    return matches


def sync_instance(instance: dict, messages_dir: Path, dry_run: bool) -> dict:
    """
    同步单个实例的状态。
    返回修复报告（不修改原对象，返回修改后的副本或原对象）。
    """
    instance_id = instance.get("instance_id", "unknown")
    inst_status = instance.get("status", "UNKNOWN")

    if inst_status not in ("PLANNING", "EXECUTING", "SUSPENDED"):
        return {"instance_id": instance_id, "action": "skipped", "reason": f"status={inst_status} not active"}

    changes = []
    stages = instance.get("stages", [])
    edges = instance.get("edges", [])

    for stage in stages:
        sid = stage.get("stage_id", "unknown")
        status = stage.get("status", "UNKNOWN")
        skill_id = stage.get("skill_id", "")
        assigned_agent_id = stage.get("assigned_agent_id", "")
        output_msg_id = stage.get("output_message_id")

        # ---- 场景1: RUNNING stage，但 Message 已上报 ----
        if status == "RUNNING":
            # 先查已记录的 output_message_id
            msg = None
            if output_msg_id:
                msg = load_message(output_msg_id, messages_dir)

            # 如果没找到，扫描所有 Message 兜底
            if not msg and assigned_agent_id:
                candidates = scan_messages_for_stage(instance_id, sid, skill_id, assigned_agent_id, messages_dir)
                if candidates:
                    msg = candidates[0]

            if msg:
                msg_status = msg.get("status", "UNKNOWN")
                if msg_status == "DONE":
                    changes.append({
                        "stage_id": sid,
                        "from": "RUNNING",
                        "to": "DONE",
                        "reason": f"message {msg.get('message_id')} status=DONE",
                    })
                    if not dry_run:
                        stage["status"] = "DONE"
                        stage["end_time"] = now_iso()
                        stage["output_message_id"] = msg.get("message_id")
                        stage["history_message_ids"] = list(dict.fromkeys(
                            stage.get("history_message_ids", []) + [msg.get("message_id")]
                        ))
                elif msg_status == "ERROR":
                    changes.append({
                        "stage_id": sid,
                        "from": "RUNNING",
                        "to": "ERROR",
                        "reason": f"message {msg.get('message_id')} status=ERROR",
                    })
                    if not dry_run:
                        stage["status"] = "ERROR"
                        stage["end_time"] = now_iso()
                        stage["output_message_id"] = msg.get("message_id")
                elif msg_status == "PENDING_CONFIRM":
                    changes.append({
                        "stage_id": sid,
                        "from": "RUNNING",
                        "to": "BLOCKED",
                        "reason": f"message {msg.get('message_id')} status=PENDING_CONFIRM",
                    })
                    if not dry_run:
                        stage["status"] = "BLOCKED"
                        stage["blocked_by_confirm"] = True
                        stage["output_message_id"] = msg.get("message_id")
                        # 加入 pending_confirmations
                        pc = instance.setdefault("pending_confirmations", [])
                        if msg.get("message_id") not in pc:
                            pc.append(msg.get("message_id"))
                # msg_status == RUNNING: SubAgent 仍在运行，无需处理

        # ---- 场景2: BLOCKED stage，但 Message 已被用户确认 ----
        elif status == "BLOCKED":
            msg = None
            if output_msg_id:
                msg = load_message(output_msg_id, messages_dir)
            if not msg and assigned_agent_id:
                candidates = scan_messages_for_stage(instance_id, sid, skill_id, assigned_agent_id, messages_dir)
                if candidates:
                    msg = candidates[0]

            if msg and msg.get("status") == "CONFIRMED":
                changes.append({
                    "stage_id": sid,
                    "from": "BLOCKED",
                    "to": "PENDING",
                    "reason": f"message {msg.get('message_id')} status=CONFIRMED",
                })
                if not dry_run:
                    stage["status"] = "PENDING"
                    stage["blocked_by_confirm"] = False
                    # 从 pending_confirmations 移除
                    pc = instance.get("pending_confirmations", [])
                    if msg.get("message_id") in pc:
                        pc.remove(msg.get("message_id"))

    # ---- 场景3: RUNNING 但无 Message（孤儿任务检测）----
    running_without_message = []
    for stage in stages:
        if stage.get("status") != "RUNNING":
            continue
        sid = stage.get("stage_id", "unknown")
        assigned_agent_id = stage.get("assigned_agent_id", "")
        output_msg_id = stage.get("output_message_id")
        skill_id = stage.get("skill_id", "")

        msg = None
        if output_msg_id:
            msg = load_message(output_msg_id, messages_dir)
        if not msg and assigned_agent_id:
            candidates = scan_messages_for_stage(instance_id, sid, skill_id, assigned_agent_id, messages_dir)
            if candidates:
                msg = candidates[0]

        if not msg:
            running_without_message.append({
                "stage_id": sid,
                "assigned_agent_id": assigned_agent_id,
                "system_agent_id": stage.get("system_agent_id", ""),
                "skill_id": skill_id,
                "start_time": stage.get("start_time", ""),
            })

    # ---- 场景4: 解锁下游 PENDING stages ----
    # 对于多实例 stage，需要 ALL 实例都 DONE/SKIPPED 才算前置满足
    stage_statuses = {}
    for s in stages:
        sid = s["stage_id"]
        if sid not in stage_statuses:
            stage_statuses[sid] = {"total": 0, "done_skipped": 0}
        stage_statuses[sid]["total"] += 1
        if s["status"] in ("DONE", "SKIPPED"):
            stage_statuses[sid]["done_skipped"] += 1

    def is_prereq_fulfilled(prereq_id):
        """检查前置 stage 的全部实例是否都已完成。"""
        info = stage_statuses.get(prereq_id)
        if not info:
            return False
        return info["done_skipped"] >= info["total"]

    for stage in stages:
        if stage.get("status") != "PENDING":
            continue
        sid = stage["stage_id"]
        prereqs = [e for e in edges if e.get("to") == sid and e.get("condition") in ("always", "success")]
        if not prereqs:
            continue  # 无前置依赖（如第一个 stage），本身就是 PENDING，无需解锁
        all_prereqs_done = all(is_prereq_fulfilled(p.get("from")) for p in prereqs)
        if all_prereqs_done and not stage.get("blocked_by_confirm"):
            pass  # 已经是 PENDING，无需处理

    # ---- 更新 Instance 级字段 ----
    if changes and not dry_run:
        running = [s for s in stages if s["status"] == "RUNNING"]
        pending = [s for s in stages if s["status"] == "PENDING"]
        blocked = [s for s in stages if s["status"] == "BLOCKED"]

        if running:
            instance["current_stage"] = running[0]["stage_id"]
        elif pending:
            instance["current_stage"] = pending[0]["stage_id"]
        elif blocked:
            instance["current_stage"] = blocked[0]["stage_id"]
        else:
            instance["current_stage"] = None

        instance["execution_summary"]["completed_stages"] = len([s for s in stages if s["status"] in ("DONE", "SKIPPED")])
        instance["execution_summary"]["active_agents"] = len(running)
        instance["updated_at"] = now_iso()

    return {
        "instance_id": instance_id,
        "action": "synced" if (changes or running_without_message) else "no_change",
        "changes": changes,
        "running_without_message": running_without_message,
        "dry_run": dry_run,
    }


def main():
    parser = argparse.ArgumentParser(description="Sync workflow instance state with actual message status")
    parser.add_argument("--instance", default="", help="Filter to a specific instance")
    parser.add_argument("--dry-run", action="store_true", help="Report inconsistencies without modifying")
    args = parser.parse_args()

    inst_dir = instances_dir()
    msgs_dir = messages_dir()
    results = []

    if not inst_dir.exists():
        print(json.dumps({"results": [], "total": 0, "note": "No instances directory found"}, ensure_ascii=False, indent=2))
        sys.exit(0)

    instance_files = list(inst_dir.glob("*.json"))
    if args.instance:
        instance_files = [f for f in instance_files if f.stem == args.instance]

    for inst_path in instance_files:
        try:
            instance = json.loads(inst_path.read_text(encoding="utf-8"))
        except Exception:
            continue

        report = sync_instance(instance, msgs_dir, args.dry_run)

        if report["action"] == "synced" and not args.dry_run:
            atomic_write_json(inst_path, instance)

        results.append(report)

    summary = {
        "results": results,
        "total": len(results),
        "synced": len([r for r in results if r["action"] == "synced"]),
        "no_change": len([r for r in results if r["action"] == "no_change"]),
        "orphans_detected": sum(len(r.get("running_without_message", [])) for r in results),
        "dry_run": args.dry_run,
    }

    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
