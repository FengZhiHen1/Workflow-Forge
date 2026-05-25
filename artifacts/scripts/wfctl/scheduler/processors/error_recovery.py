"""ErrorRecoveryProcessor：基于 TransitionPolicy 的错误恢复 + 超时检测。

委托给 TransitionPolicy.on_error() 做恢复决策，消除重复的边处理逻辑。
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

from compat import CURRENT
from infrastructure.io import atomic_write_json
from infrastructure.project import find_root
from infrastructure.timestamp import iso_timestamp, parse_iso_timestamp
from domain.transition.policy import TransitionPolicy
from scheduler.context import ExecutionContext
from state.model import (
    CycleMeta,
    InstanceState,
    StageStatus,
    StateDelta,
    InstanceStatus,
)
from scheduler.processors.base import ProcessorResult, SideEffect
from services.state_manager import append_deviation


@dataclass
class ErrorRecoveryProcessor:
    """处理 ERROR 恢复和超时检测。

    错误恢复决策委托给 TransitionPolicy.on_error()。
    超时检测保留内联逻辑，但通过 cycle_meta 标记状态变更。
    """

    def process(self, ctx: ExecutionContext, state: InstanceState) -> ProcessorResult:
        delta = StateDelta()
        cycle_meta = state.cycle_meta
        actions: list[dict] = []
        side_effects: list[SideEffect] = []

        stage_specs = {s.stage_id: s for s in ctx.spec.stages}

        # 1. 错误恢复
        for st in state.stages:
            if st.status != StageStatus.ERROR:
                continue

            policy = TransitionPolicy.from_adjacency(ctx.adj, st.stage_id)
            result = policy.on_error(st)

            if result.action == "retry":
                delta.stage_updates[st.stage_instance_id] = {
                    "status": result.next_status,
                    **result.updates,
                }
                actions.append({
                    "action": "retry",
                    "instance_id": ctx.instance_id,
                    "stage_id": st.stage_id,
                    "attempt": st.attempt_count + 1,
                })

            elif result.action == "spawn":
                # 激活目标 stage
                target = state.first_stage_by_id(result.target_stage_id)
                if target is None:
                    delta.instance_updates["status"] = InstanceStatus.FAILED
                    actions.append({
                        "action": "terminate",
                        "instance_id": ctx.instance_id,
                        "status": "FAILED",
                        "reason": f"error target stage '{result.target_stage_id}' not found",
                    })
                    continue

                # 判断触发源：loop_exceeded → failure edge → error-recovery
                reason = "error-recovery"
                if self._is_loop_exceeded(st, ctx):
                    reason = "loop-exceeded"
                elif self._is_failure_edge_path(st, ctx):
                    reason = "failure-edge"

                delta.stage_updates[target.stage_instance_id] = {
                    "status": StageStatus.PENDING,
                    "loop_counter": st.loop_counter + 1,
                }
                actions.append({
                    "action": "spawn",
                    "instance_id": ctx.instance_id,
                    "stage_id": result.target_stage_id,
                    "reason": reason,
                })

            elif result.action == "terminate":
                delta.instance_updates["status"] = InstanceStatus.FAILED
                actions.append({
                    "action": "terminate",
                    "instance_id": ctx.instance_id,
                    "status": "FAILED",
                    "reason": f"no recovery path for stage {st.stage_id}",
                })

        # 2. 超时检测
        timeout_delta, timeout_cycle = self._check_timeouts(ctx, state, stage_specs, side_effects)
        if timeout_delta:
            delta = delta.merge(timeout_delta)
        if timeout_cycle:
            cycle_meta = self._merge_cycle_meta(cycle_meta, timeout_cycle)

        final_delta = StateDelta(
            stage_updates=delta.stage_updates,
            instance_updates=delta.instance_updates,
            append_stages=delta.append_stages,
            remove_stage_instance_ids=delta.remove_stage_instance_ids,
            cycle_meta=cycle_meta,
        )
        return ProcessorResult(state_delta=final_delta, actions=actions, side_effects=side_effects)

    def _check_timeouts(
        self, ctx: ExecutionContext, state: InstanceState, stage_specs: dict,
        side_effects: list[SideEffect],
    ) -> tuple[StateDelta | None, CycleMeta | None]:
        """检测 RUNNING stage 超时，生成合成 ERROR 消息的独立副作用。"""
        root = find_root()
        delta = StateDelta()
        cycle_meta: CycleMeta | None = None

        for st in state.stages:
            if st.status != StageStatus.RUNNING:
                continue
            if not st.started_at:
                continue
            stage_spec = stage_specs.get(st.stage_id)
            if not stage_spec or not stage_spec.timeout_seconds:
                continue
            try:
                elapsed = time.time() - parse_iso_timestamp(st.started_at)
            except (ValueError, OSError):
                continue
            if elapsed > stage_spec.timeout_seconds:
                delta.stage_updates[st.stage_instance_id] = {"started_at": None}

                # 合成超时 ERROR 消息 → 独立副作用
                messages_dir = root / ".agent" / "instances" / ctx.instance_id / "messages"
                messages_dir.mkdir(parents=True, exist_ok=True)
                msg_id = f"msg-{uuid.uuid4().hex[:8]}"
                msg_path = messages_dir / f"{msg_id}.json"
                msg = {
                    "schema_version": CURRENT.value,
                    "message_id": msg_id,
                    "instance_id": ctx.instance_id,
                    "stage_id": st.stage_id,
                    "stage_instance_id": st.stage_instance_id,
                    "status": "ERROR",
                    "report": f"Stage timed out after {stage_spec.timeout_seconds}s",
                    "checkpoint_summary": "",
                    "confirm_questions": [],
                    "parallel_targets": None,
                    "modified_files": [],
                    "timestamp": iso_timestamp(),
                }
                side_effects.append(SideEffect(
                    kind="file_write",
                    description=f"Timeout error message {msg_id}",
                    execute=lambda p=msg_path, m=msg: atomic_write_json(p, m),
                ))

                if cycle_meta is None:
                    cycle_meta = state.cycle_meta
                cycle_meta = cycle_meta.with_error(st.stage_instance_id)

                # deviation 写入 → 独立副作用
                side_effects.append(SideEffect(
                    kind="deviation_write",
                    description=f"Timeout deviation for {st.stage_id}",
                    execute=lambda iid=ctx.instance_id, e=elapsed, sid=st.stage_id: append_deviation(
                        iid, "STAGE_TIMEOUT",
                        f"Stage {sid} timed out after {e:.0f}s",
                        stage_id=sid,
                    ),
                ))

        return (delta if not delta.is_empty() else None), cycle_meta

    @staticmethod
    def _is_loop_exceeded(st: StageState, ctx: ExecutionContext) -> bool:
        """判断 stage 是否通过 loop_exceeded 路径恢复。"""
        from domain.dag.graph import get_loop_exceeded_edge
        edge = get_loop_exceeded_edge(ctx.adj, st.stage_id)
        if edge is None:
            return False
        max_loop = edge.max_loop or 0
        return st.loop_counter >= max_loop

    @staticmethod
    def _is_failure_edge_path(st: StageState, ctx: ExecutionContext) -> bool:
        """判断 stage 是否通过 failure_edge 路径恢复。"""
        from domain.dag.graph import get_failure_edge, get_loop_exceeded_edge
        failure = get_failure_edge(ctx.adj, st.stage_id)
        if failure is None:
            return False
        loop_edge = get_loop_exceeded_edge(ctx.adj, st.stage_id)
        if loop_edge is not None:
            max_loop = loop_edge.max_loop or 0
            if st.loop_counter >= max_loop:
                return False  # loop_exceeded 优先
        return True

    @staticmethod
    def _merge_cycle_meta(base: CycleMeta, other: CycleMeta) -> CycleMeta:
        """合并两个 CycleMeta 的差分集合。"""
        return CycleMeta(
            newly_done_stage_instance_ids=base.newly_done_stage_instance_ids | other.newly_done_stage_instance_ids,
            newly_error_stage_instance_ids=base.newly_error_stage_instance_ids | other.newly_error_stage_instance_ids,
            newly_awaiting_confirm_ids=base.newly_awaiting_confirm_ids | other.newly_awaiting_confirm_ids,
            ready_candidates=base.ready_candidates + other.ready_candidates,
        )
