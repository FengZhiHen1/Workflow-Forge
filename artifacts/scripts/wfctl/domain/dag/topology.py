"""Tarjan SCC 拓扑分析引擎。"""

from __future__ import annotations

from dataclasses import dataclass, field

from domain.dag.graph import AdjacencyList
from domain.workflow.spec import EdgeSpec


@dataclass(frozen=True)
class TopologyResult:
    """Workflow 图拓扑分析结果。

    字段:
        order: 压缩 SCC 后的拓扑排序（stage_id 列表）
        cycles: 所有检测到的环（每个环是 stage_id 列表）
        back_edges: 回边列表（from 在压缩拓扑序中 >= to 的位置）
    """

    order: list[str] = field(default_factory=list)
    cycles: list[list[str]] = field(default_factory=list)
    back_edges: list[EdgeSpec] = field(default_factory=list)


def analyze_topology(adj: AdjacencyList) -> TopologyResult:
    """对工作流图执行 Tarjan SCC 分析。

    算法流程:
    1. DFS 计算 index/lowlink，识别所有 SCC
    2. 压缩 SCC 为 DAG，用 Kahn 算法拓扑排序
    3. 检测环（多节点 SCC 或自环）
    4. 识别回边（同 SCC 内，或 from 在压缩拓扑序中排在 to 之后）
    """
    all_nodes = list(adj.stages.keys())

    # Step 1: Tarjan SCC, capture DFS back edges
    index_counter = 0
    indices: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    on_stack: dict[str, bool] = {n: False for n in all_nodes}
    stack: list[str] = []
    sccs: list[list[str]] = []
    dfs_back_edges: list[EdgeSpec] = []

    def _strongconnect(v: str) -> None:
        nonlocal index_counter
        indices[v] = index_counter
        lowlinks[v] = index_counter
        index_counter += 1
        stack.append(v)
        on_stack[v] = True

        for edge in adj.outgoing.get(v, []):
            w = edge.to_stage
            if w not in indices:
                _strongconnect(w)
                lowlinks[v] = min(lowlinks[v], lowlinks[w])
            elif on_stack.get(w, False):
                lowlinks[v] = min(lowlinks[v], indices[w])
                dfs_back_edges.append(edge)

        if lowlinks[v] == indices[v]:
            scc: list[str] = []
            while True:
                w = stack.pop()
                on_stack[w] = False
                scc.append(w)
                if w == v:
                    break
            sccs.append(scc)

    for node in all_nodes:
        if node not in indices:
            _strongconnect(node)

    # Step 2: Build SCC-compressed DAG, Kahn topo-sort
    scc_index: dict[str, int] = {}
    for i, scc in enumerate(sccs):
        for node in scc:
            scc_index[node] = i

    scc_count = len(sccs)
    scc_outgoing: list[set[int]] = [set() for _ in range(scc_count)]
    for node in all_nodes:
        from_scc = scc_index[node]
        for edge in adj.outgoing.get(node, []):
            to_scc = scc_index[edge.to_stage]
            if from_scc != to_scc:
                scc_outgoing[from_scc].add(to_scc)

    in_degree = [0] * scc_count
    for u in range(scc_count):
        for v in scc_outgoing[u]:
            in_degree[v] += 1

    queue: list[int] = [i for i in range(scc_count) if in_degree[i] == 0]
    topo_scc_order: list[int] = []
    while queue:
        u = queue.pop(0)
        topo_scc_order.append(u)
        for v in scc_outgoing[u]:
            in_degree[v] -= 1
            if in_degree[v] == 0:
                queue.append(v)

    # Flatten SCC order
    order: list[str] = []
    for scc_idx in topo_scc_order:
        order.extend(sccs[scc_idx])

    # Step 3: Detect cycles
    cycles: list[list[str]] = []
    for scc in sccs:
        if len(scc) > 1:
            cycles.append(scc)
        elif len(scc) == 1:
            node = scc[0]
            for edge in adj.outgoing.get(node, []):
                if edge.to_stage == node:
                    cycles.append(scc)
                    break

    # Step 4: Identify back edges
    pos: dict[str, int] = {node: idx for idx, node in enumerate(order)}
    back_edges: list[EdgeSpec] = []

    # 4a. DFS back edges captured during Tarjan (self-loops + cycle-closing edges)
    seen: set[tuple[str, str]] = set()
    for e in dfs_back_edges:
        key = (e.from_stage, e.to_stage)
        if key not in seen:
            seen.add(key)
            back_edges.append(e)

    # 4b. Cross-SCC back edges (from_pos > to_pos in compressed DAG)
    for node in all_nodes:
        for edge in adj.outgoing.get(node, []):
            if scc_index[node] == scc_index[edge.to_stage]:
                continue  # same SCC handled by DFS back edges
            from_pos = pos.get(node, -1)
            to_pos = pos.get(edge.to_stage, -1)
            if from_pos > to_pos:
                key = (edge.from_stage, edge.to_stage)
                if key not in seen:
                    seen.add(key)
                    back_edges.append(edge)

    return TopologyResult(order=order, cycles=cycles, back_edges=back_edges)
