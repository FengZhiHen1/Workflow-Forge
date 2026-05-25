"""skip 命令——跳过指定 stage，直接标记为 DONE。

决策委托给 TransitionPolicy.on_skip()，副作用（锚点、deviation）保留在 handler。
"""

from domain.dag.graph import build_adjacency
from infrastructure.errors import InputError, StateError
from compat.workflow.registry import load_workflow
from domain.transition.policy import TransitionPolicy
from compat.instance.registry import load_instance_state, save_instance_state
from services.state_manager import append_deviation
from state.timeline import append_timeline
from runtime.worktree.manager import tag_anchor


def register_skip(subparsers):
    p = subparsers.add_parser("skip", help="跳过指定 stage（标记为 DONE）")
    p.add_argument("--instance", required=True, help="实例 ID")
    p.add_argument("--stage", required=True, help="目标 stage_id")
    p.add_argument("--reason", default="Manually skipped", help="跳过原因")
    p.add_argument(
        "--force",
        action="store_true",
        help="强制跳过任意非终态 stage（RUNNING / AWAITING_CONFIRM / ERROR）",
    )
    p.set_defaults(handler=_handle_skip)


def _handle_skip(args) -> dict:
    state = load_instance_state(args.instance)

    if state.status.value == "COMPLETED":
        raise StateError("Instance already completed")
    if state.status.value == "FAILED":
        raise StateError("Instance already terminated")

    stage_id = args.stage

    # 加载 spec（获取 anchor_prefix）
    from services.resolver import find_workflow_dir
    version = state.version
    wf_dir = find_workflow_dir(state.workflow_id, version if version else None)
    spec = load_workflow(wf_dir / "WORKFLOW.yaml")

    # 纯决策
    policy = TransitionPolicy.from_adjacency(build_adjacency(spec), stage_id)
    result = policy.on_skip(state, args.force)

    # 隔离未消费消息
    from runtime.message.handler import scan_messages
    consumed = set(state.consumed_message_ids) | set(result.blocked_message_ids)
    pending_msgs = scan_messages(args.instance, consumed)
    blocked_ids = {m["message_id"] for m in pending_msgs if m.get("stage_id") == stage_id}
    if blocked_ids:
        consumed.update(blocked_ids)

    # 合并 delta：跳过 + 消息隔离
    from state.model import StateDelta
    combined = result.state_delta
    if blocked_ids:
        combined = combined.merge(StateDelta(
            instance_updates={"consumed_message_ids": frozenset(consumed)},
        ))

    # 应用状态变更
    new_state = state.apply_delta(combined)

    # ── 副作用区 ──
    for s_inst_id in result.stage_instance_ids:
        tag_anchor(args.instance, f"{spec.anchor_prefix}-{args.instance}-{s_inst_id}")
        append_timeline(
            args.instance, stage_id,
            f"skipped{' force' if result.force_applied else ''}",
            {"reason": args.reason, "stage_instance_id": s_inst_id},
        )

    append_deviation(
        args.instance,
        "STAGE_SKIPPED_FORCE" if result.force_applied else "STAGE_SKIPPED",
        args.reason,
        stage_id=stage_id,
    )

    save_instance_state(args.instance, new_state)

    return {
        "status": "ok",
        "stage_id": stage_id,
        "instances_skipped": len(result.stage_instance_ids),
        "forced": result.force_applied,
        "reason": args.reason,
    }
