"""ReadyComputeProcessor：就绪计算 + 调度约束。

步骤 9, 10：compute_ready + _apply_scheduling_constraints。
在 context 中标记 ready_stage_ids 供 AllocateSpawnProcessor 使用。
"""

from __future__ import annotations

from dataclasses import dataclass

from core.dag import compute_ready
from core.schema.interface import StageTargetType
from services.scheduler.context import ExecutionContext
from services.scheduler.state_model import InstanceState, StageStatus
from services.scheduler.processors.base import ProcessorResult


@dataclass
class ReadyComputeProcessor:
    """计算就绪 stage 并应用调度约束。"""

    def process(self, ctx: ExecutionContext, state: InstanceState) -> ProcessorResult:
        # 将 InstanceState 转换为旧格式的 dict 供 compute_ready 使用
        instance_dict = state.to_dict()
        ready = compute_ready(ctx.adj, instance_dict)
        ready = self._apply_scheduling_constraints(ready, state, ctx)
        # 将结果存入 context（供后续 Processor 使用）
        ctx.extra["ready_stage_ids"] = ready
        return ProcessorResult()

    def _apply_scheduling_constraints(
        self, ready: list[str], state: InstanceState, ctx: ExecutionContext
    ) -> list[str]:
        """应用 exclusive 和 max_parallel_agents 约束。"""
        running = [s for s in state.stages if s.status == StageStatus.RUNNING]
        running_stage_ids = {s.stage_id for s in running}
        stage_spec_map = {s.stage_id: s for s in ctx.spec.stages}

        # 有 exclusive RUNNING → 过滤掉所有就绪 stage
        if any(
            stage_spec_map.get(sid) and stage_spec_map[sid].exclusive
            for sid in running_stage_ids
        ):
            return []

        # max_parallel_agents
        max_parallel = ctx.spec.max_parallel_agents
        if len(running) >= max_parallel:
            return []

        available_slots = max_parallel - len(running)
        return ready[:available_slots]
