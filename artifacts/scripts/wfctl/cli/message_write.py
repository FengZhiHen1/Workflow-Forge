"""message write 命令。"""

import json
from pathlib import Path

from core.errors import InputError
from services.message_handler import write_message


def register_message_write(subparsers):
    p = subparsers.add_parser("message", help="消息操作")
    sub = p.add_subparsers(dest="message_cmd", required=True)
    write_p = sub.add_parser("write", help="写入消息")
    write_p.add_argument("--instance", required=True, help="实例 ID")
    write_p.add_argument("--stage", required=True, help="stage_id")
    write_p.add_argument("--status", required=True, help="消息状态")
    write_p.add_argument("--report", required=True, help="执行摘要")
    write_p.add_argument("--checkpoint", default=None, help="checkpoint_summary")
    write_p.add_argument("--questions", nargs="*", default=[], help="确认问题列表")
    write_p.add_argument("--parallel-targets", nargs="*", default=[], help="parallel 目标")
    write_p.set_defaults(handler=_handle_message_write)


def _handle_message_write(args) -> dict:
    # 从当前目录向上查找身份文件
    identity_file = _find_identity_file()
    if not identity_file:
        raise InputError(
            "Identity file not found. Are you in a stage worktree?",
            code="IDENTITY_MISMATCH",
        )

    try:
        identity = json.loads(identity_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        raise InputError("Corrupted identity file", code="IDENTITY_MISMATCH")

    # 校验调用者身份
    if identity.get("instance_id") != args.instance:
        raise InputError(
            f"Identity mismatch: instance_id {identity.get('instance_id')} != {args.instance}",
            code="IDENTITY_MISMATCH",
        )
    if identity.get("stage_id") and identity["stage_id"] != args.stage:
        raise InputError(
            f"Identity mismatch: stage_id {identity['stage_id']} != {args.stage}",
            code="IDENTITY_MISMATCH",
        )

    # worktree 为身份文件所在目录
    worktree = identity_file.parent

    # 从身份文件读取 stage_instance_id（解决 parallel 拆分场景）
    stage_instance_id = identity.get("stage_instance_id", args.stage)

    parallel_targets = None
    if args.parallel_targets:
        parallel_targets = []
        for t in args.parallel_targets:
            if ":" in t:
                parts = t.split(":", 2)
                parallel_targets.append({
                    "id": parts[0],
                    "label": parts[1] if len(parts) > 1 else parts[0],
                    "context": parts[2] if len(parts) > 2 else "",
                })
            else:
                parallel_targets.append({"id": t, "label": t, "context": ""})

    return write_message(
        instance_id=args.instance,
        stage_id=args.stage,
        stage_instance_id=stage_instance_id,
        status=args.status,
        report=args.report,
        checkpoint_summary=args.checkpoint,
        confirm_questions=list(args.questions) if args.questions else None,
        parallel_targets=parallel_targets,
        worktree=worktree,
        message_target_path=identity.get("message_target_path"),
    )


def _find_identity_file() -> Path | None:
    """从当前目录向上查找 .wfctl_identity.json。"""
    cwd = Path.cwd()
    for parent in [cwd] + list(cwd.parents):
        candidate = parent / ".wfctl_identity.json"
        if candidate.exists():
            return candidate
    return None
