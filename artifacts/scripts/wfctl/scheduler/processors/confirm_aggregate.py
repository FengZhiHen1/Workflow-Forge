"""ConfirmAggregateProcessor：确认点聚合。

步骤 12：收集 AWAITING_CONFIRM stage，预解析 questions 为结构化 options。
"""

from __future__ import annotations

from dataclasses import dataclass

from scheduler.context import ExecutionContext
from state.model import InstanceState, StageStatus
from scheduler.processors.base import ProcessorResult


@dataclass
class ConfirmAggregateProcessor:
    """收集当前实例的 AWAITING_CONFIRM stage，预解析问题选项。"""

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
    """将 "choice_key：描述" 格式的问题列表预解析为结构化选项。

    编排器不再需要自行解析 `：` 分隔符——直接使用 choice_key 和 description。
    """
    result: list[dict] = []
    for q in questions:
        if not q:
            continue
        if "：" in q:
            key, _, desc = q.partition("：")
            key = key.strip()
            desc = desc.strip()
        else:
            key = q.strip()
            desc = ""
        if not key:
            continue
        result.append({"choice_key": key, "description": desc})
    return result
