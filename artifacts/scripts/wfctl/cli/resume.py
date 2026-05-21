"""resume 命令——恢复暂停的实例。"""

from core.errors import StateError
from services.state_manager import (
    _append_timeline,
    append_deviation,
    load_instance,
    save_instance,
)


def register_resume(subparsers):
    p = subparsers.add_parser("resume", help="恢复暂停的实例")
    p.add_argument("--instance", required=True, help="实例 ID")
    p.set_defaults(handler=_handle_resume)


def _handle_resume(args) -> dict:
    instance = load_instance(args.instance)

    if instance.get("status") == "COMPLETED":
        raise StateError("Instance already completed")
    if instance.get("status") == "FAILED":
        raise StateError("Instance already terminated")
    if instance.get("status") == "ACTIVE":
        raise StateError("Instance is already active")
    if instance.get("status") != "PAUSED":
        raise StateError(f"Cannot resume instance in status: {instance.get('status')}")

    instance["status"] = "ACTIVE"
    _append_timeline(args.instance, "", "instance→active (resumed)")
    append_deviation(args.instance, "INSTANCE_RESUMED", "User resumed instance")
    save_instance(args.instance, instance)

    return {"status": "ok", "instance_id": args.instance}
