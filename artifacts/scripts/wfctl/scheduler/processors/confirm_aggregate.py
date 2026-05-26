"""ConfirmAggregateProcessor：确认点聚合。

步骤 12：收集 AWAITING_CONFIRM stage，预解析 questions 为结构化 options。
仅在无就绪 stage 时产出 confirm——有活先派活，流水线排空后再问用户。
"""

from __future__ import annotations

from dataclasses import dataclass

from scheduler.context import ExecutionContext
from state.model import InstanceState, StageStatus
from scheduler.processors.base import ProcessorResult


@dataclass
class ConfirmAggregateProcessor:
    """收集当前实例的 AWAITING_CONFIRM stage，预解析问题选项。

    门控规则：当 ready_candidates 非空时，优先推进流水线——
    将 confirm 推迟到就绪 slot 全部派完后。
    """

    def process(self, ctx: ExecutionContext, state: InstanceState) -> ProcessorResult:
        # 有就绪 stage 可推进 → 优先派活，暂不呈现确认
        if state.cycle_meta.ready_candidates:
            return ProcessorResult()

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
            options = _parse_questions(st.confirm_questions)
            if not options:
                continue
            pending.append({
                "stage_id": st.stage_id,
                "instance_id": state.instance_id,
                "options": options,
            })
        return pending if pending else None


def _parse_questions(questions: list[str]) -> list[dict]:
    """将问题列表预解析为结构化选项。

    选项文本即 key——SubAgent 传入的每项直接作为 choice_key 和 label，
    不再要求 "choice_key：描述" 两段式格式。
    """
    result: list[dict] = []
    for q in questions:
        q = q.strip()
        if not q:
            continue
        result.append({"choice_key": q, "description": ""})
    return result
