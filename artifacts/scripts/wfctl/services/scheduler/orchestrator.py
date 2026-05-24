"""调度编排器：按序执行 Processors，管理状态变更与 Action 组装。

Phase 3：完成全部集成。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from services.scheduler.context import ExecutionContext
from services.scheduler.state_model import InstanceState, StateDelta
from services.scheduler.processors.base import Processor, ProcessorResult
from services.scheduler.processors.sync_worktree import SyncWorktreeProcessor
from services.scheduler.processors.consume_messages import ConsumeMessagesProcessor
from services.scheduler.processors.auto_commit import AutoCommitProcessor
from services.scheduler.processors.merge_worktrees import MergeWorktreesProcessor
from services.scheduler.processors.check_children import CheckChildrenProcessor
from services.scheduler.processors.parallel_split import ParallelSplitProcessor
from services.scheduler.processors.error_handler import ErrorHandlerProcessor
from services.scheduler.processors.conflict_handler import ConflictHandlerProcessor
from services.scheduler.processors.virtual_stages import VirtualStagesProcessor
from services.scheduler.processors.ready_compute import ReadyComputeProcessor
from services.scheduler.processors.allocate_spawn import AllocateSpawnProcessor
from services.scheduler.processors.confirm_aggregate import ConfirmAggregateProcessor
from services.scheduler.processors.finalize import FinalizeProcessor


@dataclass
class SchedulerOrchestrator:
    """调度编排器。

    按 PROCESSORS 顺序逐个执行，每步将 ProcessorResult 的 StateDelta
    应用到当前 InstanceState，收集所有 actions。
    """

    PROCESSORS: list[type[Processor]] = field(default_factory=lambda: [
        SyncWorktreeProcessor,
        ConsumeMessagesProcessor,
        AutoCommitProcessor,
        MergeWorktreesProcessor,
        CheckChildrenProcessor,
        ParallelSplitProcessor,
        ErrorHandlerProcessor,
        ConflictHandlerProcessor,
        VirtualStagesProcessor,
        ReadyComputeProcessor,
        AllocateSpawnProcessor,
        ConfirmAggregateProcessor,
        FinalizeProcessor,
    ])

    def run(self, ctx: ExecutionContext, initial_state: InstanceState) -> dict[str, Any]:
        """执行完整调度流程。

        Returns:
            {"status": "ok", "actions": [...]}
        """
        state = initial_state
        all_actions: list[dict] = []

        for proc_cls in self.PROCESSORS:
            proc = proc_cls()
            result = proc.process(ctx, state)
            state = state.apply_delta(result.state_delta)
            all_actions.extend(result.actions)

        if not all_actions:
            all_actions.append({"action": "await", "reason": "no ready stages"})

        return {"status": "ok", "actions": all_actions}
