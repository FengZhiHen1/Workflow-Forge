"""ParallelSplitProcessor：parallel 拆分。

步骤 5：检查 parallel 需求，拆分 stage 为多个并行实例。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from infrastructure.project import find_root
from infrastructure.timestamp import iso_timestamp
from scheduler.context import ExecutionContext
from state.model import InstanceState, StageState, StateDelta, StageStatus
from scheduler.processors.base import ProcessorResult, SideEffect
from services.state_manager import append_deviation as _append_deviation


@dataclass
class ParallelSplitProcessor:
    """处理 parallel 拆分。"""

    def process(self, ctx: ExecutionContext, state: InstanceState) -> ProcessorResult:
        actions: list[dict] = []
        side_effects: list[SideEffect] = []
        delta = StateDelta()

        for stage_spec in ctx.spec.stages:
            if not stage_spec.parallel:
                continue

            source_stage_id = stage_spec.parallel.source
            source_stage = state.first_stage_by_id(source_stage_id)
            if not source_stage or source_stage.status != StageStatus.DONE:
                continue

            # 检查是否已拆分
            existing = [s for s in state.stages if s.stage_id == stage_spec.stage_id and s.fan_out_target]
            if existing:
                continue

            # 检查是否已置为 ERROR
            already_error = any(
                s.stage_id == stage_spec.stage_id and s.status == StageStatus.ERROR
                for s in state.stages
            )
            if already_error:
                continue

            # 获取上游消息的 parallel_targets
            msg_id = source_stage.output_message_id
            if not msg_id:
                reinforce_actions, reinforce_delta = self._handle_missing_targets(
                    state, stage_spec, ctx.instance_id, source_stage_id,
                    "上游 stage 已完成但未产出 output_message_id", side_effects,
                )
                actions.extend(reinforce_actions)
                delta = delta.merge(reinforce_delta)
                continue

            msg_path = ctx.root / ".agent" / "instances" / ctx.instance_id / "messages" / f"{msg_id}.json"
            if not msg_path.exists():
                reinforce_actions, reinforce_delta = self._handle_missing_targets(
                    state, stage_spec, ctx.instance_id, source_stage_id,
                    f"上游 stage 的消息文件 {msg_id}.json 不存在", side_effects,
                )
                actions.extend(reinforce_actions)
                delta = delta.merge(reinforce_delta)
                continue

            side_effects.append(SideEffect(
                kind="file_read", description=f"Read message {msg_id}", execute=None,
            ))
            try:
                msg = json.loads(msg_path.read_text(encoding="utf-8"))
            except Exception:
                reinforce_actions, reinforce_delta = self._handle_missing_targets(
                    state, stage_spec, ctx.instance_id, source_stage_id,
                    f"上游 stage 的消息文件 {msg_id}.json 解析失败", side_effects,
                )
                actions.extend(reinforce_actions)
                delta = delta.merge(reinforce_delta)
                continue

            if "parallel_targets" not in msg:
                reason = (
                    f"上游 stage {source_stage_id} 未产出 parallel_targets"
                    f"（SubAgent 上报时未传 --parallel-targets）"
                )
                reinforce_actions, reinforce_delta = self._handle_missing_targets(
                    state, stage_spec, ctx.instance_id, source_stage_id, reason, side_effects,
                )
                actions.extend(reinforce_actions)
                delta = delta.merge(reinforce_delta)
                continue

            targets = msg.get("parallel_targets", [])
            if not targets:
                continue

            # 有 targets → 清除重试计数，执行拆分
            max_inst = stage_spec.parallel.max_instances
            if max_inst:
                targets = targets[:max_inst]

            new_stages: list[StageState] = []
            for idx, target in enumerate(targets):
                stage_inst_id = f"{stage_spec.stage_id}_{idx}"
                new_stages.append(StageState(
                    stage_id=stage_spec.stage_id,
                    stage_instance_id=stage_inst_id,
                    status=StageStatus.PENDING,
                    model=stage_spec.model,
                    fan_out_target=target,
                ))

            # 移除原有的单 stage PENDING 条目
            delta = delta.merge(StateDelta(
                remove_stage_instance_ids=[
                    s.stage_instance_id for s in state.stages
                    if s.stage_id == stage_spec.stage_id
                    and not s.fan_out_target
                    and s.status == StageStatus.PENDING
                ],
            ))
            delta = delta.merge(StateDelta(append_stages=new_stages))

        return ProcessorResult(state_delta=delta, actions=actions, side_effects=side_effects)

    def _handle_missing_targets(
        self, state: InstanceState, stage_spec, instance_id: str, source_stage_id: str,
        reason: str, side_effects: list[SideEffect],
    ) -> tuple[list[dict], StateDelta]:
        max_retry = 2
        retry_count = self._get_parallel_retry(state, stage_spec.stage_id)

        source_stage = state.first_stage_by_id(source_stage_id)
        source_system_agent_id = source_stage.system_agent_id if source_stage else None

        if source_system_agent_id and retry_count < max_retry:
            new_count = retry_count + 1
            side_effects.append(SideEffect(
                kind="deviation_write",
                description=f"Parallel targets reinforce {stage_spec.stage_id} #{new_count}",
                execute=lambda iid=instance_id, sid=stage_spec.stage_id, nc=new_count, mr=max_retry, src=source_stage_id: _append_deviation(
                    iid, "PARALLEL_TARGETS_REINFORCE",
                    f"stage {sid}: 上游 {src} 未产出 parallel_targets，第 {nc}/{mr} 次强化重试",
                    stage_id=sid,
                ),
            ))
            # 递增 retry count
            for s in state.stages:
                if s.stage_id == stage_spec.stage_id and s.status == StageStatus.PENDING and not s.fan_out_target:
                    delta = StateDelta(stage_updates={
                        s.stage_instance_id: {"parallel_retry_count": new_count}
                    })
                    break
            else:
                delta = StateDelta()
            return [{
                "action": "reinforce",
                "instance_id": instance_id,
                "type": "parallel_targets_missing",
                "stage_id": stage_spec.stage_id,
                "source_stage_id": source_stage_id,
                "system_agent_id": source_system_agent_id,
                "retry_count": new_count,
                "max_retry": max_retry,
                "message": (
                    f"你在 stage {source_stage_id} 的上报中未包含 parallel_targets。"
                    f"请根据 contracts/output.md 中「parallel_targets 规范」补充产出，"
                    f"通过 --parallel-targets 参数重新上报（格式：id:标签:上下文）。"
                    f"这是第 {new_count}/{max_retry} 次提醒，超次将终止工作流。"
                ),
            }], delta

        # SubAgent 已终止或重试耗尽 → 置 ERROR
        # 返回 reinforce action，实际错误处理由 ErrorRecoveryProcessor 在后续步骤处理
        return [], StateDelta()

    def _get_parallel_retry(self, state: InstanceState, stage_id: str) -> int:
        for s in state.stages:
            if s.stage_id == stage_id and s.status == StageStatus.PENDING and not s.fan_out_target:
                return s.parallel_retry_count
        return 0
