"""StateTransitionProcessor：单一状态变更点。

将 cycle_meta 中的差分事件应用到 stages，确保所有状态变更
经过同一条路径，方便审计和调试。
"""

from __future__ import annotations

from dataclasses import dataclass

from scheduler.context import ExecutionContext
from state.model import InstanceState, StageStatus, StateDelta
from scheduler.processors.base import ProcessorResult


@dataclass
class StateTransitionProcessor:
    """将 cycle_meta 中的事件批量转换为 stage 状态变更。

    这是 Processor 流水线中唯一直接设置 stage status 的地方。
    其他 Processor 通过标记 cycle_meta 来间接触发状态变更。
    """

    def process(self, ctx: ExecutionContext, state: InstanceState) -> ProcessorResult:
        delta = StateDelta()

        for sid in state.cycle_meta.newly_done_stage_instance_ids:
            delta.stage_updates[sid] = {"status": StageStatus.DONE}

        for sid in state.cycle_meta.newly_error_stage_instance_ids:
            delta.stage_updates[sid] = {"status": StageStatus.ERROR}

        for sid in state.cycle_meta.newly_awaiting_confirm_ids:
            delta.stage_updates[sid] = {"status": StageStatus.AWAITING_CONFIRM}

        return ProcessorResult(state_delta=delta)
