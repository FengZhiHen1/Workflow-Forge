"""pause 命令——暂停活跃实例，重置运行中 stage。"""

from core.errors import StateError
from services.state_manager import (
    _append_timeline,
    append_deviation,
    load_instance,
    save_instance,
)


def register_pause(subparsers):
    p = subparsers.add_parser("pause", help="暂停活跃实例")
    p.add_argument("--instance", required=True, help="实例 ID")
    p.add_argument("--reason", default="User requested pause", help="暂停原因")
    p.set_defaults(handler=_handle_pause)


def _handle_pause(args) -> dict:
    instance = load_instance(args.instance)

    if instance.get("status") == "COMPLETED":
        raise StateError("Instance already completed")
    if instance.get("status") == "FAILED":
        raise StateError("Instance already terminated")
    if instance.get("status") == "PAUSED":
        raise StateError("Instance already paused")
    if instance.get("status") != "ACTIVE":
        raise StateError(f"Cannot pause instance in status: {instance.get('status')}")

    # 重置 RUNNING → PENDING
    reset_stages: list[str] = []
    for s in instance["stages"]:
        if s.get("status") == "RUNNING":
            s["status"] = "PENDING"
            reset_stages.append(s["stage_id"])

    instance["status"] = "PAUSED"
    _append_timeline(args.instance, "", "instance→paused", {"reason": args.reason, "reset_stages": reset_stages})
    append_deviation(args.instance, "INSTANCE_PAUSED", args.reason)
    save_instance(args.instance, instance)

    return {"status": "ok", "instance_id": args.instance, "reset_stages": reset_stages}
