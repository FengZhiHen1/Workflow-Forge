"""CheckChildrenProcessor：子工作流完成检查 + 创建 + 递归调度。

步骤 4, 5.5, 5.6：检查子实例状态、创建新子实例、递归调度。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from core.dag import _any_upstream_satisfied
from core.lock import FileLock
from core.project import find_root
from core.schema.interface import StageTargetType
from core.timestamp import iso_timestamp
from services.scheduler.context import ExecutionContext
from services.scheduler.state_model import InstanceState, StageState, StateDelta, StageStatus
from services.scheduler.processors.base import ProcessorResult
from services.state_manager import append_deviation, load_instance
from services.worktree_manager import tag_anchor


@dataclass
class CheckChildrenProcessor:
    """处理子工作流。"""

    def process(self, ctx: ExecutionContext, state: InstanceState) -> ProcessorResult:
        delta = StateDelta()
        child_results = {
            "spawn_continue": [],
            "retry": [],
            "reinforce": [],
            "confirm_pending": [],
            "error": [],
            "conflict": [],
            "merge_conflict": [],
            "terminate": [],
        }

        # 步骤 4：检查子工作流完成状态
        self._check_child_workflows(state, ctx, delta)

        # 步骤 5.5：子工作流实例创建
        self._spawn_child_workflows(state, ctx, delta)

        # 步骤 5.6：递归处理所有活跃子实例
        child_results = self._recurse_child_instances(state, ctx)

        # 步骤 5.6.1：子实例递归后重新检查完成状态
        self._check_child_workflows(state, ctx, delta)

        # 将 child_results 转换为 actions
        actions: list[dict] = []
        actions.extend(child_results.get("spawn_continue", []))
        actions.extend(child_results.get("retry", []))
        actions.extend(child_results.get("reinforce", []))
        actions.extend(child_results.get("error", []))
        actions.extend(child_results.get("conflict", []))
        actions.extend(child_results.get("merge_conflict", []))
        actions.extend(child_results.get("terminate", []))

        # confirm_pending 由 ConfirmAggregateProcessor 统一处理
        ctx.extra["child_confirm_pending"] = child_results.get("confirm_pending", [])

        return ProcessorResult(state_delta=delta, actions=actions)

    def _check_child_workflows(
        self, state: InstanceState, ctx: ExecutionContext, delta: StateDelta
    ) -> None:
        """检查 RUNNING WORKFLOW stage 的子实例状态。"""
        root = find_root()
        for st in state.stages:
            if st.status != StageStatus.RUNNING:
                continue
            if not st.child_instance_id:
                continue
            child_path = root / ".agent" / "instances" / st.child_instance_id / "instance.json"
            if not child_path.exists():
                continue
            try:
                child = json.loads(child_path.read_text(encoding="utf-8"))
                if child.get("status") == "COMPLETED":
                    delta.stage_updates[st.stage_instance_id] = {
                        "status": StageStatus.DONE,
                        "exit_condition": "success",
                    }
                elif child.get("status") == "FAILED":
                    delta.stage_updates[st.stage_instance_id] = {"status": StageStatus.ERROR}
            except Exception:
                pass

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

            delta.stage_updates[st.stage_id] = {
                "child_instance_id": child_result["instance_id"],
                "status": StageStatus.RUNNING,
                "started_at": iso_timestamp(),
            }

    def _recurse_child_instances(
        self, state: InstanceState, ctx: ExecutionContext
    ) -> dict[str, list[dict]]:
        """递归处理所有活跃子工作流实例。"""
        from services.scheduler_legacy import _run_next_inner

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
                child_instance = load_instance(child_id)
            except Exception:
                continue

            if child_instance.get("status") != "ACTIVE":
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
                child_result = _run_next_inner(child_id)
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
