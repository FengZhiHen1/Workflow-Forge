"""VirtualStagesProcessor：预处理虚拟 stage。

步骤 8：在就绪计算前将满足条件的虚拟 stage 标为 DONE。
使用 TransitionPolicy 替代 _all_satisfied_virtual。
"""

from __future__ import annotations

from dataclasses import dataclass

from domain.workflow.spec import StageTargetType
from domain.transition.policy import TransitionPolicy
from scheduler.context import ExecutionContext
from state.model import InstanceState, StageState, StateDelta, StageStatus
from scheduler.processors.base import ProcessorResult, SideEffect
from runtime.worktree.manager import tag_anchor as _tag_anchor


@dataclass
class VirtualStagesProcessor:
    """将满足上游条件的虚拟 stage 标记为 DONE。"""

    def process(self, ctx: ExecutionContext, state: InstanceState) -> ProcessorResult:
        delta = StateDelta()
        side_effects: list[SideEffect] = []
        policy_cache: dict[str, TransitionPolicy] = {}

        def _silent_tag(iid: str, anchor: str) -> None:
            try:
                _tag_anchor(iid, anchor)
            except Exception:
                pass

        def _get_policy(stage_id: str) -> TransitionPolicy:
            if stage_id not in policy_cache:
                policy_cache[stage_id] = TransitionPolicy.from_adjacency(ctx.adj, stage_id)
            return policy_cache[stage_id]

        changed = True
        while changed:
            changed = False
            stage_index = {s.stage_instance_id: s for s in state.stages}

            for st in list(state.stages):
                stage_spec = ctx.adj.stages.get(st.stage_id)
                if not stage_spec or stage_spec.target_type != StageTargetType.VIRTUAL:
                    continue
                if st.stage_instance_id in delta.stage_updates:
                    st = st.replace(**delta.stage_updates[st.stage_instance_id])
                if st.status != StageStatus.PENDING:
                    continue

                upstream_edges = ctx.adj.incoming.get(st.stage_id, [])
                policy = _get_policy(st.stage_id)
                if _all_upstream_virtual_satisfied(upstream_edges, state, policy):
                    delta.stage_updates[st.stage_instance_id] = {"status": StageStatus.DONE}
                    anchor = f"{ctx.spec.anchor_prefix}-{ctx.instance_id}-{st.stage_instance_id}"
                    side_effects.append(SideEffect(
                        kind="git_tag",
                        description=f"Virtual stage anchor {st.stage_instance_id}",
                        execute=lambda iid=ctx.instance_id, a=anchor: _silent_tag(iid, a),
                    ))
                    changed = True

            if changed:
                state = state.apply_delta(delta)

        return ProcessorResult(state_delta=delta, side_effects=side_effects)


def _all_upstream_virtual_satisfied(
    upstream_edges: list,
    state: InstanceState,
    policy: TransitionPolicy,
) -> bool:
    """虚拟 stage 的就绪判断，使用 TransitionPolicy。"""
    if not upstream_edges:
        return True
    for edge in upstream_edges:
        upstream = state.first_stage_by_id(edge.from_stage)
        if upstream is None or upstream.status != StageStatus.DONE:
            continue
        if policy.is_upstream_satisfied(upstream, edge):
            return True
    return False
