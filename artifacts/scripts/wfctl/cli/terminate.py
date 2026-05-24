"""terminate 命令——取消活跃实例，清理 worktree 和 anchor tag。"""

from core.errors import InputError, StateError
from core.git_ops import _git, git_add_all, git_commit, git_rev_parse, git_status_porcelain
from core.project import find_root
from services.state_manager import load_instance, save_instance, append_deviation
from services.worktree_manager import (
    backup_instance,
    cleanup_orphan_worktrees,
    remove_anchor,
    remove_instance_worktree,
)


def register_terminate(subparsers):
    p = subparsers.add_parser("terminate", help="终止活跃实例，清理 worktree 和 tag")
    p.add_argument("--instance", required=True, help="实例 ID")
    p.add_argument("--reason", default="User requested termination", help="终止原因")
    p.add_argument("--force", action="store_true", help="跳过安全确认，强制终止")
    p.set_defaults(handler=_handle_terminate)


def _handle_terminate(args) -> dict:
    instance = load_instance(args.instance)

    if instance.get("status") == "COMPLETED":
        raise InputError("Instance already completed", code="INVALID_ARGUMENT")
    if instance.get("status") == "FAILED":
        raise InputError("Instance already terminated", code="INVALID_ARGUMENT")

    root = find_root()
    instance_id = args.instance

    # 安全检查：一级实例未合入 main，需 --force 或确认
    is_root = not instance.get("parent_instance_id")
    if is_root and not args.force:
        if not _is_merged_to_main(instance_id, instance):
            return {
                "status": "requires_confirmation",
                "instance_id": instance_id,
                "reason": "Root instance not merged to main. Use --force to terminate anyway.",
            }

    # 0. 创建备份（保底恢复）
    backup_ok = False
    try:
        backup_ok = backup_instance(instance_id)
    except Exception:
        pass

    # 1. 置为 FAILED
    instance["status"] = "FAILED"
    save_instance(instance_id, instance)

    # 2. 清理该实例的所有 anchor tag（在删 worktree 之前）
    from services.resolver import find_workflow_dir
    version = instance.get("version", "")
    wf_dir = find_workflow_dir(instance["workflow_id"], version if version else None)
    from core.schema.loader import load_workflow
    spec = load_workflow(wf_dir / "WORKFLOW.yaml")
    anchor_prefix = spec.anchor_prefix

    rc, stdout, _ = _git(root, "tag", "-l", f"{anchor_prefix}-{instance_id}-*")
    if rc == 0 and stdout.strip():
        for tag_name in stdout.strip().splitlines():
            tag_name = tag_name.strip()
            if tag_name:
                remove_anchor(instance_id, tag_name)

    # 3. 抢救实例 worktree 中未提交的文件（正常流程外的直接写入）
    wt_path = root / ".tmp" / "worktrees" / f"instance-{instance_id}"
    if wt_path.exists():
        try:
            rc, stdout, _ = git_status_porcelain(wt_path)
            if rc == 0 and stdout.strip():
                git_add_all(wt_path)
                rescue_msg = (
                    f"rescue: auto-commit before terminate {instance_id}\n\n"
                    f"Uncommitted files at termination:\n{stdout}"
                )
                c_rc, c_out, c_err = git_commit(wt_path, rescue_msg)
                if c_rc == 0:
                    sha_rc, sha_out, _ = git_rev_parse(wt_path, "HEAD")
                    rescue_sha = sha_out.strip() if sha_rc == 0 else "unknown"
                    append_deviation(
                        instance_id, "TERMINATE_RESCUE",
                        f"Rescued uncommitted files as commit {rescue_sha}",
                    )
                else:
                    append_deviation(
                        instance_id, "TERMINATE_RESCUE_FAILED",
                        f"Could not commit uncommitted files: {c_err}",
                    )
        except Exception:
            pass

    # 4. 移除实例 worktree
    try:
        remove_instance_worktree(instance_id)
    except Exception:
        pass

    # 5. 清理孤儿 worktree（stage 级残留）
    try:
        cleanup_orphan_worktrees()
    except Exception:
        pass

    # 6. 记录 deviation
    append_deviation(instance_id, "USER_TERMINATE", args.reason)

    # 7. 清理残留实例目录（backup 后用 shutil.move 移走，但 save_instance
    #    和 append_deviation 会通过 mkdir(parents=True) 重新创建）
    if backup_ok:
        import shutil
        inst_dir = root / ".agent" / "instances" / instance_id
        if inst_dir.exists():
            shutil.rmtree(str(inst_dir), ignore_errors=True)

    return {
        "status": "ok",
        "instance_id": instance_id,
        "reason": args.reason,
    }


def _is_merged_to_main(instance_id: str, instance: dict) -> bool:
    """检查实例是否已合入 main：status == COMPLETED 或 merge_confirmed 已设置。"""
    if instance.get("status") == "COMPLETED":
        return True
    if instance.get("merge_confirmed"):
        return True
    return False
