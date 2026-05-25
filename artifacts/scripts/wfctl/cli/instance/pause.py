"""pause 命令——暂停活跃实例，重置运行中 stage。

决策委托给 TransitionPolicy.on_pause()。
"""

from infrastructure.errors import StateError
from domain.transition.policy import TransitionPolicy
from compat.instance.registry import load_instance_state, save_instance_state
from services.state_manager import append_deviation
from state.timeline import append_timeline


def register_pause(subparsers):
    p = subparsers.add_parser("pause", help="暂停活跃实例")
    p.add_argument("--instance", required=True, help="实例 ID")
    p.add_argument("--reason", default="User requested pause", help="暂停原因")
    p.set_defaults(handler=_handle_pause)


def _handle_pause(args) -> dict:
    state = load_instance_state(args.instance)

    if state.status.value == "COMPLETED":
        raise StateError("Instance already completed")
    if state.status.value == "FAILED":
        raise StateError("Instance already terminated")
    if state.status.value == "PAUSED":
        raise StateError("Instance already paused")
    if state.status.value != "ACTIVE":
        raise StateError(f"Cannot pause instance in status: {state.status.value}")

    # 纯决策
    delta = TransitionPolicy.on_pause(state)

    reset_stages = [sid for sid in delta.stage_updates]
    reset_stage_ids = [
        state.stage_by_instance_id(sid).stage_id
        for sid in reset_stages
        if state.stage_by_instance_id(sid)
    ]

    # 应用
    new_state = state.apply_delta(delta)

    # ── 副作用区 ──
    append_timeline(args.instance, "", "instance→paused", {
        "reason": args.reason,
        "reset_stages": reset_stage_ids,
    })
    append_deviation(args.instance, "INSTANCE_PAUSED", args.reason)

    save_instance_state(args.instance, new_state)

    return {"status": "ok", "instance_id": args.instance, "reset_stages": reset_stage_ids}
