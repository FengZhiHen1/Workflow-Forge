"""ErrorHandlerProcessor：处理 ERROR 分支 + 超时检测。

步骤 6, 6.5：将 ERROR stage 按 retry / failure_edge / loop_exceeded 处理。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from core.dag import get_failure_edge, get_loop_exceeded_edge
from core.errors import GitError
from core.schema.interface import StageTargetType
from core.timestamp import iso_timestamp, parse_iso_timestamp
from services.scheduler.context import ExecutionContext
from services.scheduler.state_model import InstanceState, StageState, StateDelta, StageStatus, InstanceStatus
from services.scheduler.processors.base import ProcessorResult
from services.state_manager import append_deviation
from services.worktree_manager import tag_anchor


@dataclass
class ErrorHandlerProcessor:
    """处理 ERROR 分支和超时检测。"""

    def process(self, ctx: ExecutionContext, state: InstanceState) -> ProcessorResult:
        delta = StateDelta()
        actions: list[dict] = []

        for st in state.stages:
            if st.status != StageStatus.ERROR:
                continue

            stage_id = st.stage_id
            stage_spec = ctx.adj.stages.get(stage_id)
            max_attempts = stage_spec.retry if stage_spec else 0

            if st.attempt_count < max_attempts:
                delta.stage_updates[st.stage_instance_id] = {
                    "status": StageStatus.PENDING,
                    "attempt_count": st.attempt_count + 1,
                }
                actions.append({
                    "action": "retry",
                    "instance_id": ctx.instance_id,
                    "stage_id": stage_id,
                    "attempt": st.attempt_count + 1,
                })
                continue

            # 重试耗尽
            failure_edge = get_failure_edge(ctx.adj, stage_id)
            if failure_edge and st.loop_counter < (failure_edge.max_loop or 0):
                target_stage = state.first_stage_by_id(failure_edge.to_stage)
                if not target_stage:
                    delta.instance_updates["status"] = InstanceStatus.FAILED
                    actions.append({
                        "action": "terminate",
                        "instance_id": ctx.instance_id,
                        "status": "FAILED",
                        "reason": "failure edge targets non-existent stage",
                    })
                    continue
                delta.stage_updates[target_stage.stage_instance_id] = {
                    "status": StageStatus.PENDING,
                    "loop_counter": st.loop_counter + 1,
                }
                actions.append({
                    "action": "spawn",
                    "instance_id": ctx.instance_id,
                    "stage_id": failure_edge.to_stage,
                    "reason": "failure-edge",
                })
                continue

            # failure edge 也耗尽
            loop_exceeded_edge = get_loop_exceeded_edge(ctx.adj, stage_id)
            if loop_exceeded_edge:
                target_stage = state.first_stage_by_id(loop_exceeded_edge.to_stage)
                updates = {}
                if target_stage:
                    updates[target_stage.stage_instance_id] = {
                        "status": StageStatus.PENDING,
                    }
                delta = delta.merge(StateDelta(stage_updates=updates))
                actions.append({
                    "action": "spawn",
                    "instance_id": ctx.instance_id,
                    "stage_id": loop_exceeded_edge.to_stage,
                    "reason": "loop-exceeded",
                })
                continue

            # 无可用 handler
            delta.instance_updates["status"] = InstanceStatus.FAILED
            actions.append({
                "action": "terminate",
                "instance_id": ctx.instance_id,
                "status": "FAILED",
                "reason": f"no handler for stage {stage_id} error",
            })

        # 6.5 超时检测
        timeout_delta = self._check_timeouts(ctx, state)
        if timeout_delta:
            delta = delta.merge(timeout_delta)

        return ProcessorResult(state_delta=delta, actions=actions)

    def _check_timeouts(self, ctx: ExecutionContext, state: InstanceState) -> StateDelta | None:
        """检测 RUNNING stage 超时。"""
        from core.atomic_write import atomic_write_json
        from core.project import find_root
        import uuid

        root = find_root()
        stage_spec_map = {s.stage_id: s for s in ctx.spec.stages}
        delta = StateDelta()

        for st in state.stages:
            if st.status != StageStatus.RUNNING:
                continue
            if not st.started_at:
                continue
            stage_spec = stage_spec_map.get(st.stage_id)
            if not stage_spec or not stage_spec.timeout_seconds:
                continue
            try:
                elapsed = time.time() - parse_iso_timestamp(st.started_at)
            except (ValueError, OSError):
                continue
            if elapsed > stage_spec.timeout_seconds:
                stage_id = st.stage_id
                delta.stage_updates[st.stage_instance_id] = {
                    "status": StageStatus.ERROR,
                    "started_at": None,
                }
                # 写入超时消息
                messages_dir = root / ".agent" / "instances" / ctx.instance_id / "messages"
                messages_dir.mkdir(parents=True, exist_ok=True)
                msg_id = f"msg-{uuid.uuid4().hex[:8]}"
                msg = {
                    "schema_version": "3.0.0",
                    "message_id": msg_id,
                    "instance_id": ctx.instance_id,
                    "stage_id": stage_id,
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
                append_deviation(
                    ctx.instance_id,
                    "STAGE_TIMEOUT",
                    f"Stage {stage_id} timed out after {elapsed:.0f}s",
                    stage_id=stage_id,
                )
        return delta if not delta.is_empty() else None
