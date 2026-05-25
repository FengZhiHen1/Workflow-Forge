"""ConfirmAggregateProcessor：确认点聚合。

步骤 12：收集 AWAITING_CONFIRM stage。
"""

from __future__ import annotations

from dataclasses import dataclass

from domain.dag.graph import get_confirmed_edges, get_rejected_edges
from scheduler.context import ExecutionContext
from state.model import InstanceState, StageStatus
from scheduler.processors.base import ProcessorResult


@dataclass
class ConfirmAggregateProcessor:
    """收集当前实例的 AWAITING_CONFIRM stage。"""

    def process(self, ctx: ExecutionContext, state: InstanceState) -> ProcessorResult:
        local_pending = self._collect_confirm_pending(state, ctx) or []
        child_pending = state.cycle_meta.child_confirm_pending
        all_pending = local_pending + child_pending
        if all_pending:
            return ProcessorResult(actions=[{"action": "confirm", "pending": all_pending}])
        return ProcessorResult()

    def _collect_confirm_pending(
        self, state: InstanceState, ctx: ExecutionContext
    ) -> list[dict] | None:
        pending: list[dict] = []
        for st in state.stages:
            if st.status != StageStatus.AWAITING_CONFIRM:
                continue
            pending.append({
                "stage_id": st.stage_id,
                "instance_id": state.instance_id,
                "questions": st.confirm_questions,
                "valid_choices": self._collect_valid_choices(ctx, st.stage_id),
            })
        return pending if pending else None

    def _collect_valid_choices(self, ctx: ExecutionContext, stage_id: str) -> list[str]:
        """收集 stage 所有 confirmed + rejected 边的 choice 值（去重）。"""
        choices: list[str] = []
        for e in get_confirmed_edges(ctx.adj, stage_id) + get_rejected_edges(ctx.adj, stage_id):
            if e.choice and e.choice not in choices:
                choices.append(e.choice)
        return choices
