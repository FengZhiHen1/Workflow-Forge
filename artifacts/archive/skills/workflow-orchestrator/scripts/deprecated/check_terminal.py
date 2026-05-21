#!/usr/bin/env python3
"""
Check Terminal

判断工作流实例是否应进入终态（COMPLETED / FAILED / CANCELLED）。

用法:
    python check_terminal.py --instance wf-001
    python check_terminal.py --scan-all
"""

import argparse
import json
import sys
from pathlib import Path


def find_instances_dir() -> Path:
    cwd = Path.cwd()
    candidate = cwd / ".agent" / "workflows" / "instances"
    if candidate.exists():
        return candidate
    for parent in [cwd.parent, cwd.parent.parent]:
        c = parent / ".agent" / "workflows" / "instances"
        if c.exists():
            return c
    return cwd / ".agent" / "workflows" / "instances"


def load_instance(instance_id: str, instances_dir: Path) -> dict:
    path = instances_dir / f"{instance_id}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def check_terminal(instance: dict) -> dict:
    """
    判断实例是否应该进入终态。
    返回 {should_terminal, new_status, reason}
    """
    inst_status = instance.get("status", "")
    if inst_status in ("COMPLETED", "FAILED", "CANCELLED"):
        return {
            "should_terminal": False,
            "new_status": inst_status,
            "reason": "Instance already in terminal state",
        }

    stages = instance.get("stages", [])
    if not stages:
        return {
            "should_terminal": True,
            "new_status": "FAILED",
            "reason": "No stages defined",
        }

    # 检查是否用户取消
    if inst_status == "CANCELLED":
        return {
            "should_terminal": True,
            "new_status": "CANCELLED",
            "reason": "User cancelled",
        }

    all_done_or_skipped = all(s["status"] in ("DONE", "SKIPPED") for s in stages)
    any_error = any(s["status"] == "ERROR" for s in stages)
    any_failed = any(s["status"] == "FAILED" for s in stages)

    # 所有 stage 都 DONE 或 SKIPPED → COMPLETED
    if all_done_or_skipped:
        return {
            "should_terminal": True,
            "new_status": "COMPLETED",
            "reason": "All stages are DONE or SKIPPED",
            "completed_stages": len([s for s in stages if s["status"] == "DONE"]),
            "skipped_stages": len([s for s in stages if s["status"] == "SKIPPED"]),
        }

    # 存在 ERROR 且无法处理 → FAILED
    if any_error or any_failed:
        # 检查是否存在可以重试的 ERROR
        retryable = []
        unrecoverable = []
        for s in stages:
            if s["status"] == "ERROR":
                attempt = s.get("attempt_count", 0)
                # 默认最大重试 2 次（实际应由 Reference 的 retry_policy 决定）
                if attempt < 2:
                    retryable.append(s["stage_id"])
                else:
                    unrecoverable.append(s["stage_id"])

        if unrecoverable and not retryable:
            return {
                "should_terminal": True,
                "new_status": "FAILED",
                "reason": f"Unrecoverable errors in stages: {unrecoverable}",
                "unrecoverable_stages": unrecoverable,
            }
        elif retryable:
            return {
                "should_terminal": False,
                "new_status": None,
                "reason": f"Retryable errors exist: {retryable}",
            }

    # 存在 loop_exceeded 且无 handler → FAILED
    edges = instance.get("edges", [])
    for s in stages:
        if s["status"] == "ERROR":
            loop_counter = s.get("loop_counter", 0)
            for e in edges:
                if e.get("from") == s["stage_id"] and e.get("condition") == "failure":
                    max_loop = e.get("max_loop")
                    if max_loop is not None and loop_counter >= max_loop:
                        # 检查是否有 loop_exceeded edge
                        loop_exceeded_edges = [ee for ee in edges
                                               if ee.get("from") == s["stage_id"] and ee.get("condition") == "loop_exceeded"]
                        if not loop_exceeded_edges:
                            return {
                                "should_terminal": True,
                                "new_status": "FAILED",
                                "reason": f"Stage {s['stage_id']} exceeded max_loop={max_loop} with no loop_exceeded handler",
                            }

    # 否则继续执行
    return {
        "should_terminal": False,
        "new_status": None,
        "reason": "Workflow still in progress",
        "running_stages": [s["stage_id"] for s in stages if s["status"] == "RUNNING"],
        "pending_stages": [s["stage_id"] for s in stages if s["status"] == "PENDING"],
        "blocked_stages": [s["stage_id"] for s in stages if s["status"] == "BLOCKED"],
    }


def cmd_check(args):
    instances_dir = find_instances_dir()
    instance = load_instance(args.instance, instances_dir)
    if not instance:
        print(json.dumps({"error": f"Instance not found: {args.instance}"}, ensure_ascii=False, indent=2))
        sys.exit(1)

    result = check_terminal(instance)
    result["instance_id"] = args.instance
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_scan_all(args):
    instances_dir = find_instances_dir()
    if not instances_dir.exists():
        print(json.dumps({"instances": [], "total": 0}, ensure_ascii=False, indent=2))
        return

    results = []
    for inst_path in instances_dir.glob("*.json"):
        try:
            instance = json.loads(inst_path.read_text(encoding="utf-8"))
        except Exception:
            continue

        inst_id = instance.get("instance_id", inst_path.stem)
        inst_status = instance.get("status", "")
        if inst_status in ("COMPLETED", "FAILED", "CANCELLED"):
            continue

        check = check_terminal(instance)
        if check["should_terminal"]:
            results.append({
                "instance_id": inst_id,
                "current_status": inst_status,
                "proposed_status": check["new_status"],
                "reason": check["reason"],
            })

    print(json.dumps({
        "instances_reaching_terminal": results,
        "total": len(results),
    }, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description="Check if workflow instance should reach terminal state")
    sub = parser.add_subparsers(dest="command", required=True)

    p_check = sub.add_parser("check", help="Check a single instance")
    p_check.add_argument("--instance", required=True)

    p_scan = sub.add_parser("scan-all", help="Scan all active instances for terminal conditions")

    args = parser.parse_args()

    if args.command == "check":
        cmd_check(args)
    elif args.command == "scan-all":
        cmd_scan_all(args)


if __name__ == "__main__":
    main()
