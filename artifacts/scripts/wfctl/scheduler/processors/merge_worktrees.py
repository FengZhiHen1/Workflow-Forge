"""MergeWorktreesProcessor：并发 stage worktree 合并。

步骤 3：将 DONE stage 的独立 worktree 合并回实例 worktree。
"""

from __future__ import annotations

from dataclasses import dataclass

from infrastructure.errors import GitError
from scheduler.context import ExecutionContext
from state.model import InstanceState, StateDelta, StageState, StageStatus
from scheduler.processors.base import ProcessorResult, SideEffect
from runtime.worktree.manager import merge_stage_worktree


@dataclass
class MergeWorktreesProcessor:
    """合并 DONE stage 的独立 worktree。"""

    def process(self, ctx: ExecutionContext, state: InstanceState) -> ProcessorResult:
        return self._merge_done_stage_worktrees(ctx, state)

    def _merge_done_stage_worktrees(
        self, ctx: ExecutionContext, state: InstanceState
    ) -> ProcessorResult:
        inst_wt = ctx.root / ".tmp" / "worktrees" / f"instance-{ctx.instance_id}"

        merge_candidates: list[StageState] = []
        for stage_inst_id in state.cycle_meta.newly_done_stage_instance_ids:
            st = state.stage_by_instance_id(stage_inst_id)
            if not st or st.status != StageStatus.DONE:
                continue
            worktree = ctx.worktree_map.get(st.stage_id)
            if not worktree or not worktree.exists():
                continue
            if worktree.resolve() == inst_wt.resolve():
                continue
            merge_candidates.append(st)

        if not merge_candidates:
            return ProcessorResult()

        merge_candidates.sort(key=lambda s: s.stage_id)

        delta = StateDelta()
        actions: list[dict] = []
        side_effects: list[SideEffect] = []

        for st in merge_candidates:
            try:
                success, conflict_files = merge_stage_worktree(ctx.instance_id, st.stage_instance_id)
                side_effects.append(SideEffect(
                    kind="git_merge",
                    description=f"Merge stage worktree {st.stage_instance_id}",
                    execute=None,
                ))
                if not success:
                    delta.stage_updates[st.stage_instance_id] = {
                        "status": StageStatus.CONFLICT,
                        "conflict_files": conflict_files,
                    }
                    actions.append({
                        "action": "conflict",
                        "instance_id": ctx.instance_id,
                        "stage_id": st.stage_id,
                        "worktree": str(ctx.worktree_map[st.stage_id].relative_to(ctx.root)),
                        "conflict_files": conflict_files,
                        "source_stage": st.stage_id,
                    })
            except GitError:
                delta.stage_updates[st.stage_instance_id] = {"status": StageStatus.CONFLICT}
                actions.append({
                    "action": "conflict",
                    "instance_id": ctx.instance_id,
                    "stage_id": st.stage_id,
                    "worktree": str(ctx.worktree_map[st.stage_id].relative_to(ctx.root)),
                    "conflict_files": [],
                    "source_stage": st.stage_id,
                })

        return ProcessorResult(state_delta=delta, actions=actions, side_effects=side_effects)
