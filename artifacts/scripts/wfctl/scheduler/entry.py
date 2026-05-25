"""wfctl 调度入口：run_next / run_sync。

替代 scheduler_legacy.py，使用编排器作为唯一调度路径。
"""

from __future__ import annotations

from pathlib import Path

from domain.dag.graph import build_adjacency
from infrastructure.lock import FileLock
from infrastructure.project import find_root
from compat.workflow.registry import load_workflow
from compat.instance.registry import load_instance_state, save_instance_state
from scheduler.context import ExecutionContext
from state.model import InstanceState, InstanceStatus
from runtime.worktree.manager import sync_instance_with_main, sync_instance_with_parent


def run_next(instance_id: str) -> dict:
    """调度核心：消费消息，推进状态，返回 actions。

    使用编排器作为唯一调度路径，运行后保存最终状态。
    """
    root = find_root()
    lock_path = root / ".agent" / "instances" / instance_id / "instance.json"
    lock = FileLock(lock_path)

    if not lock.acquire(timeout=15.0):
        from infrastructure.errors import StateError
        raise StateError("Could not acquire instance lock", code="STATE_LOCKED")

    try:
        return _run_next_inner(instance_id)
    finally:
        lock.release()


def _run_next_inner(instance_id: str) -> dict:
    """单实例调度核心（不含顶层锁管理，供递归调用）。"""
    root = find_root()
    state = load_instance_state(instance_id)

    if state.status != InstanceStatus.ACTIVE:
        return {"status": "error", "reason": f"Instance is {state.status.value}"}

    # 0. 同步 worktree
    _sync_worktree_upstream(instance_id, state)

    # 1. 加载 spec 和邻接表
    spec = _load_workflow_for_instance(state)
    adj = build_adjacency(spec)
    worktree_map = _build_worktree_map(instance_id, state)

    # 2. 构建上下文并运行编排器
    ctx = ExecutionContext(
        instance_id=instance_id,
        root=root,
        spec=spec,
        adj=adj,
        worktree_map=worktree_map,
    )

    from scheduler.orchestrator import SchedulerOrchestrator
    result = SchedulerOrchestrator().run(ctx, state)

    # 3. 保存最终状态
    final_state = result["_state"]
    save_instance_state(instance_id, final_state)

    return {"status": "ok", "actions": result["actions"]}


def run_sync(instance_id: str) -> dict:
    """仅消费消息、更新状态，不计算 actions。"""
    root = find_root()
    lock_path = root / ".agent" / "instances" / instance_id / "instance.json"
    lock = FileLock(lock_path)

    if not lock.acquire(timeout=15.0):
        from infrastructure.errors import StateError
        raise StateError("Could not acquire instance lock", code="STATE_LOCKED")

    try:
        state = load_instance_state(instance_id)
        spec = _load_workflow_for_instance(state)
        adj = build_adjacency(spec)
        worktree_map = _build_worktree_map(instance_id, state)

        # 同步
        _sync_worktree_upstream(instance_id, state)

        # 仅运行消息消费 + 状态转换
        from scheduler.processors import ConsumeMessagesProcessor, StateTransitionProcessor

        initial_consumed = frozenset(state.consumed_message_ids)

        ctx = ExecutionContext(
            instance_id=instance_id,
            root=root,
            spec=spec,
            adj=adj,
            worktree_map=worktree_map,
        )

        consumer = ConsumeMessagesProcessor()
        result = consumer.process(ctx, state)
        state = state.apply_delta(result.state_delta)

        transition = StateTransitionProcessor()
        result = transition.process(ctx, state)
        state = state.apply_delta(result.state_delta)

        # 收集新消费的消息 ID 作为 changes
        new_consumed = state.consumed_message_ids - initial_consumed
        changes = [{"message_id": mid} for mid in new_consumed]

        save_instance_state(instance_id, state)

        return {"status": "ok", "changes": changes}
    finally:
        lock.release()


# ── 内部辅助 ──

def _sync_worktree_upstream(instance_id: str, state: InstanceState) -> None:
    """同步实例 worktree 与上游。"""
    from services.state_manager import append_deviation

    if state.parent_instance_id:
        result = sync_instance_with_parent(instance_id, state.parent_instance_id)
    else:
        result = sync_instance_with_main(instance_id)

    if not result.success:
        append_deviation(instance_id, "SYNC_SKIPPED", result.message)


def _load_workflow_for_instance(state: InstanceState):
    """加载工作流 spec。"""
    from services.resolver import find_workflow_dir

    version = state.version
    wf_dir = find_workflow_dir(state.workflow_id, version if version else None)
    return load_workflow(wf_dir / "WORKFLOW.yaml")


def _build_worktree_map(instance_id: str, state: InstanceState) -> dict[str, Path]:
    """构建 stage_id → worktree 路径映射。"""
    root = find_root()
    wt_map: dict[str, Path] = {}
    inst_wt = root / ".tmp" / "worktrees" / f"instance-{instance_id}"

    for st in state.stages:
        stage_wt = root / ".tmp" / "worktrees" / f"stage-{instance_id}-{st.stage_instance_id}"
        if stage_wt.exists():
            wt_map[st.stage_id] = stage_wt
        else:
            wt_map[st.stage_id] = inst_wt

    return wt_map
