"""DAG 引擎：邻接表构建、BFS 就绪计算、下游遍历。"""

from __future__ import annotations

from dataclasses import dataclass

from infrastructure.errors import InputError
from domain.workflow.spec import EdgeCondition, EdgeSpec, StageSpec, StageStatus, WorkflowSpec


@dataclass
class AdjacencyList:
    """邻接表：stage_id → 从该 stage 出发的所有 EdgeSpec"""

    outgoing: dict[str, list[EdgeSpec]]    # key → 出发边
    incoming: dict[str, list[EdgeSpec]]    # key → 到达边（反向索引，加速查上游）
    stages: dict[str, StageSpec]           # stage_id → StageSpec


def build_adjacency(spec: WorkflowSpec) -> AdjacencyList:
    """解析 WorkflowSpec，构建 outgoing + incoming 双索引。"""
    outgoing: dict[str, list[EdgeSpec]] = {}
    incoming: dict[str, list[EdgeSpec]] = {}
    stages: dict[str, StageSpec] = {}

    for stage in spec.stages:
        stages[stage.stage_id] = stage
        if stage.stage_id not in outgoing:
            outgoing[stage.stage_id] = []
        if stage.stage_id not in incoming:
            incoming[stage.stage_id] = []

    for edge in spec.edges:
        if edge.from_stage not in outgoing:
            outgoing[edge.from_stage] = []
        if edge.to_stage not in incoming:
            incoming[edge.to_stage] = []
        outgoing[edge.from_stage].append(edge)
        incoming[edge.to_stage].append(edge)

    return AdjacencyList(outgoing=outgoing, incoming=incoming, stages=stages)


def compute_ready(adj: AdjacencyList, state: "InstanceState") -> list[tuple[str, str]]:
    """计算就绪的 (stage_id, stage_instance_id) 列表。

    使用 InstanceState 原生接口，支持 parallel 拆分后的多实例场景。
    """
    ready: list[tuple[str, str]] = []

    for stage_id in adj.stages:
        for st in state.stages_by_id(stage_id):
            if st.status != StageStatus.PENDING:
                continue
            upstream_edges = adj.incoming.get(stage_id, [])
            if _any_upstream_satisfied(upstream_edges, state, adj):
                ready.append((stage_id, st.stage_instance_id))

    return ready


def _any_upstream_satisfied(
    upstream_edges: list[EdgeSpec],
    state: "InstanceState",
    adj: AdjacencyList,
) -> bool:
    """检查至少一条上游边已满足（OR 语义）。

    每条边用 TransitionPolicy.is_upstream_satisfied() 检查。
    """
    if not upstream_edges:
        return True

    from domain.transition.policy import TransitionPolicy  # lazy import (avoid circular)

    for edge in upstream_edges:
        upstream = state.first_stage_by_id(edge.from_stage)
        if upstream is None:
            continue
        policy = TransitionPolicy.from_adjacency(adj, edge.from_stage)
        if policy.is_upstream_satisfied(upstream, edge):
            return True

    return False


def collect_downstream(
    adj: AdjacencyList,
    stage_id: str,
    exclude_conditions: set[EdgeCondition],
) -> set[str]:
    """BFS 从 stage_id 出发，沿 edges 遍历所有可达 stage，
       排除指定 condition 的边（如 failure、loop_exceeded）。
       返回受影响 stage_id 集合。"""
    visited: set[str] = set()
    queue: list[str] = [stage_id]

    while queue:
        current = queue.pop(0)
        if current in visited:
            continue
        visited.add(current)
        for edge in adj.outgoing.get(current, []):
            if edge.condition in exclude_conditions:
                continue
            if edge.cascade_reset_until is not None:
                continue
            if edge.to_stage not in visited:
                queue.append(edge.to_stage)

    # 排除自身
    visited.discard(stage_id)
    return visited


def collect_ancestors(
    adj: AdjacencyList,
    stage_id: str,
    exclude_conditions: set[EdgeCondition] | None = None,
) -> set[str]:
    """反向 BFS：从 stage_id 出发沿 incoming edges 收集拓扑前驱。

    排除指定 condition 的边（默认排除 failure / rejected / loop_exceeded，
    因为这些边不被视为「正常到达路径」）。
    返回祖先 stage_id 集合（不含自身）。
    """
    if exclude_conditions is None:
        exclude_conditions = {
            EdgeCondition.FAILURE,
            EdgeCondition.LOOP_EXCEEDED,
        }

    visited: set[str] = set()
    queue: list[str] = [stage_id]

    while queue:
        current = queue.pop(0)
        if current in visited:
            continue
        visited.add(current)
        for edge in adj.incoming.get(current, []):
            if edge.condition in exclude_conditions:
                continue
            if edge.cascade_reset_until is not None:
                continue
            if edge.from_stage not in visited:
                queue.append(edge.from_stage)

    visited.discard(stage_id)
    return visited


def get_failure_edge(adj: AdjacencyList, stage_id: str) -> EdgeSpec | None:
    """获取指定 stage 的 failure edge。"""
    for edge in adj.outgoing.get(stage_id, []):
        if edge.condition == EdgeCondition.FAILURE:
            return edge
    return None


def get_loop_exceeded_edge(adj: AdjacencyList, stage_id: str) -> EdgeSpec | None:
    """获取指定 stage 的 loop_exceeded edge。"""
    for edge in adj.outgoing.get(stage_id, []):
        if edge.condition == EdgeCondition.LOOP_EXCEEDED:
            return edge
    return None


def get_self_loop_max_loop(adj: AdjacencyList, stage_id: str) -> int | None:
    """获取自环边上的 max_loop，无自环边则返回 None。"""
    for edge in adj.outgoing.get(stage_id, []):
        if edge.from_stage == stage_id and edge.to_stage == stage_id:
            return edge.max_loop
    return None


def is_backward_edge(stage_order: list[str], from_stage: str, to_stage: str) -> bool:
    """检测边是否回指拓扑序中更前的 stage（用于 cascade reset 判断）。"""
    try:
        return stage_order.index(to_stage) < stage_order.index(from_stage)
    except ValueError:
        return False
