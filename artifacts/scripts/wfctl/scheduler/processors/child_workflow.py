"""ChildWorkflowProcessor：子工作流完成检查 + 创建 + 递归调度。

替代 check_children.py。递归调度使用 SchedulerOrchestrator.run()。
子 confirm 挂起项通过 CycleMeta.child_confirm_pending 传递。
"""

from __future__ import annotations

from dataclasses import dataclass

from domain.dag.graph import _any_upstream_satisfied
from infrastructure.lock import FileLock
from infrastructure.project import find_root
from domain.workflow.spec import StageTargetType
from infrastructure.timestamp import iso_timestamp
from compat.instance.registry import load_instance_state, save_instance_state
from scheduler.context import ExecutionContext
from state.model import (
    CycleMeta,
    InstanceState,
    StageState,
    StageStatus,
    StateDelta,
    InstanceStatus,
)
from scheduler.processors.base import ProcessorResult, SideEffect
from services.state_manager import append_deviation as _append_deviation


@dataclass
class ChildWorkflowProcessor:
    """处理子工作流：检查完成、创建子实例、递归调度。"""

    def process(self, ctx: ExecutionContext, state: InstanceState) -> ProcessorResult:
        delta = StateDelta()
        cycle_meta = state.cycle_meta
        actions: list[dict] = []
        side_effects: list[SideEffect] = []

        # 1. 检查子工作流完成状态
        self._check_child_workflows(state, ctx, delta, side_effects)

        # 2. 创建子工作流实例
        self._spawn_child_workflows(state, ctx, delta, side_effects)

        # 3. 递归调度活跃子实例（需合并 delta，确保刚创建的子实例也能被调度）
        merged_state = state.apply_delta(delta)
        child_results = self._recurse_child_instances(merged_state, ctx, side_effects)

        # 4. 递归后二次检查
        self._check_child_workflows(state, ctx, delta, side_effects)

        # 5. 组装 actions 和 cycle_meta
        actions.extend(child_results.get("spawn_continue", []))
        actions.extend(child_results.get("retry", []))
        actions.extend(child_results.get("reinforce", []))
        actions.extend(child_results.get("error", []))
        actions.extend(child_results.get("conflict", []))
        actions.extend(child_results.get("merge_conflict", []))
        actions.extend(child_results.get("terminate", []))

        cycle_meta = CycleMeta(
            newly_done_stage_instance_ids=cycle_meta.newly_done_stage_instance_ids,
            newly_error_stage_instance_ids=cycle_meta.newly_error_stage_instance_ids,
            newly_awaiting_confirm_ids=cycle_meta.newly_awaiting_confirm_ids,
            ready_candidates=cycle_meta.ready_candidates,
            child_confirm_pending=child_results.get("confirm_pending", []),
        )

        final_delta = StateDelta(
            stage_updates=delta.stage_updates,
            instance_updates=delta.instance_updates,
            append_stages=delta.append_stages,
            remove_stage_instance_ids=delta.remove_stage_instance_ids,
            cycle_meta=cycle_meta,
        )
        return ProcessorResult(state_delta=final_delta, actions=actions, side_effects=side_effects)

    def _check_child_workflows(
        self, state: InstanceState, ctx: ExecutionContext, delta: StateDelta,
        side_effects: list[SideEffect],
    ) -> None:
        """检查 RUNNING WORKFLOW stage 的子实例状态。"""
        for st in state.stages:
            if st.status != StageStatus.RUNNING:
                continue
            if not st.child_instance_id:
                continue
            try:
                child_state = load_instance_state(st.child_instance_id)
                side_effects.append(SideEffect(
                    kind="file_read", description=f"Load child state {st.child_instance_id}", execute=None,
                ))
            except Exception:
                continue

            if child_state.status == InstanceStatus.COMPLETED:
                delta.stage_updates[st.stage_instance_id] = {
                    "status": StageStatus.DONE,
                    "exit_condition": "success",
                }
            elif child_state.status == InstanceStatus.FAILED:
                delta.stage_updates[st.stage_instance_id] = {"status": StageStatus.ERROR}

    def _spawn_child_workflows(
        self, state: InstanceState, ctx: ExecutionContext, delta: StateDelta,
        side_effects: list[SideEffect],
    ) -> None:
        """为 PENDING WORKFLOW stage 创建子实例。"""
        from runtime.worktree.git import git_rev_parse
        from services.creator import create_instance as _create_child

        root = find_root()
        stage_specs = {s.stage_id: s for s in ctx.spec.stages}
        inst_wt = root / ".tmp" / "worktrees" / f"instance-{ctx.instance_id}"

        rc, head_ref, _ = git_rev_parse(inst_wt, "HEAD")
        side_effects.append(SideEffect(
            kind="git_read", description="Git rev-parse for child base ref", execute=None,
        ))
        base_ref = head_ref.strip() if rc == 0 else "HEAD"

        for st in state.stages:
            if st.status != StageStatus.PENDING:
                continue
            stage_spec = stage_specs.get(st.stage_id)
            if not stage_spec or stage_spec.target_type != StageTargetType.WORKFLOW:
                continue

            upstream_edges = ctx.adj.incoming.get(st.stage_id, [])
            if not _any_upstream_satisfied(upstream_edges, state, ctx.adj):
                continue

            wf_ref = stage_spec.target
            if "@" in wf_ref:
                child_wf_id, child_version = wf_ref.split("@", 1)
            else:
                child_wf_id, child_version = wf_ref, None

            fan_out = st.fan_out_target or {}
            goal_parts = [fan_out.get("label", st.stage_id)]
            if fan_out.get("context"):
                goal_parts.append(fan_out["context"])
            child_goal = "：".join(goal_parts)

            child_state = _create_child(
                workflow_id=child_wf_id,
                version=child_version,
                goal=child_goal,
                parent_instance_id=ctx.instance_id,
                worktree_base_ref=base_ref,
            )
            side_effects.append(SideEffect(
                kind="worktree_create", description=f"Create child instance {child_state.instance_id}", execute=None,
            ))
            side_effects.append(SideEffect(
                kind="json_write", description=f"Save child instance {child_state.instance_id}", execute=None,
            ))

            delta.stage_updates[st.stage_instance_id] = {
                "child_instance_id": child_state.instance_id,
                "status": StageStatus.RUNNING,
                "started_at": iso_timestamp(),
            }

    def _recurse_child_instances(
        self, state: InstanceState, ctx: ExecutionContext, side_effects: list[SideEffect],
    ) -> dict[str, list[dict]]:
        """递归调度所有活跃子工作流实例。"""
        from scheduler.orchestrator import SchedulerOrchestrator

        root = find_root()
        result: dict[str, list[dict]] = {
            "spawn_continue": [],
            "retry": [],
            "reinforce": [],
            "confirm_pending": [],
            "error": [],
            "conflict": [],
            "merge_conflict": [],
            "terminate": [],
        }

        for st in state.stages:
            child_id = st.child_instance_id
            if not child_id:
                continue
            if st.status != StageStatus.RUNNING:
                continue

            try:
                child_state = load_instance_state(child_id)
                side_effects.append(SideEffect(
                    kind="file_read", description=f"Load child state {child_id} for recurse", execute=None,
                ))
            except Exception:
                continue

            if child_state.status != InstanceStatus.ACTIVE:
                continue

            child_lock_path = root / ".agent" / "instances" / child_id / "instance.json"
            child_lock = FileLock(child_lock_path)
            if not child_lock.acquire(timeout=10.0):
                side_effects.append(SideEffect(
                    kind="deviation_write",
                    description=f"Child lock failed for {child_id}",
                    execute=lambda iid=ctx.instance_id, cid=child_id, sid=st.stage_id: _append_deviation(
                        iid, "CHILD_LOCK_FAILED",
                        f"Could not acquire lock for child instance {cid}",
                        stage_id=sid,
                    ),
                ))
                continue

            try:
                from domain.dag.graph import build_adjacency
                from compat.workflow.registry import load_workflow
                from services.resolver import find_workflow_dir

                child_wf_dir = find_workflow_dir(
                    child_state.workflow_id,
                    child_state.version if child_state.version else None,
                )
                child_spec = load_workflow(child_wf_dir / "WORKFLOW.yaml")
                child_adj = build_adjacency(child_spec)

                child_ctx = ExecutionContext(
                    instance_id=child_id,
                    root=root,
                    spec=child_spec,
                    adj=child_adj,
                    worktree_map={},
                )

                orchestrator = SchedulerOrchestrator()
                side_effects.append(SideEffect(
                    kind="file_read", description=f"Acquire lock for child {child_id}", execute=None,
                ))
                child_result = orchestrator.run(child_ctx, child_state)
                side_effects.append(SideEffect(
                    kind="git_merge", description=f"Recursive orchestration for child {child_id}", execute=None,
                ))

                if child_result.get("status") != "ok":
                    continue

                # 持久化子实例状态变更，避免递归编排后的 stage 变更丢失
                save_instance_state(child_id, child_result["_state"])

                for action in child_result.get("actions", []):
                    action_type = action.get("action")
                    if action_type in ("spawn", "continue"):
                        result["spawn_continue"].append(action)
                    elif action_type == "retry":
                        result["retry"].append(action)
                    elif action_type == "reinforce":
                        result["reinforce"].append(action)
                    elif action_type == "confirm":
                        result["confirm_pending"].extend(action.get("pending", []))
                    elif action_type == "conflict":
                        if action.get("source_stage"):
                            result["merge_conflict"].append(action)
                        else:
                            result["conflict"].append(action)
                    elif action_type == "terminate":
                        result["terminate"].append(action)
                    elif action_type in ("error",):
                        result["error"].append(action)
            finally:
                child_lock.release()

        return result
