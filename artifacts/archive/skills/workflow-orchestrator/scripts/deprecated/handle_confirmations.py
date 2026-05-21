#!/usr/bin/env python3
"""
Handle Confirmations

编排器专用脚本：扫描、处理和解除工作流确认阻塞。

用法:
    # 扫描所有待确认的 message（按时间戳排序，自动标记 BLOCKED）
    python handle_confirmations.py scan [--instance wf-...]

    # 标记 message 为 AWAITING_USER（调用 AskUserQuestion 后执行）
    python handle_confirmations.py mark-awaiting --message-id 20260509-003-a7f3

    # 标记 message 为 CONFIRMED（用户回复后执行）
    python handle_confirmations.py mark-confirmed --message-id 20260509-003-a7f3 --confirm-responses '[true]'

    # 解除 stage 的 BLOCKED 状态
    python handle_confirmations.py unblock-stage --instance wf-001 --stage s2_refactor
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def find_paths() -> tuple:
    """返回 (instances_dir, messages_dir)"""
    cwd = Path.cwd()
    candidate_agent = cwd / ".agent"
    if candidate_agent.exists():
        return (
            candidate_agent / "workflows" / "instances",
            candidate_agent / "messages",
        )
    for parent in [cwd.parent, cwd.parent.parent]:
        a = parent / ".agent"
        if a.exists():
            return (
                a / "workflows" / "instances",
                a / "messages",
            )
    return (
        cwd / ".agent" / "workflows" / "instances",
        cwd / ".agent" / "messages",
    )


def load_instance(instance_id: str, instances_dir: Path) -> dict:
    path = instances_dir / f"{instance_id}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def find_message_path(message_id: str, messages_dir: Path) -> Path:
    """按日期分区查找 message 文件，返回 Path 或 None。"""
    if not messages_dir.exists():
        return None
    # message_id 格式: YYYYMMDD-序号-后缀
    date_prefix = message_id[:4] + "-" + message_id[4:6] + "-" + message_id[6:8]
    date_dir = messages_dir / date_prefix
    if date_dir.exists():
        path = date_dir / f"{message_id}.json"
        if path.exists():
            return path
    # 尝试所有子目录
    for subdir in messages_dir.iterdir():
        if subdir.is_dir():
            path = subdir / f"{message_id}.json"
            if path.exists():
                return path
    return None


def load_message(message_id: str, messages_dir: Path) -> dict:
    path = find_message_path(message_id, messages_dir)
    if not path:
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_write_json(filepath: Path, data: dict):
    """原子写入 JSON。"""
    filepath.parent.mkdir(parents=True, exist_ok=True)
    tmp = filepath.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(str(tmp), str(filepath))


def update_instance_stage_status(instance_id: str, stage_id: str, new_status: str, instances_dir: Path) -> dict:
    """
    直接原子更新 instance 中指定 stage 的 status。
    用于将 stage 标记为 BLOCKED 或解除 BLOCKED。
    """
    inst_path = instances_dir / f"{instance_id}.json"
    if not inst_path.exists():
        return {"error": f"Instance not found: {instance_id}"}

    instance = json.loads(inst_path.read_text(encoding="utf-8"))
    stage = None
    for s in instance["stages"]:
        if s["stage_id"] == stage_id:
            stage = s
            break

    if not stage:
        return {"error": f"Stage not found: {stage_id}"}

    old_status = stage["status"]
    if old_status == new_status:
        return {"success": True, "instance_id": instance_id, "stage_id": stage_id, "status": new_status, "changed": False}

    stage["status"] = new_status
    stage["blocked_by_confirm"] = (new_status == "BLOCKED")
    instance["updated_at"] = now_iso()

    # 更新 current_stage
    running = [s for s in instance["stages"] if s["status"] == "RUNNING"]
    pending = [s for s in instance["stages"] if s["status"] == "PENDING"]
    blocked = [s for s in instance["stages"] if s["status"] == "BLOCKED"]
    if running:
        instance["current_stage"] = running[0]["stage_id"]
    elif pending:
        instance["current_stage"] = pending[0]["stage_id"]
    elif blocked:
        instance["current_stage"] = blocked[0]["stage_id"]
    else:
        instance["current_stage"] = None

    # 更新 execution_summary
    instance["execution_summary"]["active_agents"] = len(running)

    atomic_write_json(inst_path, instance)
    return {
        "success": True,
        "instance_id": instance_id,
        "stage_id": stage_id,
        "old_status": old_status,
        "new_status": new_status,
        "changed": True,
    }


def cmd_scan(args):
    instances_dir, messages_dir = find_paths()
    if not instances_dir.exists():
        print(json.dumps({"pending": [], "already_awaiting": [], "total": 0}, ensure_ascii=False, indent=2))
        return

    pending = []
    already_awaiting = []
    instance_files = list(instances_dir.glob("*.json"))
    if args.instance:
        instance_files = [f for f in instance_files if f.stem == args.instance]

    for inst_path in instance_files:
        try:
            instance = json.loads(inst_path.read_text(encoding="utf-8"))
        except Exception:
            continue

        inst_id = instance.get("instance_id", inst_path.stem)
        inst_status = instance.get("status", "UNKNOWN")
        if inst_status not in ("PLANNING", "EXECUTING", "SUSPENDED"):
            continue

        for msg_id in instance.get("pending_confirmations", []):
            msg = load_message(msg_id, messages_dir)
            if not msg:
                continue

            msg_status = msg.get("status", "UNKNOWN")

            # 找到对应的 stage
            stage_id = None
            for s in instance.get("stages", []):
                if s.get("output_message_id") == msg_id:
                    stage_id = s["stage_id"]
                    break

            entry = {
                "instance_id": inst_id,
                "stage_id": stage_id,
                "message_id": msg_id,
                "message_status": msg_status,
                "skill_id": msg.get("skill_id", ""),
                "agent_id": msg.get("agent_id", ""),
                "confirm_questions": msg.get("confirm_questions", []),
                "report_preview": msg.get("report", "")[:300] if msg.get("report") else "",
                "timestamp": msg.get("timestamp", ""),
            }

            if msg_status == "PENDING_CONFIRM":
                pending.append(entry)
                # 自动将对应 stage 标记为 BLOCKED（若尚未标记）
                if stage_id:
                    for s in instance.get("stages", []):
                        if s["stage_id"] == stage_id and s["status"] != "BLOCKED":
                            update_instance_stage_status(inst_id, stage_id, "BLOCKED", instances_dir)
                            break
            elif msg_status == "AWAITING_USER":
                already_awaiting.append(entry)

    # 按 message_id 中的时间戳排序（YYYYMMDD-序号-后缀）
    def sort_key(entry):
        mid = entry.get("message_id", "")
        parts = mid.split("-")
        if len(parts) >= 3 and parts[0].isdigit():
            try:
                return (parts[0], int(parts[1]))
            except (ValueError, IndexError):
                return (parts[0], 0)
        return (mid, 0)

    pending.sort(key=sort_key)
    already_awaiting.sort(key=sort_key)

    print(json.dumps({
        "pending": pending,
        "already_awaiting": already_awaiting,
        "total_pending": len(pending),
        "total_awaiting": len(already_awaiting),
    }, ensure_ascii=False, indent=2))


def cmd_mark_awaiting(args):
    _, messages_dir = find_paths()
    msg_path = find_message_path(args.message_id, messages_dir)
    if not msg_path:
        print(json.dumps({"error": f"Message not found: {args.message_id}"}, ensure_ascii=False, indent=2))
        sys.exit(1)

    msg = json.loads(msg_path.read_text(encoding="utf-8"))

    if msg.get("status") != "PENDING_CONFIRM":
        print(json.dumps({
            "error": f"Message status is {msg.get('status')}, expected PENDING_CONFIRM",
            "message_id": args.message_id,
        }, ensure_ascii=False, indent=2))
        sys.exit(1)

    now = now_iso()
    msg["status"] = "AWAITING_USER"
    meta = msg.setdefault("metadata", {})
    meta["awaiting_since"] = now
    edit_history = meta.setdefault("edit_history", [])
    edit_history.append({
        "timestamp": now,
        "editor": "orchestrator",
        "fields_changed": ["status", "metadata.awaiting_since"],
        "reason": "orchestrator_presented_confirm_via_ask_user_question",
    })

    atomic_write_json(msg_path, msg)
    print(json.dumps({
        "success": True,
        "message_id": args.message_id,
        "old_status": "PENDING_CONFIRM",
        "new_status": "AWAITING_USER",
    }, ensure_ascii=False, indent=2))


def cmd_mark_confirmed(args):
    _, messages_dir = find_paths()
    msg_path = find_message_path(args.message_id, messages_dir)
    if not msg_path:
        print(json.dumps({"error": f"Message not found: {args.message_id}"}, ensure_ascii=False, indent=2))
        sys.exit(1)

    msg = json.loads(msg_path.read_text(encoding="utf-8"))

    if msg.get("status") not in ("PENDING_CONFIRM", "AWAITING_USER"):
        print(json.dumps({
            "error": f"Message status is {msg.get('status')}, expected PENDING_CONFIRM or AWAITING_USER",
            "message_id": args.message_id,
        }, ensure_ascii=False, indent=2))
        sys.exit(1)

    now = now_iso()
    msg["status"] = "CONFIRMED"
    meta = msg.setdefault("metadata", {})
    meta["confirm_responses"] = args.confirm_responses
    meta["confirmed_at"] = now
    meta["confirmed_by"] = "user"
    edit_history = meta.setdefault("edit_history", [])
    edit_history.append({
        "timestamp": now,
        "editor": "orchestrator",
        "fields_changed": ["status", "metadata.confirm_responses", "metadata.confirmed_at", "metadata.confirmed_by"],
        "reason": "user_confirmed_via_ask_user_question",
    })

    atomic_write_json(msg_path, msg)
    print(json.dumps({
        "success": True,
        "message_id": args.message_id,
        "old_status": msg.get("status"),
        "new_status": "CONFIRMED",
        "confirm_responses": args.confirm_responses,
    }, ensure_ascii=False, indent=2))


def cmd_unblock_stage(args):
    instances_dir, _ = find_paths()
    result = update_instance_stage_status(args.instance, args.stage, "PENDING", instances_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description="Handle workflow confirmations")
    sub = parser.add_subparsers(dest="command", required=True)

    p_scan = sub.add_parser("scan", help="Scan all pending confirmations")
    p_scan.add_argument("--instance", default="", help="Filter to a specific instance")

    p_await = sub.add_parser("mark-awaiting", help="Mark message as AWAITING_USER")
    p_await.add_argument("--message-id", required=True)

    p_conf = sub.add_parser("mark-confirmed", help="Mark message as CONFIRMED")
    p_conf.add_argument("--message-id", required=True)
    p_conf.add_argument("--confirm-responses", type=json.loads, default=None,
                        help="User confirm responses as JSON array (e.g. '[true, false]')")

    p_unblock = sub.add_parser("unblock-stage", help="Unblock a stage (BLOCKED -> PENDING)")
    p_unblock.add_argument("--instance", required=True)
    p_unblock.add_argument("--stage", required=True)

    args = parser.parse_args()

    if args.command == "scan":
        cmd_scan(args)
    elif args.command == "mark-awaiting":
        cmd_mark_awaiting(args)
    elif args.command == "mark-confirmed":
        cmd_mark_confirmed(args)
    elif args.command == "unblock-stage":
        cmd_unblock_stage(args)


if __name__ == "__main__":
    main()
