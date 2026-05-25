"""ConflictHandlerProcessor：处理 CONFLICT 分支。

步骤 7：尝试重试合并冲突的 stage worktree。
"""

from __future__ import annotations

from dataclasses import dataclass

from infrastructure.errors import GitError
from scheduler.context import ExecutionContext
from state.model import InstanceState, StageState, StateDelta, StageStatus
from scheduler.processors.base import ProcessorResult
from runtime.worktree.manager import resolve_conflicts_and_merge, tag_anchor


@dataclass
class ConflictHandlerProcessor:
    """处理 CONFLICT stage：尝试自动合并。"""

    def process(self, ctx: ExecutionContext, state: InstanceState) -> ProcessorResult:
        delta = StateDelta()
        actions: list[dict] = []

        for st in state.stages:
            if st.status != StageStatus.CONFLICT:
                continue

            stage_id = st.stage_id
            stage_inst_id = st.stage_instance_id

            try:
                success = resolve_conflicts_and_merge(ctx.instance_id, stage_inst_id)
                if success:
                    delta.stage_updates[st.stage_instance_id] = {"status": StageStatus.DONE}
                    # 打锚点
                    anchor = f"{ctx.spec.anchor_prefix}-{ctx.instance_id}-{stage_inst_id}"
                    try:
                        tag_anchor(ctx.instance_id, anchor)
                    except Exception:
                        pass
                else:
                    actions.append({
                        "action": "conflict",
                        "instance_id": ctx.instance_id,
                        "stage_id": stage_id,
                        "conflict_files": st.conflict_files,
                        "source_stage": stage_id,
                    })
            except GitError:
                actions.append({
                    "action": "conflict",
                    "instance_id": ctx.instance_id,
                    "stage_id": stage_id,
                    "conflict_files": st.conflict_files,
                    "source_stage": stage_id,
                })

        return ProcessorResult(state_delta=delta, actions=actions)
