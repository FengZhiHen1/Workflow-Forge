"""Processor 导出层。

流水线顺序（按 PROCESSORS 列表）：
  01. sync_worktree
  02. message_consumer
  03. virtual_stages
  04. state_transition
  05. auto_commit
  06. merge_worktrees
  07. parallel_split
  08. child_workflow
  09. error_recovery
  10. conflict_handler
  11. ready_compute
  12. allocate_spawn
  13. confirm_aggregate
  14. finalize
"""
from .base import Processor, ProcessorResult
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
    "Processor", "ProcessorResult",
    "SyncWorktreeProcessor", "ConsumeMessagesProcessor",
    "VirtualStagesProcessor", "StateTransitionProcessor",
    "AutoCommitProcessor", "MergeWorktreesProcessor",
    "ParallelSplitProcessor", "ChildWorkflowProcessor",
    "ErrorRecoveryProcessor", "ConflictHandlerProcessor",
    "ReadyComputeProcessor", "AllocateSpawnProcessor",
    "ConfirmAggregateProcessor", "FinalizeProcessor",
]
