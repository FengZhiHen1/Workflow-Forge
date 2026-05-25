"""SyncWorktreeProcessor：同步 worktree 与上游。

步骤 0：Level 1（根实例）或 Level 1.5（子实例）同步。
失败时记录 deviation，不阻塞流程。
"""

from __future__ import annotations

from dataclasses import dataclass

from scheduler.context import ExecutionContext
from state.model import InstanceState
from scheduler.processors.base import ProcessorResult, SideEffect
from services.state_manager import append_deviation as _append_deviation
from runtime.worktree.manager import sync_instance_with_main, sync_instance_with_parent


@dataclass
class SyncWorktreeProcessor:
    """同步实例 worktree 与上游。"""

    def process(self, ctx: ExecutionContext, state: InstanceState) -> ProcessorResult:
        side_effects: list[SideEffect] = []
        parent_id = state.parent_instance_id

        if parent_id:
            result = sync_instance_with_parent(ctx.instance_id, parent_id)
        else:
            result = sync_instance_with_main(ctx.instance_id)

        side_effects.append(SideEffect(
            kind="git_merge", description="Sync worktree with upstream", execute=None,
        ))

        if not result.success:
            side_effects.append(SideEffect(
                kind="deviation_write",
                description="Sync skipped deviation",
                execute=lambda iid=ctx.instance_id, msg=result.message: _append_deviation(iid, "SYNC_SKIPPED", msg),
            ))

        return ProcessorResult(side_effects=side_effects)
