"""FinalizeProcessor：组装 actions + 全部 DONE 合并。

步骤 13, 14：最终收尾。
"""

from __future__ import annotations

from dataclasses import dataclass

from core.errors import GitError
from core.schema.interface import StageTargetType
from services.scheduler.context import ExecutionContext
from services.scheduler.state_model import InstanceState, StageStatus, InstanceStatus, StateDelta
from services.scheduler.processors.base import ProcessorResult
from services.worktree_manager import merge_instance_to_main, tag_anchor


@dataclass
class FinalizeProcessor:
    """最终收尾：检查全部 DONE，执行合并。"""

    def process(self, ctx: ExecutionContext, state: InstanceState) -> ProcessorResult:
        if not self._check_all_done(state, ctx):
            return ProcessorResult()

        # 非根实例或已确认合并 → 直接执行合并
        if state.parent_instance_id or state.merge_confirmed:
            return self._execute_merge(ctx, state)

        # 根实例：插入合并确认伪 stage
        merge_stage = StageState(
            stage_id="__merge__",
            stage_instance_id="__merge__",
            status=StageStatus.AWAITING_CONFIRM,
            confirm_questions=[
                f"实例 {ctx.instance_id}（{state.goal}）全部 stage 已完成，是否合入 main？",
            ],
        )
        delta = StateDelta(append_stages=[merge_stage])
        return ProcessorResult(state_delta=delta)

    def _check_all_done(self, state: InstanceState, ctx: ExecutionContext) -> bool:
        """检查是否所有非虚拟 stage 都 DONE。"""
        non_virtual = [s for s in ctx.spec.stages if s.target_type != StageTargetType.VIRTUAL]
        if not non_virtual:
            return False
        stage_map = state.stage_map()
        return all(
            stage_map.get(s.stage_id) and stage_map[s.stage_id].status == StageStatus.DONE
            for s in non_virtual
        )

    def _execute_merge(self, ctx: ExecutionContext, state: InstanceState) -> ProcessorResult:
        """执行实例 worktree 合入主仓库。"""
        delta = StateDelta()
        actions: list[dict] = []
        try:
            success, conflict_files = merge_instance_to_main(ctx.instance_id)
            if success:
                delta.instance_updates["status"] = InstanceStatus.COMPLETED
                anchor = f"{ctx.spec.anchor_prefix}-{ctx.instance_id}-final"
                try:
                    tag_anchor(ctx.instance_id, anchor)
                except Exception:
                    pass
                actions.append({"action": "merge_to_main", "status": "completed"})
            else:
                actions.append({
                    "action": "conflict",
                    "conflict_files": conflict_files,
                    "worktree": ".",
                })
        except GitError as e:
            actions.append({"action": "merge_to_main", "status": "error", "reason": str(e)})

        return ProcessorResult(state_delta=delta, actions=actions)
