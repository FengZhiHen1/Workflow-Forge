"""VirtualStagesProcessor：预处理虚拟 stage。

步骤 8：在就绪计算前将满足条件的虚拟 stage 标为 DONE。
"""

from __future__ import annotations

from dataclasses import dataclass

from core.dag import AdjacencyList
from core.schema.interface import EdgeCondition, StageTargetType
from services.scheduler.context import ExecutionContext
from services.scheduler.state_model import InstanceState, StageState, StateDelta, StageStatus
from services.scheduler.processors.base import ProcessorResult
from services.worktree_manager import tag_anchor


@dataclass
class VirtualStagesProcessor:
    """将满足上游条件的虚拟 stage 标记为 DONE。"""

    def process(self, ctx: ExecutionContext, state: InstanceState) -> ProcessorResult:
        delta = StateDelta()
        stage_map = {s.stage_id: s for s in state.stages}
        spec_stages = {s.stage_id: s for s in ctx.spec.stages}

        changed = True
        while changed:
            changed = False
            for stage_id, stage_spec in spec_stages.items():
                if stage_spec.target_type != StageTargetType.VIRTUAL:
                    continue
                st = stage_map.get(stage_id)
                if not st or st.status != StageStatus.PENDING:
                    continue
                upstream_edges = ctx.adj.incoming.get(stage_id, [])
                if _all_satisfied_virtual(upstream_edges, stage_map):
                    delta.stage_updates[stage_id] = {"status": StageStatus.DONE}
                    stage_map[stage_id] = st.replace(status=StageStatus.DONE)
                    # 打锚点（副作用保持原样）
                    anchor = f"{ctx.spec.anchor_prefix}-{ctx.instance_id}-{stage_id}"
                    try:
                        tag_anchor(ctx.instance_id, anchor)
                    except Exception:
                        pass
                    changed = True

        return ProcessorResult(state_delta=delta)


def _all_satisfied_virtual(upstream_edges: list, stage_states: dict[str, StageState]) -> bool:
    """虚拟 stage 的就绪判断。"""
    if not upstream_edges:
        return True
    for edge in upstream_edges:
        upstream_stage = stage_states.get(edge.from_stage)
        if not upstream_stage or upstream_stage.status != StageStatus.DONE:
            continue
        exit_cond = upstream_stage.exit_condition
        if edge.condition == EdgeCondition.ALWAYS:
            return True
        if edge.condition == EdgeCondition.SUCCESS and exit_cond in ("success", ""):
            return True
        if edge.condition == EdgeCondition.CONFIRMED and exit_cond in ("confirmed", ""):
            return True
    return False
