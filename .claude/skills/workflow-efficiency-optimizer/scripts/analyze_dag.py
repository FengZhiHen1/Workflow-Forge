#!/usr/bin/env python3
"""
DAG 并发分析脚本

解析 WORKFLOW.yaml，构建有向无环图，计算：
  - 关键路径长度（从 start 到 end 的最长路径）
  - 总 stage 数（排除虚拟节点）
  - 理论最大并行度 & 实际并行度
  - 可并行但被串行的 stage 对
  - max_parallel_agents 是否构成瓶颈

用法：
    python analyze_dag.py --workflow-yaml <WORKFLOW.yaml路径> [--output <输出JSON路径>]
"""

import argparse
import json
import sys
from collections import deque
from pathlib import Path

import yaml  # type: ignore


VIRTUAL_STAGES = {"s00-workflow-start", "s99-workflow-end"}


def load_workflow(yaml_path: Path) -> dict:
    with open(yaml_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_dag(stages: list, edges: list) -> tuple:
    """
    构建 DAG，返回 (adjacency, in_degree, stage_map)
    排除虚拟节点 s00 / s99
    """
    stage_ids = {s["stage_id"] for s in stages if s["stage_id"] not in VIRTUAL_STAGES}
    stage_map = {s["stage_id"]: s for s in stages}

    adj = {sid: [] for sid in stage_ids}
    in_deg = {sid: 0 for sid in stage_ids}

    # 排除显式回退/重试边（rejected / loop_exceeded），其余均为有效推进边。
    # failure 在对抗性工作流中可以是正向推进（盲测发现漏洞 → 进入修复），不应排除。
    BACKWARD_CONDITIONS = {"rejected", "loop_exceeded"}

    def _num(stage_id: str) -> int:
        """从 s01-xxx 格式中提取数字编号"""
        import re as _re
        m = _re.match(r"s(\d+)", stage_id)
        return int(m.group(1)) if m else 0

    for e in edges:
        frm, to = e["from"], e["to"]
        cond = e.get("condition", "always")

        # 跳过虚拟节点之间的边
        if frm in VIRTUAL_STAGES and to in VIRTUAL_STAGES:
            continue
        # 跳过自环
        if frm == to:
            continue
        # 跳过显式回退边
        if cond in BACKWARD_CONDITIONS:
            continue
        # 跳过回边（from 编号 >= to 编号）
        if _num(frm) >= _num(to):
            continue

        if frm in VIRTUAL_STAGES:
            if to in in_deg:
                in_deg[to] = max(in_deg[to], 0)
        elif to in VIRTUAL_STAGES:
            pass
        else:
            if frm in adj and to in adj and to not in adj[frm]:
                adj[frm].append(to)
                in_deg[to] = in_deg.get(to, 0) + 1

    # 保证所有 stage 都在 in_deg 中
    for sid in stage_ids:
        if sid not in in_deg:
            in_deg[sid] = 0
        if sid not in adj:
            adj[sid] = []

    return adj, in_deg, stage_map


def topological_sort(adj: dict, in_deg: dict) -> list:
    """Kahn 算法拓扑排序"""
    in_deg_copy = dict(in_deg)
    queue = deque([n for n, d in in_deg_copy.items() if d == 0])
    order = []
    while queue:
        node = queue.popleft()
        order.append(node)
        for neighbor in adj.get(node, []):
            in_deg_copy[neighbor] -= 1
            if in_deg_copy[neighbor] == 0:
                queue.append(neighbor)
    return order


def critical_path(adj: dict, in_deg: dict, stage_map: dict) -> dict:
    """
    计算关键路径（最长路径，按 stage 个数）。
    假设每个 stage 耗时权重为 1（无实际耗时数据时）。
    """
    topo = topological_sort(adj, in_deg)
    dist = {n: 0 for n in adj}
    prev = {n: None for n in adj}

    # 所有入度为 0 的节点：距离 = 1（自身）
    for n in adj:
        if in_deg.get(n, 0) == 0:
            dist[n] = 1

    for u in topo:
        for v in adj.get(u, []):
            if dist[v] < dist[u] + 1:
                dist[v] = dist[u] + 1
                prev[v] = u

    # 找最远的节点
    end_node = max(dist, key=dist.get)
    path = []
    cur = end_node
    while cur:
        path.append(cur)
        cur = prev[cur]
    path.reverse()

    return {
        "length": dist[end_node],
        "path": path,
        "path_stage_names": [stage_map.get(sid, {}).get("name", sid) for sid in path],
    }


def find_parallel_opportunities(adj: dict, in_deg: dict, topo: list) -> list:
    """
    找出每个拓扑层级中可以并行的 stage 组。
    同层级且互不依赖的 stage 即为可并行组。
    """
    # 计算每个 stage 的拓扑层级
    levels: dict = {n: 0 for n in adj}
    for u in topo:
        for v in adj.get(u, []):
            levels[v] = max(levels[v], levels[u] + 1)

    # 按层级分组
    by_level: dict = {}
    for n, lvl in levels.items():
        by_level.setdefault(lvl, []).append(n)

    parallel_groups = []
    for lvl, nodes in sorted(by_level.items()):
        if len(nodes) <= 1:
            continue
        parallel_groups.append({
            "level": lvl,
            "count": len(nodes),
            "stages": nodes,
        })

    return parallel_groups


def main():
    parser = argparse.ArgumentParser(description="分析工作流 DAG 的并发效率")
    parser.add_argument("--workflow-yaml", required=True, help="WORKFLOW.yaml 路径")
    parser.add_argument("--output", help="输出 JSON 文件路径（默认 stdout）")
    args = parser.parse_args()

    yaml_path = Path(args.workflow_yaml)
    if not yaml_path.is_file():
        print(json.dumps({"error": f"File not found: {args.workflow_yaml}"}, ensure_ascii=False, indent=2))
        sys.exit(1)

    wf = load_workflow(yaml_path)
    stages = wf.get("stages", [])
    edges = wf.get("edges", [])
    concurrency = wf.get("concurrency_rules", {})

    adj, in_deg, stage_map = build_dag(stages, edges)
    real_stages = [s for s in stages if s["stage_id"] not in VIRTUAL_STAGES]
    topo = topological_sort(adj, in_deg)
    cp = critical_path(adj, in_deg, stage_map)
    parallel_opps = find_parallel_opportunities(adj, in_deg, topo)

    max_parallel = concurrency.get("max_parallel_agents", 1)
    total_stages = len(real_stages)
    cp_length = cp["length"]
    parallelism_ratio = round(total_stages / cp_length, 2) if cp_length > 0 else 0
    max_possible_parallel = sum(1 for d in in_deg.values() if d == 0)

    conf_points = [s["stage_id"] for s in real_stages if s.get("confirmation_point")]
    non_conf_stages = [s["stage_id"] for s in real_stages if not s.get("confirmation_point")]

    output = {
        "summary": {
            "total_real_stages": total_stages,
            "critical_path_length": cp_length,
            "parallelism_ratio": parallelism_ratio,
            "max_parallel_agents": max_parallel,
            "max_possible_entry_points": max_possible_parallel,
            "confirmation_points": len(conf_points),
            "non_confirmation_stages": len(non_conf_stages),
        },
        "critical_path": cp,
        "parallel_opportunities": parallel_opps,
        "confirmation_point_stages": conf_points,
        "bottleneck_analysis": {
            "max_parallel_low": max_parallel < max_possible_parallel,
            "message": (
                f"max_parallel_agents={max_parallel}，而最大可能入口点为 {max_possible_parallel}，"
                f"存在并行瓶颈" if max_parallel < max_possible_parallel
                else "max_parallel_agents 充足，非瓶颈"
            ),
        },
    }

    out_json = json.dumps(output, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(out_json, encoding="utf-8")
        print(f"[INFO] DAG analysis written to {args.output}")
    else:
        print(out_json)


if __name__ == "__main__":
    main()
