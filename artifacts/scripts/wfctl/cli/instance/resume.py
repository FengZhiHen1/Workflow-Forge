"""resume 命令——恢复暂停的实例。

决策委托给 TransitionPolicy.on_resume()。
"""

from infrastructure.errors import StateError
from domain.transition.policy import TransitionPolicy
from compat.instance.registry import load_instance_state, save_instance_state
from services.state_manager import append_deviation
from state.timeline import append_timeline


def register_resume(subparsers):
    p = subparsers.add_parser("resume", help="恢复暂停的实例")
    p.add_argument("--instance", required=True, help="实例 ID")
    p.set_defaults(handler=_handle_resume)


def _handle_resume(args) -> dict:
    state = load_instance_state(args.instance)

    if state.status.value == "COMPLETED":
        raise StateError("Instance already completed")
    if state.status.value == "FAILED":
        raise StateError("Instance already terminated")
    if state.status.value == "ACTIVE":
        raise StateError("Instance is already active")
    if state.status.value != "PAUSED":
        raise StateError(f"Cannot resume instance in status: {state.status.value}")

    # 纯决策
    delta = TransitionPolicy.on_resume(state)

    # 应用
    new_state = state.apply_delta(delta)

    # ── 副作用区 ──
    append_timeline(args.instance, "", "instance→active (resumed)")
    append_deviation(args.instance, "INSTANCE_RESUMED", "User resumed instance")

    save_instance_state(args.instance, new_state)

    return {"status": "ok", "instance_id": args.instance}
