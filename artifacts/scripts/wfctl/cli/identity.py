"""identity 命令。"""

import json
from pathlib import Path

from core.errors import InputError


def register_identity(subparsers):
    p = subparsers.add_parser("identity", help="读取当前 worktree 的身份元数据")
    p.set_defaults(handler=_handle_identity)


def _handle_identity(args) -> dict:
    # 从当前目录向上查找 .wfctl_identity.json
    cwd = Path.cwd()
    identity_file = None
    for parent in [cwd] + list(cwd.parents):
        candidate = parent / ".wfctl_identity.json"
        if candidate.exists():
            identity_file = candidate
            break

    if not identity_file:
        raise InputError("Identity file not found. Are you in a stage worktree?", code="IDENTITY_MISMATCH")

    data = json.loads(identity_file.read_text(encoding="utf-8"))
    # 防御性过滤——绝不返回 project_root（权限隔离）
    return {
        "instance_id": data["instance_id"],
        "stage_id": data.get("stage_id"),
        "stage_instance_id": data.get("stage_instance_id", data.get("stage_id")),
        "message_target_path": data["message_target_path"],
    }
