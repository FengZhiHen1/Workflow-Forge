"""MergeWorktreesProcessor：并发 stage worktree 合并。

步骤 3：将 DONE stage 的独立 worktree 合并回实例 worktree。
"""

from __future__ import annotations

from dataclasses import dataclass

from core.errors import GitError
from services.scheduler.context import ExecutionContext
from services.scheduler.state_model import InstanceState, StageState, StateDelta, StageStatus
from services.scheduler.processors.base import ProcessorResult
from services.worktree_manager import merge_stage_worktree


@dataclass
class MergeWorktreesProcessor:
    """合并 DONE stage 的独立 worktree。"""

    def process(self, ctx: ExecutionContext, state: InstanceState) -> ProcessorResult:
        changes = ctx.extra.get("message_changes", [])
        return self._merge_done_stage_worktrees(ctx, state, changes)

    def _merge_done_stage_worktrees(
        self, ctx: ExecutionContext, state: InstanceState, changes: list[dict]
    ) -> ProcessorResult:
        inst_wt = ctx.root / ".tmp" / "worktrees" / f"instance-{ctx.instance_id}"

        merge_candidates = []
        for change in changes:
            if change.get("new_status") != "DONE":
                continue
            stage_id = change["stage_id"]
            worktree = ctx.worktree_map.get(stage_id)
            if not worktree or not worktree.exists():
                continue
            if worktree.resolve() == inst_wt.resolve():
                continue
            merge_candidates.append(change)

        if not merge_candidates:
            return ProcessorResult()

        merge_candidates.sort(key=lambda c: c["stage_id"])

        delta = StateDelta()
        actions: list[dict] = []

        for change in merge_candidates:
            stage_id = change["stage_id"]
            stage_inst_id = change.get("message", {}).get("stage_instance_id", stage_id)

            try:
                success, conflict_files = merge_stage_worktree(ctx.instance_id, stage_inst_id)
                if not success:
                    delta.stage_updates[stage_id] = {
                        "status": StageStatus.CONFLICT,
                        "conflict_files": conflict_files,
                    }
                    actions.append({
                        "action": "conflict",
                        "instance_id": ctx.instance_id,
                        "stage_id": stage_id,
                        "worktree": str(ctx.worktree_map[stage_id].relative_to(ctx.root)),
                        "conflict_files": conflict_files,
                        "source_stage": stage_id,
                    })
            except GitError:
                delta.stage_updates[stage_id] = {"status": StageStatus.CONFLICT}
                actions.append({
                    "action": "conflict",
                    "instance_id": ctx.instance_id,
                    "stage_id": stage_id,
                    "worktree": str(ctx.worktree_map[stage_id].relative_to(ctx.root)),
                    "conflict_files": [],
                    "source_stage": stage_id,
                })

        return ProcessorResult(state_delta=delta, actions=actions)
