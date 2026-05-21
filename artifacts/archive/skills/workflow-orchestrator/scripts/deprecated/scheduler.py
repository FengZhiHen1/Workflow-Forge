#!/usr/bin/env python3
"""
Scheduler

扫描所有活跃工作流实例，分析依赖、循环计数器、并发规则，
返回当前可以立即调度的 stages 列表，以及阻塞/运行中的状态概览。

用法:
    python scheduler.py scan [--instance wf-...]
    python scheduler.py check-loop --instance wf-... --stage s3_test
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


def find_messages_dir() -> Path:
    cwd = Path.cwd()
    candidate = cwd / ".agent" / "messages"
    if candidate.exists():
        return candidate
    for parent in [cwd.parent, cwd.parent.parent]:
        c = parent / ".agent" / "messages"
        if c.exists():
            return c
    return cwd / ".agent" / "messages"


def load_instance(instance_id: str, instances_dir: Path) -> dict:
    path = instances_dir / f"{instance_id}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def load_message(message_id: str, messages_dir: Path) -> dict:
    """按日期分区查找 message 文件。"""
    if not messages_dir.exists():
        return None
    # message_id 格式: YYYYMMDD-序号-后缀
    date_prefix = message_id[:4] + "-" + message_id[4:6] + "-" + message_id[6:8]
    # 规范中是 YYYY-MM-DD/ 目录
    date_dir = messages_dir / date_prefix
    if date_dir.exists():
        path = date_dir / f"{message_id}.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    # 尝试直接在 messages_dir 下查找（兼容不同结构）
    for subdir in messages_dir.iterdir():
        if subdir.is_dir():
            path = subdir / f"{message_id}.json"
            if path.exists():
                return json.loads(path.read_text(encoding="utf-8"))
    return None


def get_stage_dependencies(instance: dict, stage_id: str) -> list:
    """
    根据 edges 计算某个 stage 的所有前置依赖（直接前置）。
    返回应该全部 DONE 后才能执行当前 stage 的 stage_id 列表。
    """
    deps = []
    for edge in instance.get("edges", []):
        if edge.get("to") == stage_id:
            # 只有 condition=always/success 的 edge 才构成前置依赖
            cond = edge.get("condition", "always")
            if cond in ("always", "success"):
                deps.append(edge.get("from"))
    return deps


def get_downstream_stages(instance: dict, stage_id: str) -> list:
    """获取依赖当前 stage 的所有下游 stages。"""
    downstream = []
    for edge in instance.get("edges", []):
        if edge.get("from") == stage_id:
            downstream.append(edge.get("to"))
    return downstream


def check_ready(instance: dict, stage: dict) -> tuple:
    """
    检查 stage 是否就绪可调度。
    返回 (is_ready: bool, reason: str)
    """
    if stage["status"] != "PENDING":
        return False, f"status is {stage['status']}, not PENDING"

    deps = get_stage_dependencies(instance, stage["stage_id"])

    for dep_id in deps:
        dep_stage = None
        for s in instance["stages"]:
            if s["stage_id"] == dep_id:
                dep_stage = s
                break
        if not dep_stage:
            return False, f"dependency stage {dep_id} not found"
        if dep_stage["status"] not in ("DONE", "SKIPPED"):
            return False, f"dependency {dep_id} is {dep_stage['status']}"

    # 检查是否因确认而阻塞（这个 stage 本身 blocked_by_confirm 应该为 false 才能就绪）
    if stage.get("blocked_by_confirm", False):
        return False, "blocked by pending confirmation"

    return True, "ready"


def check_loop_exceeded(instance: dict, stage_id: str) -> tuple:
    """
    检查某个 stage 是否已经达到循环上限。
    返回 (exceeded: bool, next_action: str, edge_info: dict)
    """
    stage = None
    for s in instance["stages"]:
        if s["stage_id"] == stage_id:
            stage = s
            break
    if not stage:
        return True, "stage_not_found", {}

    loop_counter = stage.get("loop_counter", 0)

    # 查找从该 stage 出发的 failure edge
    for edge in instance.get("edges", []):
        if edge.get("from") == stage_id and edge.get("condition") == "failure":
            max_loop = edge.get("max_loop")
            if max_loop is not None and loop_counter >= max_loop:
                # 查找 loop_exceeded edge
                for le_edge in instance.get("edges", []):
                    if le_edge.get("from") == stage_id and le_edge.get("condition") == "loop_exceeded":
                        return True, "loop_exceeded", le_edge
                return True, "loop_exceeded_no_handler", edge
            return False, "within_limit", edge

    return False, "no_loop_edge", {}


def check_resource_conflict(instance: dict, ready_stages: list, current_stage: dict) -> bool:
    """
    检查当前 stage 是否与已就绪的其他 stage 存在资源冲突。
    目前简化实现：检查 concurrency_rules.allowed_parallel_stages。
    """
    rules = instance.get("concurrency_rules", {})
    allowed_groups = rules.get("allowed_parallel_stages", [])

    if not allowed_groups:
        # 没有显式允许并发的配置，默认不允许同一实例内并发
        for rs in ready_stages:
            if rs["instance_id"] == instance["instance_id"]:
                return True
        return False

    # 检查当前 stage 是否与已就绪的 stage 在同一个 allowed_parallel_stages 组中
    current_id = current_stage["stage_id"]
    for group in allowed_groups:
        if current_id in group:
            # 同一组内的 stage 可以并行，不同组之间需要串行
            for rs in ready_stages:
                if rs["instance_id"] != instance["instance_id"]:
                    continue
                if rs["stage_id"] not in group:
                    return True
            return False

    # 不在任何允许并行组中，不能与其他 stage 并行
    for rs in ready_stages:
        if rs["instance_id"] == instance["instance_id"]:
            return True
    return False


def scan_instances(instances_dir: Path, messages_dir: Path, filter_instance: str = None) -> dict:
    """
    扫描所有实例，返回完整调度状态。
    """
    if not instances_dir.exists():
        return {"error": "Instances directory not found", "ready_stages": [], "blocked": [], "running": []}

    ready_stages = []
    blocked_by_confirm = []
    running = []
    errors_pending_retry = []
    completed_this_round = []
    instances_summary = []

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

        if inst_status not in ("PLANNING", "EXECUTING", "SUSPENDED"):
            continue

        inst_summary = {
            "instance_id": inst_id,
            "status": inst_status,
            "current_stage": instance.get("current_stage"),
            "pending_confirmations": len(instance.get("pending_confirmations", [])),
            "running_stages": [],
            "ready_stages": [],
            "blocked_stages": [],
        }

        for stage in instance.get("stages", []):
            stage_id = stage["stage_id"]
            status = stage["status"]

            if status == "RUNNING":
                running.append({
                    "instance_id": inst_id,
                    "stage_id": stage_id,
                    "skill_id": stage.get("skill_id"),
                    "agent_id": stage.get("assigned_agent_id"),
                    "output_message_id": stage.get("output_message_id"),
                })
                inst_summary["running_stages"].append(stage_id)

            elif status == "BLOCKED":
                blocked_by_confirm.append({
                    "instance_id": inst_id,
                    "stage_id": stage_id,
                    "skill_id": stage.get("skill_id"),
                    "message_id": stage.get("output_message_id"),
                })
                inst_summary["blocked_stages"].append(stage_id)

            elif status == "ERROR":
                # 检查是否可以重试
                ref_stages = instance.get("stages", [])
                ref_stage = None
                for rs in ref_stages:
                    if rs.get("stage_id") == stage_id:
                        ref_stage = rs
                        break
                # retry_policy 目前简化处理：默认 max_attempts=1，但实例中 attempt_count 跟踪
                attempt_count = stage.get("attempt_count", 0)
                # 这里假设如果 attempt_count < 3 则允许重试（实际应由 Reference 定义）
                # 为简化，任何 ERROR 状态且未达上限的都放入 errors_pending_retry
                errors_pending_retry.append({
                    "instance_id": inst_id,
                    "stage_id": stage_id,
                    "skill_id": stage.get("skill_id"),
                    "attempt_count": attempt_count,
                    "message_id": stage.get("output_message_id"),
                })

            elif status == "PENDING":
                is_ready, reason = check_ready(instance, stage)
                if is_ready:
                    # 检查资源冲突
                    conflict = check_resource_conflict(instance, ready_stages, stage)
                    if not conflict:
                        entry = {
                            "instance_id": inst_id,
                            "stage_id": stage_id,
                            "skill_id": stage.get("skill_id"),
                            "upstream_files": stage.get("input_message_ids", []),
                            "upstream_message_ids": stage.get("input_message_ids", []),
                            "code_sub_phase": "core",  # 默认，实际应由 workflow 定义或注入
                            "special_instructions": instance.get("special_instructions", ""),
                            "git_anchor_tag": stage.get("git_anchor_tag"),
                        }
                        ready_stages.append(entry)
                        inst_summary["ready_stages"].append(stage_id)

        instances_summary.append(inst_summary)

    # 检查 running stages 是否已有新的 message 完成（用于编排器读取）
    for run_info in running:
        msg_id = run_info.get("output_message_id")
        if msg_id:
            msg = load_message(msg_id, messages_dir)
            if msg:
                run_info["message_status"] = msg.get("status", "UNKNOWN")
                run_info["report_preview"] = msg.get("report", "")[:200] if msg.get("report") else ""
            else:
                run_info["message_status"] = "NOT_FOUND"

    return {
        "ready_stages": ready_stages,
        "blocked_by_confirm": blocked_by_confirm,
        "running": running,
        "errors_pending_retry": errors_pending_retry,
        "instances_summary": instances_summary,
        "max_parallel_hint": max(
            [inst.get("concurrency_rules", {}).get("max_parallel_agents", 4)
             for inst in [json.loads(p.read_text(encoding="utf-8")) for p in instance_files]
             if inst.get("status") in ("PLANNING", "EXECUTING", "SUSPENDED")],
            default=4
        ),
    }


def cmd_scan(args):
    instances_dir = find_instances_dir()
    messages_dir = find_messages_dir()
    result = scan_instances(instances_dir, messages_dir, args.instance)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_check_loop(args):
    instances_dir = find_instances_dir()
    instance = load_instance(args.instance, instances_dir)
    if not instance:
        print(json.dumps({"error": f"Instance not found: {args.instance}"}, ensure_ascii=False, indent=2))
        sys.exit(1)

    exceeded, action, edge_info = check_loop_exceeded(instance, args.stage)
    print(json.dumps({
        "exceeded": exceeded,
        "action": action,
        "edge": edge_info,
        "loop_counter": next((s.get("loop_counter", 0) for s in instance["stages"] if s["stage_id"] == args.stage), 0),
    }, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description="Scheduler")
    sub = parser.add_subparsers(dest="command", required=True)

    p_scan = sub.add_parser("scan", help="Scan all instances and return ready stages")
    p_scan.add_argument("--instance", default="", help="Filter to a specific instance")

    p_loop = sub.add_parser("check-loop", help="Check if a stage has exceeded its loop limit")
    p_loop.add_argument("--instance", required=True)
    p_loop.add_argument("--stage", required=True)

    args = parser.parse_args()

    if args.command == "scan":
        cmd_scan(args)
    elif args.command == "check-loop":
        cmd_check_loop(args)


if __name__ == "__main__":
    main()
