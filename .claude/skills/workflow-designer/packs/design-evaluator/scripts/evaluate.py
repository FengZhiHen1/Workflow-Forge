#!/usr/bin/env python3
"""
工作流设计规则检查脚本（L2 质量保障）。

定位：检查客观规则违反，不是"质量评估"。
通过了不代表设计好，没通过一定有问题。

检查项按 mode 区分：
  fast:    3 项（确认点密度、死 Stage、循环出口）
  standard: 4 项（+ 数据流完整性）
  deep:    6 项（+ 并发效率、反模式检测）

调用方式:
    python evaluate_workflow_design.py \
        --workflow-yaml <path/to/WORKFLOW.yaml> \
        [--dependency-graph <path/to/dependency-graph.yaml>] \
        --mode fast|standard|deep

返回 JSON:
    {"pass": true, "checks": [...]}
    {"pass": false, "checks": [...], "issues": [...]}
"""

import argparse
import json
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None


def load_yaml(path: Path) -> dict | None:
    if yaml is None:
        return None
    try:
        text = path.read_text(encoding="utf-8")
        return yaml.safe_load(text)
    except Exception:
        return None


def check_confirmation_density(stages: list, edges: list) -> dict:
    """检查确认点密度是否合理。"""
    business_stages = [
        s for s in stages
        if isinstance(s, dict) and s.get("stage_id") not in ("s00-workflow-start", "s99-workflow-end")
    ]
    total = len(business_stages)
    if total == 0:
        return {"name": "确认点密度", "pass": True, "detail": "无业务 Stage"}

    # 确认点密度已废弃（confirmation_point 字段已移除，确认现在是 Skill 内部行为）
    return {
        "name": "确认点密度",
        "pass": True,
        "detail": "已废弃——确认现在是 Skill 的 AskUserQuestion 内部行为，工作流定义不再声明确认点",
    }


def check_dead_stages(stages: list, edges: list) -> dict:
    """检查是否有死 Stage（无入边或无出边的非虚拟 Stage）。"""
    stage_ids = {s["stage_id"] for s in stages if isinstance(s, dict)}
    virtual = {"s00-workflow-start", "s99-workflow-end"}

    has_in = {sid: False for sid in stage_ids}
    has_out = {sid: False for sid in stage_ids}

    for edge in edges:
        if not isinstance(edge, dict):
            continue
        fr = edge.get("from")
        to = edge.get("to")
        if fr in stage_ids:
            has_out[fr] = True
        if to in stage_ids:
            has_in[to] = True

    issues = []
    for sid in stage_ids:
        if sid in virtual:
            continue
        if not has_in[sid] and not has_out[sid]:
            issues.append(f"Stage '{sid}' 是孤立节点（无任何 edge 连接）")
        elif not has_in[sid]:
            issues.append(f"Stage '{sid}' 无入边（不可达）")
        elif not has_out[sid]:
            issues.append(f"Stage '{sid}' 无出边（死胡同）")

    return {
        "name": "死 Stage 检测",
        "pass": len(issues) == 0,
        "detail": f"检查 {len(stage_ids)} 个 Stage",
        "issues": issues,
    }


def check_loop_exits(stages: list, edges: list) -> dict:
    """检查所有带 max_loop 的 edge 都有对应的 loop_exceeded 出口。"""
    loop_from = set()
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        if edge.get("max_loop"):
            loop_from.add(edge.get("from"))

    has_loop_exceeded = set()
    for edge in edges:
        if isinstance(edge, dict) and edge.get("condition") == "loop_exceeded":
            has_loop_exceeded.add(edge.get("from"))

    issues = []
    for sid in loop_from:
        if sid not in has_loop_exceeded:
            issues.append(f"Stage '{sid}' 有带 max_loop 的 edge，但缺少 loop_exceeded 出口")

    return {
        "name": "循环出口检测",
        "pass": len(issues) == 0,
        "detail": f"{len(loop_from)} 个循环，{len(has_loop_exceeded)} 个有出口",
        "issues": issues,
    }


def check_data_flow(stages: list, edges: list) -> dict:
    """检查数据流完整性：每个业务 Stage 至少有一条入边和一条出边（虚拟 Stage 除外）。"""
    stage_ids = {s["stage_id"] for s in stages if isinstance(s, dict)}
    virtual = {"s00-workflow-start", "s99-workflow-end"}

    in_count = {sid: 0 for sid in stage_ids}
    out_count = {sid: 0 for sid in stage_ids}

    for edge in edges:
        if not isinstance(edge, dict):
            continue
        fr = edge.get("from")
        to = edge.get("to")
        if fr in stage_ids:
            out_count[fr] += 1
        if to in stage_ids:
            in_count[to] += 1

    issues = []
    for sid in stage_ids:
        if sid in virtual:
            # 虚拟 Stage 检查：start 必须有出边，end 必须有入边
            if sid == "s00-workflow-start" and out_count[sid] == 0:
                issues.append("虚拟起始 Stage 's00-workflow-start' 无出边")
            if sid == "s99-workflow-end" and in_count[sid] == 0:
                issues.append("虚拟终止 Stage 's99-workflow-end' 无入边")
            continue
        if in_count[sid] == 0:
            issues.append(f"Stage '{sid}' 无入边（不可达）")
        if out_count[sid] == 0:
            issues.append(f"Stage '{sid}' 无出边（流程终止）")

    return {
        "name": "数据流完整性",
        "pass": len(issues) == 0,
        "detail": f"检查 {len(stage_ids)} 个 Stage 的入边/出边",
        "issues": issues,
    }


def check_concurrency_efficiency(stages: list, edges: list) -> dict:
    """检查并发效率：max_parallel_agents 设置是否合理。"""
    mpa = None
    # 注意：这里不读取顶层 mpa，因为此脚本只检查设计规则，不校验 schema
    # 实际调用时应由主 Agent 传入或从 YAML 读取
    # 简化处理：假设 stages 中不含 mpa，需要外部传入
    # 这里我们只检查 parallel 声明与 exclusive 的冲突

    issues = []
    parallel_count = 0
    for stage in stages:
        if not isinstance(stage, dict):
            continue
        if stage.get("parallel"):
            parallel_count += 1
            if stage.get("exclusive"):
                issues.append(
                    f"Stage '{stage.get('stage_id')}' 同时声明 parallel 和 exclusive，语义冲突"
                )

    return {
        "name": "并发效率",
        "pass": len(issues) == 0,
        "detail": f"{parallel_count} 个并行 Stage",
        "issues": issues,
    }


def check_anti_patterns(stages: list, edges: list, dep_graph: dict | None) -> dict:
    """检查常见反模式。"""
    issues = []

    # 反模式：残留 confirmed/rejected 边条件
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        cond = edge.get("condition")
        if cond in ("confirmed", "rejected"):
            fr = edge.get("from")
            issues.append(
                f"Edge '{fr}' 使用已废弃的 condition={cond}——改为 condition=success + choice"
            )

    # 反模式：残留 confirmation_point 字段
    for stage in stages:
        if not isinstance(stage, dict):
            continue
        if "confirmation_point" in stage:
            sid = stage.get("stage_id", "?")
            issues.append(
                f"Stage '{sid}' 包含已废弃的 confirmation_point 字段——请移除。"
                f"确认现在是 Skill 内部行为（AskUserQuestion）"
            )

    return {
        "name": "反模式检测",
        "pass": len(issues) == 0,
        "detail": "检查已废弃的 confirmed/rejected/confirmation_point 残留",
        "issues": issues,
    }


def evaluate(workflow_yaml: Path, dependency_graph: Path | None, mode: str) -> dict:
    data = load_yaml(workflow_yaml)
    if data is None:
        return {"pass": False, "error": "无法解析 YAML"}

    stages = data.get("stages", [])
    edges = data.get("edges", [])
    dep_data = None
    if dependency_graph and dependency_graph.exists():
        dep_data = load_yaml(dependency_graph)

    check_map = {
        "fast": [check_confirmation_density, check_dead_stages, check_loop_exits],
        "standard": [check_confirmation_density, check_dead_stages, check_loop_exits, check_data_flow],
        "deep": [
            check_confirmation_density, check_dead_stages, check_loop_exits,
            check_data_flow, check_concurrency_efficiency, check_anti_patterns,
        ],
    }

    checks_to_run = check_map.get(mode, check_map["standard"])
    results = []
    all_pass = True

    for check_fn in checks_to_run:
        if check_fn == check_anti_patterns:
            result = check_fn(stages, edges, dep_data)
        else:
            result = check_fn(stages, edges)
        results.append(result)
        if not result["pass"]:
            all_pass = False

    return {
        "pass": all_pass,
        "mode": mode,
        "checks": results,
    }


def main():
    parser = argparse.ArgumentParser(description="工作流设计规则检查（L2）")
    parser.add_argument("--workflow-yaml", required=True, help="WORKFLOW.yaml 路径")
    parser.add_argument("--dependency-graph", help="dependency-graph.yaml 路径（deep 模式）")
    parser.add_argument("--mode", choices=["fast", "standard", "deep"], default="standard",
                        help="检查模式")
    args = parser.parse_args()

    wf_path = Path(args.workflow_yaml).resolve()
    if not wf_path.exists():
        print(json.dumps({"pass": False, "error": f"文件不存在: {wf_path}"},
                         ensure_ascii=False, indent=2))
        sys.exit(1)

    dep_path = Path(args.dependency_graph).resolve() if args.dependency_graph else None
    result = evaluate(wf_path, dep_path, args.mode)

    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result.get("pass") else 1)


if __name__ == "__main__":
    main()
