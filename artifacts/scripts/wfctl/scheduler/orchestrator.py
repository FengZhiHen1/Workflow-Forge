"""调度编排器：按序执行 Processors，管理状态变更与 Action 组装。

Phase 5：目录重组，使用 scheduler.processors 统一导出。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from scheduler.context import ExecutionContext
from state.model import InstanceState, StateDelta
from scheduler.processors import (
    Processor,
    ProcessorResult,
    SyncWorktreeProcessor,
    ConsumeMessagesProcessor,
    StateTransitionProcessor,
    AutoCommitProcessor,
    MergeWorktreesProcessor,
    VirtualStagesProcessor,
    ChildWorkflowProcessor,
    ParallelSplitProcessor,
    ErrorRecoveryProcessor,
    ConflictHandlerProcessor,
    ReadyComputeProcessor,
    AllocateSpawnProcessor,
    ConfirmAggregateProcessor,
    FinalizeProcessor,
)


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
        VirtualStagesProcessor,       # 06 - 虚拟 stage 无 worktree，不需 AutoCommit/Merge，放其后执行
        ChildWorkflowProcessor,       # 07 - 递归调度
        ParallelSplitProcessor,       # 08 - parallel 拆分
        ErrorRecoveryProcessor,       # 09 - 基于 TransitionPolicy
        ConflictHandlerProcessor,     # 10 - 冲突自愈
        ReadyComputeProcessor,        # 11 - 就绪计算
        AllocateSpawnProcessor,       # 12 - worktree 分配 + action 生成
        ConfirmAggregateProcessor,    # 13 - 确认点聚合
        FinalizeProcessor,            # 14 - 收尾
    ])

    def run(self, ctx: ExecutionContext, initial_state: InstanceState) -> dict[str, Any]:
        """执行完整调度流程。

        Returns:
            {"status": "ok", "actions": [...], "_state": InstanceState, "_side_effects": [...]}
        """
        state = initial_state
        all_actions: list[dict] = []
        all_side_effects: list[dict] = []

        for proc_cls in self.PROCESSORS:
            proc = proc_cls()
            result = proc.process(ctx, state)
            state = state.apply_delta(result.state_delta)
            all_actions.extend(result.actions)
            # 统一执行独立副作用（execute 非 None），收集所有副作用记录
            for se in result.side_effects:
                if se.execute is not None:
                    se.execute()
                all_side_effects.append({
                    "kind": se.kind,
                    "description": se.description,
                    "metadata": se.metadata,
                    "deferred": se.execute is not None,
                })

        if not all_actions:
            all_actions.append({"action": "await", "reason": "no ready stages"})

        return {"status": "ok", "actions": all_actions, "_state": state, "_side_effects": all_side_effects}
