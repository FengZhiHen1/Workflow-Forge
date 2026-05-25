"""rollback 命令——回退到指定 stage 锚点。

决策委托给 TransitionPolicy.on_rollback()，副作用（Git 操作）保留在 handler。
"""

from domain.dag.graph import build_adjacency
from infrastructure.errors import InputError
from infrastructure.project import find_root
from compat.workflow.registry import load_workflow
from domain.transition.policy import TransitionPolicy
from compat.instance.registry import load_instance_state, save_instance_state
from state.timeline import append_timeline
from runtime.worktree.manager import checkout_to_anchor, remove_anchor


def register_rollback(subparsers):
    p = subparsers.add_parser("rollback", help="回退到指定 stage 锚点")
    p.add_argument("--instance", required=True, help="实例 ID")
    p.add_argument("--stage", required=True, help="目标 stage_id")
    p.set_defaults(handler=_handle_rollback)


def _handle_rollback(args) -> dict:
    state = load_instance_state(args.instance)
    stage_id = args.stage

    if state.status.value == "COMPLETED":
        raise InputError("Instance already merged to main", code="INVALID_ARGUMENT")
    if state.status.value == "FAILED":
        raise InputError("Instance already terminated", code="INVALID_ARGUMENT")

    # 加载 spec 和邻接表
    from services.resolver import find_workflow_dir
    version = state.version
    wf_dir = find_workflow_dir(state.workflow_id, version if version else None)
    spec = load_workflow(wf_dir / "WORKFLOW.yaml")
    adj = build_adjacency(spec)

    # 校验锚点存在
    root = find_root()
    from runtime.worktree.git import git_rev_parse
    inst_wt = root / ".tmp" / "worktrees" / f"instance-{args.instance}"
    if not inst_wt.exists():
        raise InputError("Instance worktree not found", code="INVALID_ARGUMENT")
    anchor_name = f"{spec.anchor_prefix}-{args.instance}-{stage_id}"
    rc, _, _ = git_rev_parse(inst_wt, anchor_name)
    if rc != 0:
        raise InputError(f"No anchor for stage {stage_id}", code="STAGE_NOT_FOUND")

    # 纯决策
    policy = TransitionPolicy.from_adjacency(adj, stage_id)
    result = policy.on_rollback(state, adj)

    # 应用状态变更
    new_state = state.apply_delta(result.state_delta)

    # ── 副作用区 ──
    checkout_to_anchor(args.instance, anchor_name)

    for s_id in result.reset_stage_ids:
        anchor = f"{spec.anchor_prefix}-{args.instance}-{s_id}"
        remove_anchor(args.instance, anchor)

    append_timeline(args.instance, stage_id, "rollback", {"reset_stages": result.reset_stage_ids})

    save_instance_state(args.instance, new_state)

    return {
        "status": "ok",
        "reset_stages": result.reset_stage_ids,
        "worktree": str(inst_wt.relative_to(root)),
    }
