"""DAG 拓扑分析引擎（wfctl domain/dag 的 dict 适配版）。

提供 Tarjan SCC + Kahn 拓扑排序 + 环/回溯边检测，纯标准库实现。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AdjacencyList:
    """双向邻接表：stage_id → 出发/到达边列表。"""

    outgoing: dict[str, list[dict]]   # stage_id → 从该 stage 出发的 edge dict 列表
    incoming: dict[str, list[dict]]   # stage_id → 到达该 stage 的 edge dict 列表
    stages: dict[str, dict]           # stage_id → stage dict


@dataclass(frozen=True)
class TopologyResult:
    """拓扑分析结果。

    Fields:
        order: 压缩 SCC 后的拓扑排序（stage_id 列表）
        cycles: 所有检测到的环（每个环是 stage_id 列表）
        back_edges: 回边列表（from 在压缩拓扑序中 >= to 的位置）
    """

    order: list[str] = field(default_factory=list)
    cycles: list[list[str]] = field(default_factory=list)
    back_edges: list[dict] = field(default_factory=list)


def build_adjacency(data: dict) -> AdjacencyList:
    """从 WORKFLOW.yaml raw dict 构建双向邻接表。"""
    stages = data.get("stages", [])
    edges = data.get("edges", [])

    outgoing: dict[str, list[dict]] = {}
    incoming: dict[str, list[dict]] = {}
    stage_map: dict[str, dict] = {}

    for stage in stages:
        if not isinstance(stage, dict):
            continue
        sid = stage.get("stage_id")
        if sid is None:
            continue
        stage_map[sid] = stage
        if sid not in outgoing:
            outgoing[sid] = []
        if sid not in incoming:
            incoming[sid] = []

    for edge in edges:
        if not isinstance(edge, dict):
            continue
        fr = edge.get("from")
        to = edge.get("to")
        if fr is None or to is None:
            continue
        if fr not in outgoing:
            outgoing[fr] = []
        if to not in incoming:
            incoming[to] = []
        outgoing[fr].append(edge)
        incoming[to].append(edge)

    return AdjacencyList(outgoing=outgoing, incoming=incoming, stages=stage_map)


def analyze_topology(adj: AdjacencyList) -> TopologyResult:
    """对工作流图执行 Tarjan SCC 分析。

    算法流程：
    1. DFS 计算 index/lowlink，识别所有 SCC
    2. 压缩 SCC 为 DAG，用 Kahn 算法拓扑排序
    3. 检测环（多节点 SCC 或自环）
    4. 识别回边（同 SCC 内，或 from 在压缩拓扑序中排在 to 之后）
    """
    all_nodes = list(adj.stages.keys())

    # Step 1: Tarjan SCC，捕获 DFS 回溯边
    index_counter = 0
    indices: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    on_stack: dict[str, bool] = {n: False for n in all_nodes}
    stack: list[str] = []
    sccs: list[list[str]] = []
    dfs_back_edges: list[dict] = []

    def _strongconnect(v: str) -> None:
        nonlocal index_counter
        indices[v] = index_counter
        lowlinks[v] = index_counter
        index_counter += 1
        stack.append(v)
        on_stack[v] = True

        for edge in adj.outgoing.get(v, []):
            w = edge.get("to")
            if w is None or w not in adj.stages:
                continue
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

    # Step 2: 构建 SCC 压缩 DAG，Kahn 拓扑排序
    scc_index: dict[str, int] = {}
    for i, scc in enumerate(sccs):
        for node in scc:
            scc_index[node] = i

    scc_count = len(sccs)
    scc_outgoing: list[set[int]] = [set() for _ in range(scc_count)]
    for node in all_nodes:
        from_scc = scc_index[node]
        for edge in adj.outgoing.get(node, []):
            to_node = edge.get("to")
            if to_node not in scc_index:
                continue
            to_scc = scc_index[to_node]
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

    # Step 3: 检测环
    cycles: list[list[str]] = []
    for scc in sccs:
        if len(scc) > 1:
            cycles.append(scc)
        elif len(scc) == 1:
            node = scc[0]
            for edge in adj.outgoing.get(node, []):
                if edge.get("to") == node:
                    cycles.append(scc)
                    break

    # Step 4: 识别回边
    pos: dict[str, int] = {node: idx for idx, node in enumerate(order)}
    back_edges: list[dict] = []
    seen: set[tuple[str | None, str | None]] = set()

    # 4a. DFS 回溯边（自环 + 闭环边）
    for e in dfs_back_edges:
        key = (e.get("from"), e.get("to"), e.get("condition"))
        if key not in seen and None not in (e.get("from"), e.get("to")):
            seen.add(key)
            back_edges.append(e)

    # 4b. 跨 SCC 回边（from_pos > to_pos）
    for node in all_nodes:
        for edge in adj.outgoing.get(node, []):
            to_node = edge.get("to")
            if to_node not in pos or to_node not in scc_index:
                continue
            if scc_index[node] == scc_index[to_node]:
                continue
            from_pos = pos.get(node, -1)
            to_pos = pos.get(to_node, -1)
            if from_pos > to_pos:
                key = (node, to_node, edge.get("condition"))
                if key not in seen:
                    seen.add(key)
                    back_edges.append(edge)

    return TopologyResult(order=order, cycles=cycles, back_edges=back_edges)


def _is_ancestor(adj: AdjacencyList, ancestor: str, descendant: str) -> bool:
    """检查 ancestor 是否为 descendant 的祖先（沿 incoming 边反向 BFS）。"""
    if ancestor == descendant:
        return True
    visited: set[str] = set()
    queue = [descendant]
    while queue:
        current = queue.pop(0)
        if current in visited:
            continue
        visited.add(current)
        if current == ancestor:
            return True
        for edge in adj.incoming.get(current, []):
            fr = edge.get("from")
            if fr and fr not in visited:
                queue.append(fr)
    return False
