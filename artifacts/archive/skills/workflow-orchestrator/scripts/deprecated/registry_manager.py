#!/usr/bin/env python3
"""
Registry Manager

维护 .agent/workflows/registry.json 的增删改查。

用法:
    python registry_manager.py register --instance wf-... --status EXECUTING
    python registry_manager.py update --instance wf-... --status COMPLETED
    python registry_manager.py archive --instance wf-...
    python registry_manager.py list
    python registry_manager.py get --instance wf-...
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def find_registry_path() -> Path:
    cwd = Path.cwd()
    candidate = cwd / ".agent" / "workflows" / "registry.json"
    if candidate.exists() or (cwd / ".agent").exists():
        return candidate

    for parent in [cwd.parent, cwd.parent.parent]:
        c = parent / ".agent" / "workflows" / "registry.json"
        if c.exists() or (parent / ".agent").exists():
            return c

    return cwd / ".agent" / "workflows" / "registry.json"


def load_registry(registry_path: Path) -> dict:
    if registry_path.exists():
        return json.loads(registry_path.read_text(encoding="utf-8"))
    return {
        "schema_version": "2.0.0",
        "last_updated": now_iso(),
        "active_instances": [],
        "suspended_instances": [],
        "completed_today": [],
        "failed_instances": [],
    }


def save_registry(registry_path: Path, data: dict):
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    data["last_updated"] = now_iso()
    tmp = registry_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    import os
    os.replace(str(tmp), str(registry_path))


def find_instance_entry(registry: dict, instance_id: str) -> tuple:
    """在 registry 中查找实例条目，返回 (category, index, entry)"""
    for category in ["active_instances", "suspended_instances", "completed_today", "failed_instances"]:
        for idx, inst in enumerate(registry.get(category, [])):
            if inst.get("instance_id") == instance_id:
                return category, idx, inst
    return None, -1, None


def build_entry(instance_data: dict) -> dict:
    """从 Instance 状态机构建 Registry 条目。"""
    running_count = len([s for s in instance_data.get("stages", []) if s.get("status") == "RUNNING"])
    return {
        "instance_id": instance_data["instance_id"],
        "status": instance_data.get("status", "EXECUTING"),
        "current_stage": instance_data.get("current_stage"),
        "reference": f"{instance_data['reference']['workflow_id']}@{instance_data['reference']['version']}",
        "last_message": instance_data.get("execution_summary", {}).get("last_message_id"),
        "pending_confirmations": len(instance_data.get("pending_confirmations", [])),
        "active_agents": running_count,
        "updated_at": instance_data.get("updated_at", now_iso()),
    }


def cmd_register(args):
    registry_path = find_registry_path()
    registry = load_registry(registry_path)

    # 读取 Instance 文件以获取完整信息
    instances_dir = registry_path.parent / "instances"
    inst_path = instances_dir / f"{args.instance}.json"

    if not inst_path.exists():
        print(json.dumps({"error": f"Instance not found: {args.instance}"}, ensure_ascii=False, indent=2))
        sys.exit(1)

    instance_data = json.loads(inst_path.read_text(encoding="utf-8"))
    entry = build_entry(instance_data)
    if args.status:
        entry["status"] = args.status

    # 从其他分类中移除（如果存在）
    for category in ["active_instances", "suspended_instances", "completed_today", "failed_instances"]:
        registry[category] = [i for i in registry.get(category, []) if i.get("instance_id") != args.instance]

    # 根据状态放入对应分类
    target_category = "active_instances"
    if entry["status"] in ("COMPLETED",):
        target_category = "completed_today"
    elif entry["status"] in ("FAILED",):
        target_category = "failed_instances"
    elif entry["status"] in ("SUSPENDED",):
        target_category = "suspended_instances"

    registry[target_category].append(entry)
    save_registry(registry_path, registry)

    print(json.dumps({
        "success": True,
        "instance_id": args.instance,
        "registered_in": target_category,
        "entry": entry,
    }, ensure_ascii=False, indent=2))


def cmd_update(args):
    registry_path = find_registry_path()
    registry = load_registry(registry_path)

    category, idx, entry = find_instance_entry(registry, args.instance)
    if not category:
        print(json.dumps({"error": f"Instance not registered: {args.instance}"}, ensure_ascii=False, indent=2))
        sys.exit(1)

    if args.status:
        entry["status"] = args.status
    if args.current_stage:
        entry["current_stage"] = args.current_stage
    if args.last_message:
        entry["last_message"] = args.last_message
    if args.pending_confirmations is not None:
        entry["pending_confirmations"] = args.pending_confirmations
    if args.active_agents is not None:
        entry["active_agents"] = args.active_agents

    entry["updated_at"] = now_iso()
    registry[category][idx] = entry

    # 如果状态改变，可能需要移动分类
    new_category = category
    if entry["status"] in ("COMPLETED",) and category != "completed_today":
        new_category = "completed_today"
    elif entry["status"] in ("FAILED",) and category != "failed_instances":
        new_category = "failed_instances"
    elif entry["status"] in ("SUSPENDED",) and category != "suspended_instances":
        new_category = "suspended_instances"
    elif entry["status"] in ("PLANNING", "EXECUTING") and category != "active_instances":
        new_category = "active_instances"

    if new_category != category:
        registry[category].pop(idx)
        registry[new_category].append(entry)

    save_registry(registry_path, registry)

    print(json.dumps({
        "success": True,
        "instance_id": args.instance,
        "category": new_category,
        "entry": entry,
    }, ensure_ascii=False, indent=2))


def cmd_archive(args):
    registry_path = find_registry_path()
    registry = load_registry(registry_path)

    # 从所有分类中移除
    found = False
    for category in ["active_instances", "suspended_instances", "completed_today", "failed_instances"]:
        original_len = len(registry.get(category, []))
        registry[category] = [i for i in registry.get(category, []) if i.get("instance_id") != args.instance]
        if len(registry[category]) < original_len:
            found = True

    if not found:
        print(json.dumps({"error": f"Instance not found in registry: {args.instance}"}, ensure_ascii=False, indent=2))
        sys.exit(1)

    save_registry(registry_path, registry)
    print(json.dumps({
        "success": True,
        "instance_id": args.instance,
        "action": "archived",
    }, ensure_ascii=False, indent=2))


def cmd_list(args):
    registry_path = find_registry_path()
    registry = load_registry(registry_path)

    result = {
        "last_updated": registry.get("last_updated"),
        "counts": {
            "active": len(registry.get("active_instances", [])),
            "suspended": len(registry.get("suspended_instances", [])),
            "completed_today": len(registry.get("completed_today", [])),
            "failed": len(registry.get("failed_instances", [])),
        },
    }

    if args.detailed:
        result["active_instances"] = registry.get("active_instances", [])
        result["suspended_instances"] = registry.get("suspended_instances", [])
    else:
        result["active_instances"] = [{"instance_id": i["instance_id"], "status": i["status"], "current_stage": i.get("current_stage")} for i in registry.get("active_instances", [])]

    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_get(args):
    registry_path = find_registry_path()
    registry = load_registry(registry_path)

    category, idx, entry = find_instance_entry(registry, args.instance)
    if not category:
        print(json.dumps({"error": f"Instance not found: {args.instance}"}, ensure_ascii=False, indent=2))
        sys.exit(1)

    print(json.dumps({
        "category": category,
        "entry": entry,
    }, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description="Registry Manager")
    sub = parser.add_subparsers(dest="command", required=True)

    p_reg = sub.add_parser("register", help="Register or re-register an instance")
    p_reg.add_argument("--instance", required=True)
    p_reg.add_argument("--status", default="")

    p_up = sub.add_parser("update", help="Update instance entry fields")
    p_up.add_argument("--instance", required=True)
    p_up.add_argument("--status", default="")
    p_up.add_argument("--current-stage", default="")
    p_up.add_argument("--last-message", default="")
    p_up.add_argument("--pending-confirmations", type=int, default=None)
    p_up.add_argument("--active-agents", type=int, default=None)

    p_arc = sub.add_parser("archive", help="Remove instance from registry")
    p_arc.add_argument("--instance", required=True)

    p_list = sub.add_parser("list", help="List registry contents")
    p_list.add_argument("--detailed", action="store_true")

    p_get = sub.add_parser("get", help="Get single instance entry")
    p_get.add_argument("--instance", required=True)

    args = parser.parse_args()

    if args.command == "register":
        cmd_register(args)
    elif args.command == "update":
        cmd_update(args)
    elif args.command == "archive":
        cmd_archive(args)
    elif args.command == "list":
        cmd_list(args)
    elif args.command == "get":
        cmd_get(args)


if __name__ == "__main__":
    main()
