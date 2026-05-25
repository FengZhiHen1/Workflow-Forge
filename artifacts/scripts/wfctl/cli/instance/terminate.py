"""terminate 命令——取消活跃实例，清理 worktree 和 anchor tag。

状态标记使用 StateDelta，副作用（备份、清理、rescue）保留在 handler。
"""

from infrastructure.errors import InputError
from runtime.worktree.git import _git, git_add_all, git_commit, git_rev_parse, git_status_porcelain
from infrastructure.project import find_root
from compat.instance.registry import load_instance_state, save_instance_state
from state.model import InstanceStatus, StateDelta
from services.state_manager import append_deviation
from runtime.worktree.manager import (
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
    state = load_instance_state(args.instance)
    instance_id = args.instance

    if state.status.value == "COMPLETED":
        raise InputError("Instance already completed", code="INVALID_ARGUMENT")
    if state.status.value == "FAILED":
        raise InputError("Instance already terminated", code="INVALID_ARGUMENT")

    root = find_root()

    # 安全检查
    is_root = not state.parent_instance_id
    if is_root and not args.force:
        if not _is_merged_to_main(state):
            return {
                "status": "requires_confirmation",
                "instance_id": instance_id,
                "reason": "Root instance not merged to main. Use --force to terminate anyway.",
            }

    # 状态变更：标记 FAILED
    delta = StateDelta(instance_updates={"status": InstanceStatus.FAILED})
    new_state = state.apply_delta(delta)
    save_instance_state(instance_id, new_state)

    # ── 副作用区 ──
    # 0. 备份
    backup_ok = False
    try:
        backup_ok = backup_instance(instance_id)
    except Exception:
        pass

    # 1. 清理 anchor tags
    from services.resolver import find_workflow_dir
    from compat.workflow.registry import load_workflow
    version = state.version
    wf_dir = find_workflow_dir(state.workflow_id, version if version else None)
    spec = load_workflow(wf_dir / "WORKFLOW.yaml")
    anchor_prefix = spec.anchor_prefix

    rc, stdout, _ = _git(root, "tag", "-l", f"{anchor_prefix}-{instance_id}-*")
    if rc == 0 and stdout.strip():
        for tag_name in stdout.strip().splitlines():
            tag_name = tag_name.strip()
            if tag_name:
                remove_anchor(instance_id, tag_name)

    # 2. 抢救未提交文件
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

    # 3. 移除 instance worktree
    try:
        remove_instance_worktree(instance_id)
    except Exception:
        pass

    # 4. 清理孤儿 worktree
    try:
        cleanup_orphan_worktrees()
    except Exception:
        pass

    # 5. 记录 deviation
    append_deviation(instance_id, "USER_TERMINATE", args.reason)

    # 6. 清理残留目录
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


def _is_merged_to_main(state) -> bool:
    """检查实例是否已合入 main。"""
    if state.status.value == "COMPLETED":
        return True
    if state.merge_confirmed:
        return True
    return False
