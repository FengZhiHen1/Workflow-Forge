"""ChildWorkflowProcessor：子工作流完成检查 + 创建 + 递归调度。

替代 check_children.py。递归调度使用 SchedulerOrchestrator.run()。
子 confirm 挂起项通过 CycleMeta.child_confirm_pending 传递。
"""

from __future__ import annotations

from dataclasses import dataclass

from core.dag import _any_upstream_satisfied
from core.lock import FileLock
from core.project import find_root
from core.schema.interface import StageTargetType
from core.timestamp import iso_timestamp
from state.persistence import load_instance_state
from services.scheduler.context import ExecutionContext
from services.scheduler.state_model import (
    CycleMeta,
    InstanceState,
    StageState,
    StageStatus,
    StateDelta,
    InstanceStatus,
)
from services.scheduler.processors.base import ProcessorResult
from services.state_manager import append_deviation


@dataclass
class ChildWorkflowProcessor:
    """处理子工作流：检查完成、创建子实例、递归调度。"""

    def process(self, ctx: ExecutionContext, state: InstanceState) -> ProcessorResult:
        delta = StateDelta()
        cycle_meta = state.cycle_meta
        actions: list[dict] = []

        # 1. 检查子工作流完成状态
        self._check_child_workflows(state, ctx, delta)

        # 2. 创建子工作流实例
        self._spawn_child_workflows(state, ctx, delta)

        # 3. 递归调度活跃子实例
        child_results = self._recurse_child_instances(state, ctx)

        # 4. 递归后二次检查
        self._check_child_workflows(state, ctx, delta)

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

        delta.cycle_meta = cycle_meta
        return ProcessorResult(state_delta=delta, actions=actions)

    def _check_child_workflows(
        self, state: InstanceState, ctx: ExecutionContext, delta: StateDelta
    ) -> None:
        """检查 RUNNING WORKFLOW stage 的子实例状态。"""
        for st in state.stages:
            if st.status != StageStatus.RUNNING:
                continue
            if not st.child_instance_id:
                continue
            try:
                child_state = load_instance_state(st.child_instance_id)
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
        self, state: InstanceState, ctx: ExecutionContext, delta: StateDelta
    ) -> None:
        """为 PENDING WORKFLOW stage 创建子实例。"""
        from core.git_ops import git_rev_parse
        from services.creator import create_instance as _create_child

        root = find_root()
        stage_specs = {s.stage_id: s for s in ctx.spec.stages}
        inst_wt = root / ".tmp" / "worktrees" / f"instance-{ctx.instance_id}"

        rc, head_ref, _ = git_rev_parse(inst_wt, "HEAD")
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

            child_result = _create_child(
                workflow_id=child_wf_id,
                version=child_version,
                goal=child_goal,
                parent_instance_id=ctx.instance_id,
                worktree_base_ref=base_ref,
            )

            delta.stage_updates[st.stage_instance_id] = {
                "child_instance_id": child_result["instance_id"],
                "status": StageStatus.RUNNING,
                "started_at": iso_timestamp(),
            }

    def _recurse_child_instances(
        self, state: InstanceState, ctx: ExecutionContext
    ) -> dict[str, list[dict]]:
        """递归调度所有活跃子工作流实例。"""
        from services.scheduler.orchestrator import SchedulerOrchestrator

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
            except Exception:
                continue

            if child_state.status != InstanceStatus.ACTIVE:
                continue

            child_lock_path = root / ".agent" / "instances" / child_id / "instance.json"
            child_lock = FileLock(child_lock_path)
            if not child_lock.acquire(timeout=10.0):
                append_deviation(
                    ctx.instance_id, "CHILD_LOCK_FAILED",
                    f"Could not acquire lock for child instance {child_id}",
                    stage_id=st.stage_id,
                )
                continue

            try:
                from core.dag import build_adjacency
                from core.schema.loader import load_workflow
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
                child_result = orchestrator.run(child_ctx, child_state)

                if child_result.get("status") != "ok":
                    continue

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
