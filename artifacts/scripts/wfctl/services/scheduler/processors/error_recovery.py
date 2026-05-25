"""ErrorRecoveryProcessor：基于 TransitionPolicy 的错误恢复 + 超时检测。

委托给 TransitionPolicy.on_error() 做恢复决策，消除重复的边处理逻辑。
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

from core.atomic_write import atomic_write_json
from core.project import find_root
from core.timestamp import iso_timestamp, parse_iso_timestamp
from domain.transition.policy import TransitionPolicy
from services.scheduler.context import ExecutionContext
from services.scheduler.state_model import (
    CycleMeta,
    InstanceState,
    StageStatus,
    StateDelta,
    InstanceStatus,
)
from services.scheduler.processors.base import ProcessorResult
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

                delta.stage_updates[target.stage_instance_id] = {
                    "status": StageStatus.PENDING,
                    "loop_counter": st.loop_counter + 1,
                }
                actions.append({
                    "action": "spawn",
                    "instance_id": ctx.instance_id,
                    "stage_id": result.target_stage_id,
                    "reason": "error-recovery",
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
        timeout_delta, timeout_cycle = self._check_timeouts(ctx, state, stage_specs)
        if timeout_delta:
            delta = delta.merge(timeout_delta)
        if timeout_cycle:
            cycle_meta = self._merge_cycle_meta(cycle_meta, timeout_cycle)

        delta.cycle_meta = cycle_meta
        return ProcessorResult(state_delta=delta, actions=actions)

    def _check_timeouts(
        self, ctx: ExecutionContext, state: InstanceState, stage_specs: dict
    ) -> tuple[StateDelta | None, CycleMeta | None]:
        """检测 RUNNING stage 超时，写入合成 ERROR 消息。"""
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

                # 合成超时 ERROR 消息
                messages_dir = root / ".agent" / "instances" / ctx.instance_id / "messages"
                messages_dir.mkdir(parents=True, exist_ok=True)
                msg_id = f"msg-{uuid.uuid4().hex[:8]}"
                msg = {
                    "schema_version": "3.0.0",
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
                atomic_write_json(messages_dir / f"{msg_id}.json", msg)

                if cycle_meta is None:
                    cycle_meta = state.cycle_meta
                cycle_meta = cycle_meta.with_error(st.stage_instance_id)

                append_deviation(
                    ctx.instance_id,
                    "STAGE_TIMEOUT",
                    f"Stage {st.stage_id} timed out after {elapsed:.0f}s",
                    stage_id=st.stage_id,
                )

        return (delta if not delta.is_empty() else None), cycle_meta

    @staticmethod
    def _merge_cycle_meta(base: CycleMeta, other: CycleMeta) -> CycleMeta:
        """合并两个 CycleMeta 的差分集合。"""
        return CycleMeta(
            newly_done_stage_instance_ids=base.newly_done_stage_instance_ids | other.newly_done_stage_instance_ids,
            newly_error_stage_instance_ids=base.newly_error_stage_instance_ids | other.newly_error_stage_instance_ids,
            newly_awaiting_confirm_ids=base.newly_awaiting_confirm_ids | other.newly_awaiting_confirm_ids,
            ready_candidates=base.ready_candidates + other.ready_candidates,
        )
