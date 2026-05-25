"""TransitionPolicy: 集中化边处理，单一真相源。

将分散在 core/dag.py、ErrorHandlerProcessor、cli/stage/confirm.py
中的边处理逻辑集中于此。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.dag import AdjacencyList
from core.schema.interface import EdgeCondition, EdgeSpec, StageSpec, StageStatus
from services.scheduler.state_model import StageState

from domain.transition.results import TransitionResult


@dataclass(frozen=True)
class TransitionPolicy:
    """单一 stage 的出边策略。

    字段:
        stage_id: 当前 stage 的 ID
        spec: 当前 stage 的配置
        ready_edges: ALWAYS + SUCCESS 边（常规触发）
        confirmed_edges: CONFIRMED 边（用户确认后触发）
        rejected_edges: REJECTED 边（用户拒绝后触发）
        failure_edge: FAILURE 边（出错时触发）
        loop_exceeded_edge: LOOP_EXCEEDED 边（循环超限时触发）
    """

    stage_id: str
    spec: StageSpec
    ready_edges: list[EdgeSpec] = field(default_factory=list)
    confirmed_edges: list[EdgeSpec] = field(default_factory=list)
    rejected_edges: list[EdgeSpec] = field(default_factory=list)
    failure_edge: EdgeSpec | None = None
    loop_exceeded_edge: EdgeSpec | None = None

    @classmethod
    def from_adjacency(cls, adj: AdjacencyList, stage_id: str) -> "TransitionPolicy":
        """从邻接表构建 TransitionPolicy。"""
        spec = adj.stages.get(stage_id)
        if spec is None:
            raise KeyError(f"Stage '{stage_id}' not found in adjacency list")

        ready: list[EdgeSpec] = []
        confirmed: list[EdgeSpec] = []
        rejected: list[EdgeSpec] = []
        failure: EdgeSpec | None = None
        loop_exceeded: EdgeSpec | None = None

        for edge in adj.outgoing.get(stage_id, []):
            cond = edge.condition
            if cond == EdgeCondition.FAILURE:
                failure = edge
            elif cond == EdgeCondition.LOOP_EXCEEDED:
                loop_exceeded = edge
            elif cond == EdgeCondition.CONFIRMED:
                confirmed.append(edge)
            elif cond == EdgeCondition.REJECTED:
                rejected.append(edge)
            else:  # ALWAYS, SUCCESS
                ready.append(edge)

        return cls(
            stage_id=stage_id,
            spec=spec,
            ready_edges=ready,
            confirmed_edges=confirmed,
            rejected_edges=rejected,
            failure_edge=failure,
            loop_exceeded_edge=loop_exceeded,
        )

    def is_upstream_satisfied(self, upstream: StageState, edge: EdgeSpec) -> bool:
        """检查上游 stage 状态是否满足给定边的条件。

        Args:
            upstream: 上游 stage 当前状态
            edge: 待检查的边

        Returns:
            True 如果该边条件已满足（下游 stage 可解锁）
        """
        if upstream.status.value != "DONE":
            return False

        exit_cond = upstream.exit_condition
        cond = edge.condition

        if cond == EdgeCondition.ALWAYS:
            return True

        if cond == EdgeCondition.SUCCESS:
            if exit_cond == "loop_exceeded":
                return False
            if edge.choice and upstream.routing_choice != edge.choice:
                return False
            return True

        if cond == EdgeCondition.CONFIRMED:
            if exit_cond not in ("confirmed", ""):
                return False
            if edge.choice:
                upstream_choice = upstream.confirmed_choice
                if upstream_choice and upstream_choice != edge.choice:
                    return False
            return True

        return False

    def on_error(self, state: StageState) -> TransitionResult:
        """ERROR 状态恢复决策。

        决策优先级：
        1. retry > 0 且 attempt_count 未达上限 → 重试
        2. 存在 loop_exceeded_edge 且 loop_counter 超限 → LOOP_EXCEEDED 路径
        3. 存在 failure_edge → FAILURE 路径
        4. 无恢复路径 → 终止
        """
        retry_max = self.spec.retry
        attempts = state.attempt_count

        if retry_max > 0 and attempts < retry_max:
            return TransitionResult(
                next_status=StageStatus.PENDING,
                action="retry",
                updates={"attempt_count": attempts + 1},
            )

        loop_edge = self.loop_exceeded_edge
        if loop_edge is not None:
            max_loop = loop_edge.max_loop or 0
            if state.loop_counter >= max_loop:
                return TransitionResult(
                    next_status=StageStatus.PENDING,
                    target_stage_id=loop_edge.to_stage,
                    action="spawn",
                )

        if self.failure_edge is not None:
            return TransitionResult(
                next_status=StageStatus.PENDING,
                target_stage_id=self.failure_edge.to_stage,
                action="spawn",
            )

        return TransitionResult(
            next_status=StageStatus.ERROR,
            action="terminate",
        )

    def valid_routing_choices(self) -> list[str]:
        """返回有效的路由选择项（来自带 choice 的 SUCCESS 边）。"""
        choices: list[str] = []
        for edge in self.ready_edges:
            if edge.condition == EdgeCondition.SUCCESS and edge.choice:
                if edge.choice not in choices:
                    choices.append(edge.choice)
        return choices

    def valid_confirm_choices(self) -> list[str]:
        """返回有效的确认选择项（来自带 choice 的 CONFIRMED 边）。"""
        choices: list[str] = []
        for edge in self.confirmed_edges:
            if edge.choice and edge.choice not in choices:
                choices.append(edge.choice)
        return choices
