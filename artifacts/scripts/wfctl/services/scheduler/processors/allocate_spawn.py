"""AllocateSpawnProcessor：worktree 分配 + spawn/continue action 生成。

步骤 11：为就绪 stage 分配 worktree 并生成 spawn/continue action。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from core.dag import AdjacencyList
from core.project import find_root
from core.schema.interface import StageSpec, StageTargetType
from core.timestamp import iso_timestamp
from services.scheduler.context import ExecutionContext
from services.scheduler.state_model import InstanceState, StageState, StateDelta, StageStatus
from services.scheduler.processors.base import ProcessorResult
from services.worktree_manager import (
    create_parallel_worktree,
    create_stage_worktree,
    sync_stage_with_instance,
)


@dataclass
class AllocateSpawnProcessor:
    """为就绪 stage 分配 worktree 并生成 action。"""

    def process(self, ctx: ExecutionContext, state: InstanceState) -> ProcessorResult:
        from services.scheduler_legacy import _load_running_agents

        ready = ctx.extra.get("ready_stage_ids", [])
        if not ready:
            return ProcessorResult()

        running_agents = _load_running_agents(ctx.instance_id)
        actions: list[dict] = []
        delta = StateDelta()
        stage_map = state.stage_map()
        multi_ready = len(ready) > 1

        for stage_id in ready:
            stage_spec = ctx.adj.stages.get(stage_id)
            if not stage_spec:
                continue

            if stage_spec.target_type == StageTargetType.VIRTUAL:
                st = stage_map.get(stage_id)
                if st:
                    delta.stage_updates[stage_id] = {"status": StageStatus.DONE}
                continue

            if stage_spec.target_type == StageTargetType.WORKFLOW:
                continue

            st = stage_map.get(stage_id)
            if not st:
                continue

            stage_inst_id = st.stage_instance_id
            is_parallel = stage_inst_id != stage_id or st.fan_out_target

            skill_id = stage_spec.target
            matched_agent = None
            if not is_parallel:
                matched_agent = self._lookup_running_agent(running_agents, skill_id)

            # worktree 分配
            if multi_ready or is_parallel:
                if self._is_parallel_instance(stage_inst_id):
                    base_id, idx_str = stage_inst_id.rsplit("_", 1)
                    worktree = create_parallel_worktree(ctx.instance_id, base_id, int(idx_str))
                else:
                    worktree = create_stage_worktree(ctx.instance_id, stage_inst_id)
            else:
                worktree = ctx.root / ".tmp" / "worktrees" / f"instance-{ctx.instance_id}"

            # 更新 stage 状态
            updates = {
                "status": StageStatus.RUNNING,
                "started_at": iso_timestamp(),
            }

            # 构建 context
            context = self._build_context(stage_id, ctx.adj, state, stage_spec)

            needs_targets = any(
                s.parallel and s.parallel.source == stage_id
                for s in ctx.spec.stages
            )
            updates["requires_parallel_targets"] = needs_targets

            routing_choices = self._collect_success_choices(ctx.adj, stage_id)
            updates["valid_routing_choices"] = routing_choices

            if matched_agent:
                # Level 2 同步
                sync_ok, conflict_files = sync_stage_with_instance(ctx.instance_id, stage_inst_id)
                if not sync_ok:
                    updates["status"] = StageStatus.CONFLICT
                    updates["conflict_files"] = conflict_files
                    delta.stage_updates[stage_id] = updates
                    actions.append({
                        "action": "conflict",
                        "instance_id": ctx.instance_id,
                        "stage_id": stage_id,
                        "worktree": str(worktree.relative_to(ctx.root)),
                        "conflict_files": conflict_files,
                        "source_stage": stage_id,
                    })
                    continue

                # 同 Skill 延续
                prev_stage_id = matched_agent["stage_id"]
                prev_stage = stage_map.get(prev_stage_id)
                if prev_stage:
                    delta.stage_updates[prev_stage_id] = {"continued_to": stage_id}

                sys_id = matched_agent["system_agent_id"]
                updates["system_agent_id"] = sys_id
                delta.stage_updates[stage_id] = updates

                self._save_running_agent(ctx.instance_id, skill_id, sys_id, stage_id)

                actions.append({
                    "action": "continue",
                    "instance_id": ctx.instance_id,
                    "stage_id": stage_id,
                    "skill_id": skill_id,
                    "worktree": str(worktree.relative_to(ctx.root)),
                    "system_agent_id": sys_id,
                    "requires_parallel_targets": needs_targets,
                    "confirmation_point": stage_spec.confirmation_point,
                    "valid_routing_choices": routing_choices,
                    "context": context,
                })
            else:
                delta.stage_updates[stage_id] = updates
                actions.append({
                    "action": "spawn",
                    "instance_id": ctx.instance_id,
                    "stage_id": stage_id,
                    "skill_id": skill_id,
                    "worktree": str(worktree.relative_to(ctx.root)),
                    "requires_parallel_targets": needs_targets,
                    "confirmation_point": stage_spec.confirmation_point,
                    "valid_routing_choices": routing_choices,
                    "context": context,
                })

        return ProcessorResult(state_delta=delta, actions=actions)

    def _is_parallel_instance(self, stage_inst_id: str) -> bool:
        parts = stage_inst_id.rsplit("_", 1)
        return len(parts) == 2 and parts[1].isdigit()

    def _lookup_running_agent(self, running_agents: list[dict], skill_id: str) -> dict | None:
        for agent in running_agents:
            if agent.get("skill_id") == skill_id:
                return agent
        return None

    def _save_running_agent(self, instance_id: str, skill_id: str, system_agent_id: str, stage_id: str) -> None:
        from core.atomic_write import atomic_write_json
        root = find_root()
        path = root / ".agent" / "running_agents.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        agents: list[dict] = []
        if path.exists():
            try:
                agents = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                pass
        agents = [a for a in agents if a.get("system_agent_id") != system_agent_id]
        agents.append({
            "skill_id": skill_id,
            "system_agent_id": system_agent_id,
            "stage_id": stage_id,
            "instance_id": instance_id,
        })
        atomic_write_json(path, agents)

    def _build_context(self, stage_id: str, adj: AdjacencyList, state: InstanceState, stage_spec: StageSpec) -> dict:
        upstream_summaries = []
        for edge in adj.incoming.get(stage_id, []):
            upstream_stage = next((s for s in state.stages if s.stage_id == edge.from_stage), None)
            if upstream_stage and upstream_stage.output_message_id:
                upstream_summaries.append({
                    "stage_id": edge.from_stage,
                    "message_id": upstream_stage.output_message_id,
                })
        return {
            "upstream": upstream_summaries,
            "stage_name": stage_spec.name or stage_id,
            "successor_stages_block": self._build_successor_stages_block(stage_id, stage_spec, adj),
        }

    def _build_successor_stages_block(self, stage_id: str, stage_spec: StageSpec, adj: AdjacencyList) -> str:
        current_skill_id = stage_spec.target
        current_name = stage_spec.name or stage_id
        successors: list[tuple[str, str, str | None]] = []
        for edge in adj.outgoing.get(stage_id, []):
            if edge.condition.value in ("success", "always"):
                succ_spec = adj.stages.get(edge.to_stage)
                if succ_spec and succ_spec.target_type != StageTargetType.VIRTUAL:
                    successors.append((edge.to_stage, succ_spec.name or edge.to_stage, succ_spec.target))
        if not successors:
            return "你是工作流的最后一个 stage，需产出最终交付物。完成后上报 DONE。"
        lines = [
            "本 stage 下游还有以下 stage，它们将由编排器独立调度，**不属于你的职责范围**："
        ]
        for succ_id, succ_name, succ_skill in successors:
            if succ_skill == current_skill_id:
                lines.append(
                    f"  - {succ_id}「{succ_name}」⚠️ 与你使用同一 Skill——"
                    f"你只需执行 Skill 中属于「{current_name}」的部分"
                )
            else:
                lines.append(f"  - {succ_id}「{succ_name}」")
        lines.append("")
        lines.append(
            f"你只需完成「{current_name}」的工作，产出 checkpoint，"
            f"然后上报 DONE / AWAITING_CONFIRM 并停止。"
            f"后续 stage 由编排器根据你的 checkpoint 和路由选择独立调度。"
        )
        return "\n".join(lines)

    def _collect_success_choices(self, adj: AdjacencyList, stage_id: str) -> list[str]:
        choices: list[str] = []
        for e in adj.outgoing.get(stage_id, []):
            if e.condition.value == "success" and e.choice and e.choice not in choices:
                choices.append(e.choice)
        return choices
