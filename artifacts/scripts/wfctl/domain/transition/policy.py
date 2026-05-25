"""TransitionPolicy: 集中化边处理，单一真相源。

将分散在 core/dag.py、ErrorRecoveryProcessor、cli/confirm.py
中的边处理逻辑集中于此。所有方法为纯函数，不产生副作用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from domain.dag.graph import AdjacencyList
from domain.workflow.spec import EdgeCondition, EdgeSpec, StageSpec, StageStatus, StageTargetType
from state.model import InstanceState, StageState, StateDelta, InstanceStatus

from domain.transition.results import (
    CascadeResetResult,
    ConfirmResult,
    MergeConfirmResult,
    RollbackResult,
    SkipResult,
    TransitionResult,
)


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

    # ── 边匹配方法 ──

    def match_confirmed_edge(self, choice: str) -> EdgeSpec | None:
        """精确匹配 choice 的 confirmed 边，无匹配时返回无 choice 的兜底边。"""
        for edge in self.confirmed_edges:
            if edge.choice == choice:
                return edge
        for edge in self.confirmed_edges:
            if not edge.choice:
                return edge
        return None

    def match_rejected_edge(self, choice: str) -> EdgeSpec | None:
        """精确匹配 choice 的 rejected 边，无匹配时返回无 choice 的兜底边。"""
        for edge in self.rejected_edges:
            if edge.choice == choice:
                return edge
        for edge in self.rejected_edges:
            if not edge.choice:
                return edge
        return None

    def match_success_edge(self, routing_choice: str | None) -> EdgeSpec | None:
        """按 routing_choice 匹配 SUCCESS 边，无匹配时返回无 choice 的兜底边。"""
        for edge in self.ready_edges:
            if edge.condition == EdgeCondition.SUCCESS and edge.choice == routing_choice:
                return edge
        for edge in self.ready_edges:
            if edge.condition == EdgeCondition.SUCCESS and not edge.choice:
                return edge
        return None

    def validate_routing_choice(self, routing_choice: str | None) -> tuple[bool, str]:
        """校验 routing_choice 合法性。返回 (is_valid, reason)。"""
        if not routing_choice:
            return True, ""
        valid = self.valid_routing_choices()
        if not valid:
            return True, ""
        if routing_choice not in valid:
            return False, f"非法 routing_choice: '{routing_choice}'，合法值: {valid}"
        return True, ""

    # ── CLI 命令决策方法（纯函数）──

    def on_confirm(
        self,
        stage: StageState,
        choice: str,
        has_feedback: bool = False,
        stage_order: list[str] | None = None,
    ) -> ConfirmResult:
        """确认/拒绝操作的纯决策。

        根据 choice 匹配 confirmed 或 rejected 边，返回 ConfirmResult。
        不执行任何副作用（文件读写、Git 操作等）。

        Args:
            stage: 当前 AWAITING_CONFIRM 状态的 stage
            choice: 用户选择的选项
            has_feedback: 用户是否提供了反馈文本
            stage_order: spec.stages 的顺序列表（用于回边检测）
        """
        # 1. 尝试匹配 confirmed 边
        matched = self.match_confirmed_edge(choice)
        if matched is not None:
            return self._handle_confirmed_match(stage, choice, matched, has_feedback, stage_order)

        # 2. 尝试匹配 rejected 边
        matched = self.match_rejected_edge(choice)
        if matched is not None:
            return self._handle_rejected_match(stage, choice, matched)

        # 3. 无匹配 → instance FAILED
        all_choices: list[str] = []
        for e in self.confirmed_edges + self.rejected_edges:
            if e.choice and e.choice not in all_choices:
                all_choices.append(e.choice)
        hint = f" 合法选项：{all_choices}" if all_choices else "（该 stage 未定义任何带 choice 的边）"
        return ConfirmResult(
            next_status=StageStatus.ERROR,
            action="terminate",
            reason=f"未知的选项：'{choice}'。{hint}",
            instance_failed=True,
        )

    def _handle_confirmed_match(
        self,
        stage: StageState,
        choice: str,
        edge: EdgeSpec,
        has_feedback: bool,
        stage_order: list[str] | None,
    ) -> ConfirmResult:
        """处理 confirmed 边匹配。"""
        if edge.to_stage == edge.from_stage:
            return self._handle_relay_confirm(stage, choice, edge, has_feedback)
        return self._handle_final_confirm(stage, choice, edge, has_feedback, stage_order)

    def _handle_relay_confirm(
        self, stage: StageState, choice: str, edge: EdgeSpec, has_feedback: bool
    ) -> ConfirmResult:
        """中继确认：edge 回指自身，stage 继续执行。"""
        loop_counter = stage.loop_counter
        max_loop = edge.max_loop or 0

        if max_loop and loop_counter >= max_loop:
            exceeded = self.loop_exceeded_edge
            if exceeded is not None:
                return ConfirmResult(
                    next_status=StageStatus.DONE,
                    exit_condition="loop_exceeded",
                    target_stage_id=exceeded.to_stage,
                    updates={"loop_counter": loop_counter},
                    action="spawn",
                    reason=f"循环超限 (choice={choice}, loop={loop_counter})",
                )
            return ConfirmResult(
                next_status=StageStatus.ERROR,
                action="terminate",
                reason=f"loop exceeded for choice: {choice}",
                instance_failed=True,
            )

        return ConfirmResult(
            next_status=StageStatus.PENDING,
            updates={
                "loop_counter": loop_counter + 1,
                "system_agent_id": None,
                "continued_to": None,
            },
            requires_feedback=has_feedback,
            is_relay=True,
            action="retry",
            reason=f"中继确认 (choice={choice}, loop={loop_counter + 1})",
        )

    def _handle_final_confirm(
        self,
        stage: StageState,
        choice: str,
        edge: EdgeSpec,
        has_feedback: bool,
        stage_order: list[str] | None,
    ) -> ConfirmResult:
        """终局确认：edge 指向下游，stage 结束。"""
        # confirmation_point：用户确认后 SubAgent 继续
        if self.spec.confirmation_point:
            return ConfirmResult(
                next_status=StageStatus.PENDING,
                updates={"confirmed_choice": choice},
                requires_feedback=has_feedback,
                action="continue",
                reason="confirmation_point: 确认后继续执行",
            )

        # 回边检测
        cascade_target = None
        if stage_order is not None:
            try:
                from_idx = stage_order.index(self.stage_id)
                to_idx = stage_order.index(edge.to_stage)
                if to_idx < from_idx:
                    cascade_target = edge.to_stage
            except ValueError:
                pass

        return ConfirmResult(
            next_status=StageStatus.DONE,
            exit_condition="confirmed",
            target_stage_id=edge.to_stage,
            updates={"confirmed_choice": choice},
            cascade_reset_target=cascade_target,
            action="spawn" if edge.to_stage != self.stage_id else "",
            reason=f"终局确认 (choice={choice}, target={edge.to_stage})",
        )

    def _handle_rejected_match(
        self, stage: StageState, choice: str, edge: EdgeSpec
    ) -> ConfirmResult:
        """处理 rejected 边匹配。"""
        if edge.to_stage == edge.from_stage:
            loop_counter = stage.loop_counter
            max_loop = edge.max_loop or 0
            if max_loop and loop_counter >= max_loop:
                exceeded = self.loop_exceeded_edge
                if exceeded is not None:
                    return ConfirmResult(
                        next_status=StageStatus.DONE,
                        exit_condition="loop_exceeded",
                        target_stage_id=exceeded.to_stage,
                        updates={"loop_counter": loop_counter},
                        action="spawn",
                        reason=f"rejected 循环超限 (choice={choice})",
                    )
                return ConfirmResult(
                    next_status=StageStatus.ERROR,
                    action="terminate",
                    reason=f"loop exceeded for rejected choice: {choice}",
                    instance_failed=True,
                )

        return ConfirmResult(
            next_status=StageStatus.DONE,
            exit_condition="rejected",
            target_stage_id=edge.to_stage,
            updates={"attempt_count": 0},
            is_rejected=True,
            action="spawn" if edge.to_stage != self.stage_id else "",
            reason=f"拒绝 (choice={choice}, target={edge.to_stage})",
        )

    @staticmethod
    def on_pause(state: InstanceState) -> StateDelta:
        """暂停实例：重置 RUNNING stage → PENDING，实例 → PAUSED。"""
        stage_updates: dict[str, dict[str, Any]] = {}
        for st in state.stages:
            if st.status == StageStatus.RUNNING:
                stage_updates[st.stage_instance_id] = {"status": StageStatus.PENDING}
        return StateDelta(
            stage_updates=stage_updates,
            instance_updates={"status": InstanceStatus.PAUSED},
        )

    @staticmethod
    def on_resume(state: InstanceState) -> StateDelta:
        """恢复实例：实例 → ACTIVE。"""
        return StateDelta(
            instance_updates={"status": InstanceStatus.ACTIVE},
        )

    def on_rollback(self, state: InstanceState, adj: AdjacencyList) -> RollbackResult:
        """回退决策：确定受影响的 stage 列表并生成重置 delta。

        Returns:
            RollbackResult 含 reset_stage_ids 和对应的 StateDelta
        """
        from domain.dag.graph import collect_downstream

        downstream = collect_downstream(
            adj, self.stage_id, {EdgeCondition.FAILURE, EdgeCondition.LOOP_EXCEEDED}
        )
        reset_stages = [self.stage_id] + list(downstream)

        stage_updates: dict[str, dict[str, Any]] = {}
        for st in state.stages:
            if st.stage_id not in reset_stages:
                continue
            updates: dict[str, Any] = {
                "status": StageStatus.PENDING,
                "attempt_count": 0,
                "loop_counter": 0,
                "system_agent_id": None,
                "continued_to": None,
                "output_message_id": None,
            }
            stage_updates[st.stage_instance_id] = updates

        # 清理 consumed_message_ids 中被重置 stage 产出的消息
        consumed = set(state.consumed_message_ids)
        for st in state.stages:
            if st.stage_id in reset_stages and st.output_message_id:
                consumed.discard(st.output_message_id)

        delta = StateDelta(
            stage_updates=stage_updates,
            instance_updates={"consumed_message_ids": frozenset(consumed)},
        )

        return RollbackResult(reset_stage_ids=reset_stages, delta=delta)

    def on_skip(
        self, state: InstanceState, force: bool = False
    ) -> SkipResult:
        """跳过决策：验证并标记 target stage 为 DONE。

        Returns:
            SkipResult 含 stage_instance_ids 和 StateDelta
        """
        FORCEABLE_STATES = {"PENDING", "RUNNING", "AWAITING_CONFIRM", "ERROR"}
        targets = state.stages_by_id(self.stage_id)
        if not targets:
            from infrastructure.errors import StateError
            raise StateError(f"Stage not found: {self.stage_id}")

        if all(s.status == StageStatus.DONE for s in targets):
            from infrastructure.errors import StateError
            raise StateError(f"All instances of stage {self.stage_id} are already DONE")

        for s in targets:
            status = s.status.value if hasattr(s.status, 'value') else str(s.status)
            if status not in FORCEABLE_STATES:
                from infrastructure.errors import StateError
                raise StateError(
                    f"Stage {self.stage_id} ({s.stage_instance_id}) is {status}, "
                    f"only {sorted(FORCEABLE_STATES)} stages can be skipped"
                )
            if status != "PENDING" and not force:
                from infrastructure.errors import StateError
                raise StateError(
                    f"Stage {self.stage_id} ({s.stage_instance_id}) is {status}, "
                    f"not PENDING. Use --force to skip non-PENDING stages."
                )

        stage_updates: dict[str, dict[str, Any]] = {}
        stage_inst_ids: list[str] = []
        for s in targets:
            stage_updates[s.stage_instance_id] = {
                "status": StageStatus.DONE,
                "started_at": None,
            }
            stage_inst_ids.append(s.stage_instance_id)

        return SkipResult(
            stage_instance_ids=stage_inst_ids,
            force_applied=force,
            delta=StateDelta(stage_updates=stage_updates),
        )

    @staticmethod
    def compute_cascade_reset(
        state: InstanceState,
        from_stage_id: str,
        to_stage_id: str,
        stage_order: list[str],
    ) -> CascadeResetResult:
        """回边级联重置：计算起止 stage 之间所有需重置的 stage。

        当 confirm 边指向拓扑序更早的 stage 时，将 [to_stage, from_stage]
        范围内的所有 DONE/ERROR stage 重置为 PENDING。
        """
        try:
            from_idx = stage_order.index(from_stage_id)
            to_idx = stage_order.index(to_stage_id)
        except ValueError:
            return CascadeResetResult()

        if to_idx >= from_idx:
            return CascadeResetResult()

        reset_inst_ids: list[str] = []
        remove_inst_ids: list[str] = []
        cleanup_stage_ids: list[str] = []

        for i in range(to_idx, from_idx + 1):
            sid = stage_order[i]
            existing = state.stages_by_id(sid)
            needs_reset = any(
                e.status in (StageStatus.DONE, StageStatus.ERROR) for e in existing
            )
            if not needs_reset:
                continue

            cleanup_stage_ids.append(sid)
            for e in existing:
                remove_inst_ids.append(e.stage_instance_id)
            # 折叠为单一 PENDING 条目（会在 apply_delta 时通过 append_stages 处理）
            reset_inst_ids.append(sid)

        return CascadeResetResult(
            reset_stage_instance_ids=reset_inst_ids,
            removed_stage_instance_ids=remove_inst_ids,
            cleanup_running_agent_stage_ids=cleanup_stage_ids,
        )

    @staticmethod
    def build_merge_stage(instance_id: str, goal: str) -> StageState:
        """构建 __merge__ 伪 stage。"""
        return StageState(
            stage_id="__merge__",
            stage_instance_id=f"{instance_id}__merge__",
            status=StageStatus.AWAITING_CONFIRM,
            confirmation_point=False,
        )

    @staticmethod
    def on_merge_confirm(choice: str) -> MergeConfirmResult:
        """__merge__ 确认决策。"""
        choice_lower = choice.lower()
        if choice_lower in ("yes", "y", "confirm", "accept", "ok"):
            return MergeConfirmResult(merge_confirmed=True, remove_merge_stage=True)
        return MergeConfirmResult(merge_confirmed=False, remove_merge_stage=True)

    @staticmethod
    def _is_terminal_stage(stage_id: str, spec_stages: list[StageSpec]) -> bool:
        """判断 stage 是否为终态 stage（被 rejected/loop_exceeded 指向时实例应 FAILED）。"""
        for s in spec_stages:
            if s.stage_id == stage_id and s.target_type == StageTargetType.VIRTUAL:
                if "workflow-end" in stage_id or "终结" in s.name or "终止" in s.name:
                    return True
        return False
