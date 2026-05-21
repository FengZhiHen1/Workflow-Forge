#!/usr/bin/env python3
"""
Message 管理器

编排器操作 Message 的**唯一入口**。编排器禁止直接读写 `.agent/messages/` 下的文件，
所有操作必须通过本脚本执行，以确保原子性、审计追踪和权限收敛。

子命令：
    read          读取指定 Message
    scan          扫描 Message 目录（支持按 instance/status 过滤）
    update        原子更新 Message 状态（mark-awaiting / mark-confirmed）
    sync          为 Instance 检查 RUNNING stages 的 Message 状态（供状态同步使用）
    upstream      收集指定 stage 的上游 Message 产出摘要（供 stage_direction 使用）

用法:
    python message_manager.py read --message-id 20260509-003-a7f3
    python message_manager.py scan --instance wf-xxx --status DONE
    python message_manager.py update --message-id 20260509-003-a7f3 --status CONFIRMED --confirm-responses '[true]'
    python message_manager.py sync --instance wf-xxx
    python message_manager.py upstream --instance wf-xxx --stage s2_refactor --max-report-length 500
"""

import argparse
import json
import sys
from pathlib import Path

from _common import (
    atomic_write_json,
    find_message_path,
    instances_dir,
    messages_dir,
    now_iso,
)


# ---------------------------------------------------------------------------
# 子命令: read
# ---------------------------------------------------------------------------

def cmd_read(args):
    msgs_dir = messages_dir()
    msg_path = find_message_path(args.message_id, msgs_dir)
    if not msg_path:
        print(json.dumps({"error": f"Message not found: {args.message_id}"}, ensure_ascii=False, indent=2))
        sys.exit(1)
    try:
        msg = json.loads(msg_path.read_text(encoding="utf-8"))
        print(json.dumps(msg, ensure_ascii=False, indent=2))
    except Exception as e:
        print(json.dumps({"error": f"Failed to read message: {e}"}, ensure_ascii=False, indent=2))
        sys.exit(1)


# ---------------------------------------------------------------------------
# 子命令: scan
# ---------------------------------------------------------------------------

def cmd_scan(args):
    msgs_dir = messages_dir()
    results = []
    if not msgs_dir.exists():
        print(json.dumps({"messages": [], "total": 0}, ensure_ascii=False, indent=2))
        return

    for subdir in msgs_dir.iterdir():
        if not subdir.is_dir():
            continue
        for msg_file in subdir.glob("*.json"):
            try:
                msg = json.loads(msg_file.read_text(encoding="utf-8"))
            except Exception:
                continue

            if args.instance and msg.get("workflow_instance_id") != args.instance:
                continue
            if args.status and msg.get("status") != args.status:
                continue
            if args.agent_id and msg.get("agent_id") != args.agent_id:
                continue

            results.append({
                "message_id": msg.get("message_id"),
                "status": msg.get("status"),
                "workflow_instance_id": msg.get("workflow_instance_id"),
                "agent_id": msg.get("agent_id"),
                "skill_id": msg.get("skill_id"),
                "timestamp": msg.get("timestamp"),
            })

    # 按时间戳排序
    results.sort(key=lambda x: x.get("timestamp", "") or "", reverse=True)
    print(json.dumps({"messages": results, "total": len(results)}, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------------------
# 子命令: update
# ---------------------------------------------------------------------------

def cmd_update(args):
    msgs_dir = messages_dir()
    msg_path = find_message_path(args.message_id, msgs_dir)
    if not msg_path:
        print(json.dumps({"error": f"Message not found: {args.message_id}"}, ensure_ascii=False, indent=2))
        sys.exit(1)

    try:
        msg = json.loads(msg_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(json.dumps({"error": f"Failed to read message: {e}"}, ensure_ascii=False, indent=2))
        sys.exit(1)

    old_status = msg.get("status")
    new_status = args.status

    # 合法的状态流转校验
    valid_transitions = {
        "PENDING_CONFIRM": ["AWAITING_USER", "CONFIRMED"],
        "AWAITING_USER": ["CONFIRMED", "CANCELLED"],
    }

    if old_status not in valid_transitions or new_status not in valid_transitions.get(old_status, []):
        if not args.force:
            print(json.dumps({
                "error": f"Invalid transition: {old_status} -> {new_status}",
                "valid": valid_transitions.get(old_status, []),
            }, ensure_ascii=False, indent=2))
            sys.exit(1)

    now = now_iso()
    msg["status"] = new_status
    meta = msg.setdefault("metadata", {})

    if new_status == "AWAITING_USER":
        meta["awaiting_since"] = now
        edit_history = meta.setdefault("edit_history", [])
        edit_history.append({
            "timestamp": now,
            "editor": "orchestrator",
            "fields_changed": ["status", "metadata.awaiting_since"],
            "reason": "orchestrator_presented_confirm_via_ask_user_question",
        })

    elif new_status == "CONFIRMED":
        if args.confirm_responses is not None:
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

    elif new_status == "CANCELLED":
        edit_history = meta.setdefault("edit_history", [])
        edit_history.append({
            "timestamp": now,
            "editor": "orchestrator",
            "fields_changed": ["status"],
            "reason": "user_cancelled_or_orchestrator_aborted",
        })

    atomic_write_json(msg_path, msg)
    print(json.dumps({
        "success": True,
        "message_id": args.message_id,
        "old_status": old_status,
        "new_status": new_status,
    }, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------------------
# 子命令: sync
# ---------------------------------------------------------------------------

def cmd_sync(args):
    """
    为指定 Instance 检查 RUNNING stages 的 Message 状态。
    返回每个 RUNNING stage 对应的 Message 状态（如果有），供状态同步使用。
    """
    inst_dir = instances_dir()
    msgs_dir = messages_dir()
    inst_path = inst_dir / f"{args.instance}.json"

    if not inst_path.exists():
        print(json.dumps({"error": f"Instance not found: {args.instance}"}, ensure_ascii=False, indent=2))
        sys.exit(1)

    try:
        instance = json.loads(inst_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(json.dumps({"error": f"Failed to read instance: {e}"}, ensure_ascii=False, indent=2))
        sys.exit(1)

    results = []
    for stage in instance.get("stages", []):
        if stage.get("status") != "RUNNING":
            continue

        sid = stage.get("stage_id")
        msg_id = stage.get("output_message_id")
        assigned_agent_id = stage.get("assigned_agent_id", "")
        skill_id = stage.get("skill_id", "")

        entry = {
            "stage_id": sid,
            "output_message_id": msg_id,
            "message_status": None,
            "message_found": False,
        }

        # 1. 通过 output_message_id 查找
        if msg_id:
            msg_path = find_message_path(msg_id, msgs_dir)
            if msg_path:
                try:
                    msg = json.loads(msg_path.read_text(encoding="utf-8"))
                    entry["message_status"] = msg.get("status")
                    entry["message_found"] = True
                except Exception:
                    pass

        # 2. 通过 assigned_agent_id 兜底查找
        if not entry["message_found"] and assigned_agent_id:
            for subdir in msgs_dir.iterdir() if msgs_dir.exists() else []:
                if not subdir.is_dir():
                    continue
                for msg_file in subdir.glob("*.json"):
                    try:
                        msg = json.loads(msg_file.read_text(encoding="utf-8"))
                    except Exception:
                        continue
                    if msg.get("workflow_instance_id") == args.instance and msg.get("agent_id") == assigned_agent_id:
                        entry["message_status"] = msg.get("status")
                        entry["message_found"] = True
                        entry["message_id_from_scan"] = msg.get("message_id")
                        break
                if entry["message_found"]:
                    break

        results.append(entry)

    print(json.dumps({
        "instance_id": args.instance,
        "running_stages_checked": len(results),
        "results": results,
    }, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------------------
# 子命令: upstream
# ---------------------------------------------------------------------------

def cmd_upstream(args):
    """
    收集指定 stage 的上游 DONE stages 的 Message 产出摘要。
    替代 collect_upstream_context.py 的核心逻辑。
    """
    inst_dir = instances_dir()
    msgs_dir = messages_dir()
    inst_path = inst_dir / f"{args.instance}.json"

    if not inst_path.exists():
        print(json.dumps({"error": f"Instance not found: {args.instance}"}, ensure_ascii=False, indent=2))
        sys.exit(1)

    try:
        instance = json.loads(inst_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(json.dumps({"error": f"Failed to read instance: {e}"}, ensure_ascii=False, indent=2))
        sys.exit(1)

    edges = instance.get("edges", [])
    all_stages = instance.get("stages", [])

    prereq_stage_ids = []
    for e in edges:
        if e.get("to") == args.stage and e.get("condition") in ("always", "success"):
            prereq_stage_ids.append(e.get("from"))

    upstream_stages = []
    max_len = args.max_report_length

    for prereq_id in prereq_stage_ids:
        # 收集该 stage_id 的全部实例（支持多实例并发）
        matching = [s for s in all_stages if s.get("stage_id") == prereq_id]
        if not matching:
            upstream_stages.append({"stage_id": prereq_id, "status": "UNKNOWN", "error": "Stage not found"})
            continue

        for prereq_stage in matching:
            siid = prereq_stage.get("stage_instance_id") or prereq_stage.get("stage_id", "unknown")
            entry = {
                "stage_id": prereq_id,
                "stage_instance_id": siid,
                "status": prereq_stage.get("status", "UNKNOWN"),
                "skill_id": prereq_stage.get("skill_id", ""),
                "message_id": prereq_stage.get("output_message_id") or "",
                "report_summary": "",
                "modified_files": [],
                "output_files": [],
            }

            msg_id = prereq_stage.get("output_message_id")
            if msg_id:
                msg_path = find_message_path(msg_id, msgs_dir)
                if msg_path:
                    try:
                        msg = json.loads(msg_path.read_text(encoding="utf-8"))
                        report = msg.get("report", "")
                        if len(report) > max_len:
                            truncated = report[:max_len]
                            for delim in ("\n", "。", ". ", "; ", ";", ", ", ","):
                                idx = truncated.rfind(delim)
                                if idx > max_len * 0.6:
                                    report = truncated[:idx + len(delim)].rstrip() + " ..."
                                    break
                            else:
                                report = truncated.rstrip() + " ..."
                        entry["report_summary"] = report
                        entry["modified_files"] = msg.get("modified_files", []) or []
                        entry["output_files"] = msg.get("output_files", []) or []
                    except Exception:
                        entry["error"] = f"Failed to read message: {msg_id}"
                else:
                    entry["error"] = f"Message file not found: {msg_id}"
            else:
                entry["error"] = "No output_message_id"

            upstream_stages.append(entry)

    print(json.dumps({
        "instance_id": args.instance,
        "stage_id": args.stage,
        "upstream_stages": upstream_stages,
        "total_upstream": len(upstream_stages),
    }, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Message manager — unified entry for orchestrator to operate messages")
    sub = parser.add_subparsers(dest="command", required=True)

    # read
    p_read = sub.add_parser("read", help="Read a specific message")
    p_read.add_argument("--message-id", required=True)

    # scan
    p_scan = sub.add_parser("scan", help="Scan messages directory")
    p_scan.add_argument("--instance", default="")
    p_scan.add_argument("--status", default="")
    p_scan.add_argument("--agent-id", default="")

    # update
    p_update = sub.add_parser("update", help="Atomically update message status")
    p_update.add_argument("--message-id", required=True)
    p_update.add_argument("--status", required=True, choices=["AWAITING_USER", "CONFIRMED", "CANCELLED"])
    p_update.add_argument("--confirm-responses", type=json.loads, default=None, help="JSON array of user responses")
    p_update.add_argument("--force", action="store_true", help="Bypass state transition validation")

    # sync
    p_sync = sub.add_parser("sync", help="Check message status for RUNNING stages of an instance")
    p_sync.add_argument("--instance", required=True)

    # upstream
    p_up = sub.add_parser("upstream", help="Collect upstream message summaries for a stage")
    p_up.add_argument("--instance", required=True)
    p_up.add_argument("--stage", required=True)
    p_up.add_argument("--max-report-length", type=int, default=500)

    args = parser.parse_args()

    if args.command == "read":
        cmd_read(args)
    elif args.command == "scan":
        cmd_scan(args)
    elif args.command == "update":
        cmd_update(args)
    elif args.command == "sync":
        cmd_sync(args)
    elif args.command == "upstream":
        cmd_upstream(args)


if __name__ == "__main__":
    main()
