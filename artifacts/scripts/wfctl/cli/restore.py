"""restore 命令——从归档恢复误删的实例。"""

from core.errors import InputError
from core.project import find_root
from services.worktree_manager import restore_instance


def register_restore(subparsers):
    p = subparsers.add_parser("restore", help="从归档恢复误删的实例")
    p.add_argument("--instance", required=True, help="要恢复的实例 ID")
    p.set_defaults(handler=_handle_restore)


def _handle_restore(args) -> dict:
    root = find_root()
    archive_dir = root / ".agent" / "archive" / args.instance
    inst_dir = root / ".agent" / "instances" / args.instance

    if not archive_dir.exists():
        raise InputError(
            f"No archive found for instance {args.instance}",
            code="INSTANCE_NOT_FOUND",
        )
    if inst_dir.exists():
        raise InputError(
            f"Instance {args.instance} already exists in instances directory",
            code="INSTANCE_EXISTS",
        )

    return restore_instance(args.instance)
