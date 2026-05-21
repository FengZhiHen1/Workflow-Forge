#!/usr/bin/env python3
"""
Route Edges

根据 stage 的完成结果，计算 Workflow 应该走哪条 edge。
处理 success/failure 路由、loop_counter 检查和 loop_exceeded 回退。

用法:
    python route_edges.py --instance wf-001 --stage s3_test --outcome success
    python route_edges.py --instance wf-001 --stage s3_test --outcome failure
    python route_edges.py --instance wf-001 --stage s2_refactor --outcome confirmed
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


def find_edges_from(instance: dict, stage_id: str, condition: str) -> list:
    """查找从指定 stage 出发、匹配指定 condition 的所有 edges。"""
    return [e for e in instance.get("edges", [])
            if e.get("from") == stage_id and e.get("condition") == condition]


def get_stage(instance: dict, stage_id: str) -> dict:
    for s in instance.get("stages", []):
        if s["stage_id"] == stage_id:
            return s
    return None


def get_downstream_stages(instance: dict, stage_id: str) -> list:
    """获取当前 stage 的直接下游 stages（edges 中的 to）。"""
    return [e.get("to") for e in instance.get("edges", [])
            if e.get("from") == stage_id and e.get("condition") in ("always", "success")]


def route(instance_id: str, stage_id: str, outcome: str, instances_dir: Path) -> dict:
    instance = load_instance(instance_id, instances_dir)
    if not instance:
        return {"error": f"Instance not found: {instance_id}"}

    stage = get_stage(instance, stage_id)
    if not stage:
        return {"error": f"Stage not found: {stage_id}"}

    result = {
        "instance_id": instance_id,
        "stage_id": stage_id,
        "outcome": outcome,
        "current_loop_counter": stage.get("loop_counter", 0),
    }

    # 1. 尝试匹配 outcome 对应的 edge
    matched_edges = find_edges_from(instance, stage_id, outcome)

    # 2. 如果没有匹配到特定 outcome，尝试 always
    if not matched_edges and outcome in ("success", "done"):
        matched_edges = find_edges_from(instance, stage_id, "always")

    if not matched_edges:
        # 没有匹配的 edge
        if outcome == "failure":
            result["action"] = "error"
            result["error"] = f"No failure edge defined for stage {stage_id}"
            return result
        else:
            # success/always 没有 edge 意味着这是最后一个 stage
            result["action"] = "terminal"
            result["next_stage"] = None
            result["notes"] = "No downstream edge; workflow reaches terminal stage"
            return result

    # 取第一条匹配的 edge（通常只有一条）
    edge = matched_edges[0]
    result["matched_edge"] = edge

    # 3. 处理 failure edge 的 loop 检查
    if outcome == "failure" and edge.get("max_loop") is not None:
        loop_counter = stage.get("loop_counter", 0)
        max_loop = edge["max_loop"]

        if loop_counter >= max_loop:
            # loop 已耗尽，查找 loop_exceeded edge
            loop_exceeded_edges = find_edges_from(instance, stage_id, "loop_exceeded")
            if loop_exceeded_edges:
                fallback = loop_exceeded_edges[0]
                result["fallback_edge"] = fallback
                result["action"] = "loop_exceeded"
                result["next_stage"] = fallback.get("to")
                result["loop_counter_increment"] = False
                result["loop_exceeded"] = True
                result["notes"] = f"max_loop={max_loop} reached, routing to loop_exceeded edge"
            else:
                result["action"] = "error"
                result["error"] = f"max_loop={max_loop} reached but no loop_exceeded edge defined"
            return result
        else:
            # loop 未耗尽，正常回跳
            result["action"] = "loop_back"
            result["next_stage"] = edge.get("to")
            result["loop_counter_increment"] = True
            result["loop_counter_after"] = loop_counter + 1
            result["loop_exceeded"] = False
            result["stages_to_reset"] = [edge.get("to")]
            result["notes"] = f"Loop {loop_counter + 1}/{max_loop}, routing back to {edge.get('to')}"
            return result

    # 4. 处理 confirmed / rejected（confirmation_point 后的 edge）
    if outcome in ("confirmed", "rejected"):
        result["action"] = "proceed"
        result["next_stage"] = edge.get("to")
        result["loop_counter_increment"] = False
        return result

    # 5. 正常流转（success / always）
    result["action"] = "proceed"
    result["next_stage"] = edge.get("to")
    result["loop_counter_increment"] = False
    result["loop_exceeded"] = False

    # 计算因当前 stage 完成而就绪的下游 stages
    downstream = get_downstream_stages(instance, stage_id)
    result["downstream_eligible"] = downstream

    return result


def cmd_route(args):
    instances_dir = find_instances_dir()
    result = route(args.instance, args.stage, args.outcome, instances_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if "error" in result and "action" not in result:
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Route workflow edges")
    parser.add_argument("--instance", required=True)
    parser.add_argument("--stage", required=True)
    parser.add_argument("--outcome", required=True,
                        choices=["success", "failure", "error", "confirmed", "rejected", "done"],
                        help="Stage completion outcome")
    args = parser.parse_args()
    cmd_route(args)


if __name__ == "__main__":
    main()
