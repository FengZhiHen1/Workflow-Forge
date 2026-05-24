"""SyncWorktreeProcessor：同步 worktree 与上游。

步骤 0：Level 1（根实例）或 Level 1.5（子实例）同步。
失败时记录 deviation，不阻塞流程。
"""

from __future__ import annotations

from dataclasses import dataclass

from services.scheduler.context import ExecutionContext
from services.scheduler.state_model import InstanceState
from services.scheduler.processors.base import ProcessorResult
from services.state_manager import append_deviation
from services.worktree_manager import sync_instance_with_main, sync_instance_with_parent


@dataclass
class SyncWorktreeProcessor:
    """同步实例 worktree 与上游。"""

    def process(self, ctx: ExecutionContext, state: InstanceState) -> ProcessorResult:
        parent_id = state.parent_instance_id

        if parent_id:
            success, msg = sync_instance_with_parent(ctx.instance_id, parent_id)
        else:
            success, msg = sync_instance_with_main(ctx.instance_id)

        if not success:
            append_deviation(ctx.instance_id, "SYNC_SKIPPED", msg)

        return ProcessorResult()
