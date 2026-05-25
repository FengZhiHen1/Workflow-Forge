"""调度编排器：按序执行 Processors，管理状态变更与 Action 组装。

Phase 4：完成 Processor 流水线显式化 + 单一状态变更点。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from services.scheduler.context import ExecutionContext
from services.scheduler.state_model import InstanceState, StateDelta
from services.scheduler.processors.base import Processor, ProcessorResult
from services.scheduler.processors.sync_worktree import SyncWorktreeProcessor
from services.scheduler.processors.consume_messages import ConsumeMessagesProcessor
from services.scheduler.processors.state_transition import StateTransitionProcessor
from services.scheduler.processors.auto_commit import AutoCommitProcessor
from services.scheduler.processors.merge_worktrees import MergeWorktreesProcessor
from services.scheduler.processors.virtual_stages import VirtualStagesProcessor
from services.scheduler.processors.child_workflow import ChildWorkflowProcessor
from services.scheduler.processors.parallel_split import ParallelSplitProcessor
from services.scheduler.processors.error_recovery import ErrorRecoveryProcessor
from services.scheduler.processors.conflict_handler import ConflictHandlerProcessor
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
        SyncWorktreeProcessor,        # 01 - worktree 同步
        ConsumeMessagesProcessor,     # 02 - 消息消费（只设元数据 + cycle_meta）
        StateTransitionProcessor,     # 03 - 单一状态变更点
        AutoCommitProcessor,          # 04 - 读 cycle_meta.newly_done
        MergeWorktreesProcessor,      # 05 - 读 cycle_meta.newly_done
        VirtualStagesProcessor,       # 06 - 虚拟 stage 直通
        ChildWorkflowProcessor,       # 07 - 递归调度（替代 CheckChildren）
        ParallelSplitProcessor,       # 08 - parallel 拆分
        ErrorRecoveryProcessor,       # 09 - 基于 TransitionPolicy（替代 ErrorHandler）
        ConflictHandlerProcessor,     # 10 - 冲突自愈
        ReadyComputeProcessor,        # 11 - 就绪计算
        AllocateSpawnProcessor,       # 12 - worktree 分配 + action 生成
        ConfirmAggregateProcessor,    # 13 - 确认点聚合（读 cycle_meta.child_confirm_pending）
        FinalizeProcessor,            # 14 - 收尾
    ])

    def run(self, ctx: ExecutionContext, initial_state: InstanceState) -> dict[str, Any]:
        """执行完整调度流程。

        Returns:
            {"status": "ok", "actions": [...], "_state": InstanceState}
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

        return {"status": "ok", "actions": all_actions, "_state": state}
