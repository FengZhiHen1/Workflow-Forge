"""DAG 引擎：邻接表构建、BFS 就绪计算、下游遍历。"""

from dataclasses import dataclass

from core.errors import InputError
from core.schema.interface import EdgeCondition, EdgeSpec, StageSpec, WorkflowSpec


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


def compute_ready(adj: AdjacencyList, instance: dict) -> list[str]:
    """计算就绪的 stage_id 列表。"""
    ready: list[str] = []
    stage_states = {s["stage_id"]: s for s in instance.get("stages", [])}

    for stage_id, stage_spec in adj.stages.items():
        state = stage_states.get(stage_id, {})
        if state.get("status") != "PENDING":
            continue
        upstream_edges = adj.incoming.get(stage_id, [])
        if _all_satisfied(upstream_edges, stage_states):
            ready.append(stage_id)

    return ready


def _all_satisfied(upstream_edges: list[EdgeSpec], stage_states: dict) -> bool:
    """检查是否至少有一条激活边已满足（OR 语义）。

    多条来自不同上游的激活边代表不同到达路径——任一路径畅通即可解锁。

    每条边仅在 upstream stage DONE 且 exit_condition 匹配时生效：
    - ALWAYS: 接受任何 exit_condition（虚拟起始 stage）
    - SUCCESS: 仅接受 "success"（SubAgent 正常上报 DONE）
    - CONFIRMED: 仅接受 "confirmed"（用户确认）
    - FAILURE / REJECTED / LOOP_EXCEEDED 不计入常规就绪（由专用 handler 触发）
    """
    if not upstream_edges:
        return True

    for edge in upstream_edges:
        upstream_stage = stage_states.get(edge.from_stage, {})
        upstream_status = upstream_stage.get("status", "PENDING")
        if upstream_status != "DONE":
            continue
        exit_cond = upstream_stage.get("exit_condition", "")

        if edge.condition == EdgeCondition.ALWAYS:
            return True
        if edge.condition == EdgeCondition.SUCCESS and exit_cond in ("success", ""):
            if edge.choice:
                routing_choice = upstream_stage.get("routing_choice", "")
                if routing_choice != edge.choice:
                    continue
            # "" 兼容旧实例（升级前已 DONE 的 stage）
            return True
        if edge.condition == EdgeCondition.CONFIRMED and exit_cond in ("confirmed", ""):
            # CONFIRMED 边有 choice 时，必须匹配上游 stage 的 confirmed_choice
            if edge.choice:
                upstream_choice = upstream_stage.get("confirmed_choice", "")
                if upstream_choice and upstream_choice != edge.choice:
                    continue
            # "" 兼容旧实例
            return True
        # failure / rejected / loop_exceeded 跳过，不计入常规就绪

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
            EdgeCondition.REJECTED,
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


def get_confirmed_edges(adj: AdjacencyList, stage_id: str) -> list[EdgeSpec]:
    """获取指定 stage 的所有 confirmed edges。"""
    return [e for e in adj.outgoing.get(stage_id, []) if e.condition == EdgeCondition.CONFIRMED]


def get_rejected_edges(adj: AdjacencyList, stage_id: str) -> list[EdgeSpec]:
    """获取指定 stage 的所有 rejected edges。"""
    return [e for e in adj.outgoing.get(stage_id, []) if e.condition == EdgeCondition.REJECTED]
