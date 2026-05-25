"""Processor 导出层。

流水线顺序（按 PROCESSORS 列表）：
  01. sync_worktree       # 同步 worktree
  02. message_consumer     # 消息消费 → cycle_meta
  03. state_transition     # 单一状态变更点（cycle_meta → stage status）
  04. auto_commit          # 自动 git commit DONE stage
  05. merge_worktrees      # 合入 merge worktree
  06. virtual_stages       # 虚拟 stage 直通（无 worktree，在 AutoCommit/MergeWorktrees 之后）
  07. child_workflow       # 递归调度子工作流
  08. parallel_split       # parallel 拆分
  09. error_recovery       # 错误恢复 + 超时检测
  10. conflict_handler     # 冲突自愈
  11. ready_compute        # 就绪计算
  12. allocate_spawn       # worktree 分配 + action 生成
  13. confirm_aggregate    # 确认点聚合
  14. finalize             # 收尾
"""
from .base import Processor, ProcessorResult, SideEffect
from .sync_worktree import SyncWorktreeProcessor
from .message_consumer import ConsumeMessagesProcessor
from .virtual_stages import VirtualStagesProcessor
from .state_transition import StateTransitionProcessor
from .auto_commit import AutoCommitProcessor
from .merge_worktrees import MergeWorktreesProcessor
from .parallel_split import ParallelSplitProcessor
from .child_workflow import ChildWorkflowProcessor
from .error_recovery import ErrorRecoveryProcessor
from .conflict_handler import ConflictHandlerProcessor
from .ready_compute import ReadyComputeProcessor
from .allocate_spawn import AllocateSpawnProcessor
from .confirm_aggregate import ConfirmAggregateProcessor
from .finalize import FinalizeProcessor

__all__ = [
    "Processor", "ProcessorResult", "SideEffect",
    "SyncWorktreeProcessor", "ConsumeMessagesProcessor",
    "VirtualStagesProcessor", "StateTransitionProcessor",
    "AutoCommitProcessor", "MergeWorktreesProcessor",
    "ParallelSplitProcessor", "ChildWorkflowProcessor",
    "ErrorRecoveryProcessor", "ConflictHandlerProcessor",
    "ReadyComputeProcessor", "AllocateSpawnProcessor",
    "ConfirmAggregateProcessor", "FinalizeProcessor",
]
