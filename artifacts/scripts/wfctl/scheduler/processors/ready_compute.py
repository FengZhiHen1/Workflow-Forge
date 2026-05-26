"""ReadyComputeProcessor：就绪计算 + 调度约束。

步骤 11：使用 InstanceState 原生 compute_ready，
结果写入 cycle_meta.ready_candidates（替代 ctx.extra）。
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from domain.dag.graph import compute_ready
from scheduler.context import ExecutionContext
from state.model import InstanceState, StateDelta, StageStatus
from scheduler.processors.base import ProcessorResult


@dataclass
class ReadyComputeProcessor:
    """计算就绪 stage 并应用调度约束。"""

    def process(self, ctx: ExecutionContext, state: InstanceState) -> ProcessorResult:
        ready = compute_ready(ctx.adj, state)
        ready = self._apply_scheduling_constraints(ready, state, ctx)

        delta = StateDelta(
            cycle_meta=replace(state.cycle_meta, ready_candidates=ready),
        )
        return ProcessorResult(state_delta=delta)

    def _apply_scheduling_constraints(
        self,
        ready: list[tuple[str, str]],
        state: InstanceState,
        ctx: ExecutionContext,
    ) -> list[tuple[str, str]]:
        running = [s for s in state.stages if s.status == StageStatus.RUNNING]
        running_stage_ids = {s.stage_id for s in running}
        stage_spec_map = {s.stage_id: s for s in ctx.spec.stages}

        if any(
            stage_spec_map.get(sid) and stage_spec_map[sid].exclusive
            for sid in running_stage_ids
        ):
            return []

        max_parallel = ctx.spec.max_parallel_agents
        if len(running) >= max_parallel:
            return []

        available_slots = max_parallel - len(running)
        return ready[:available_slots]
