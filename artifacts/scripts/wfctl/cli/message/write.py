"""message write 命令。"""

import json
from pathlib import Path

from infrastructure.errors import InputError
from runtime.message.handler import write_message


def register_message_write(subparsers):
    p = subparsers.add_parser("message", help="消息操作")
    sub = p.add_subparsers(dest="message_cmd", required=True)
    write_p = sub.add_parser("write", help="写入消息")
    write_p.add_argument("--instance", required=True, help="实例 ID")
    write_p.add_argument("--stage", required=True, help="stage_id")
    write_p.add_argument("--status", required=True, help="消息状态")
    write_p.add_argument("--report", default="", help="执行摘要（直接文本）")
    write_p.add_argument("--report-file", default=None, help="执行摘要文件路径（绕过 shell 转义）")
    write_p.add_argument("--checkpoint", default=None, help="checkpoint_summary")
    write_p.add_argument("--questions", nargs="*", default=[], help="确认问题列表")
    write_p.add_argument("--parallel-targets", nargs="*", default=[], help="parallel 目标")
    write_p.add_argument("--choice", default=None, help="路由选择（匹配 SUCCESS 边的 choice）")
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
    if identity.get("stage_id") is not None and identity["stage_id"] != args.stage:
        raise InputError(
            f"Identity mismatch: stage_id {identity['stage_id']} != {args.stage}",
            code="IDENTITY_MISMATCH",
        )

    # worktree 为身份文件所在目录
    worktree = identity_file.parent

    # 从身份文件读取 stage_instance_id（解决 parallel 拆分场景）
    stage_instance_id = identity.get("stage_instance_id", args.stage)

    # 解析 report：--report-file 优先级高于 --report，绕过 shell 转义
    report = args.report
    if args.report_file:
        file_path = Path(args.report_file)
        if not file_path.exists():
            raise InputError(
                f"Report file not found: {args.report_file}",
                code="REPORT_FILE_NOT_FOUND",
            )
        report = file_path.read_text(encoding="utf-8").strip()
    if not report:
        raise InputError(
            "必须提供 --report 或 --report-file 参数",
            code="REPORT_REQUIRED",
        )

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
        report=report,
        checkpoint_summary=args.checkpoint,
        confirm_questions=list(args.questions) if args.questions else None,
        parallel_targets=parallel_targets,
        routing_choice=args.choice,
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
