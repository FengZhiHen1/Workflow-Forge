"""ConfirmAggregateProcessor：确认点聚合。

步骤 12：收集 AWAITING_CONFIRM stage。
"""

from __future__ import annotations

from dataclasses import dataclass

from scheduler.context import ExecutionContext
from state.model import InstanceState, StageStatus
from scheduler.processors.base import ProcessorResult


@dataclass
class ConfirmAggregateProcessor:
    """收集当前实例的 AWAITING_CONFIRM stage。"""

    def process(self, ctx: ExecutionContext, state: InstanceState) -> ProcessorResult:
        local_pending = self._collect_confirm_pending(state) or []
        child_pending = state.cycle_meta.child_confirm_pending
        all_pending = local_pending + child_pending
        if all_pending:
            return ProcessorResult(actions=[{"action": "confirm", "pending": all_pending}])
        return ProcessorResult()

    def _collect_confirm_pending(
        self, state: InstanceState
    ) -> list[dict] | None:
        pending: list[dict] = []
        for st in state.stages:
            if st.status != StageStatus.AWAITING_CONFIRM:
                continue
            pending.append({
                "stage_id": st.stage_id,
                "instance_id": state.instance_id,
                "questions": st.confirm_questions,
            })
        return pending if pending else None
