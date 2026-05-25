"""worktree 生命周期管理。"""

from pathlib import Path

from infrastructure.errors import GitError, WorktreeError
from runtime.worktree.git import (
    git_branch,
    git_checkout,
    git_fetch,
    git_merge,
    git_merge_abort,
    git_rev_parse,
    git_status_porcelain,
    git_tag,
    git_tag_delete,
    git_worktree_add,
    git_worktree_list,
    git_worktree_remove,
)
from infrastructure.project import find_root
from domain.transition.results import SyncResult


def create_instance_worktree(instance_id: str, base_ref: str = "HEAD") -> Path:
    """创建实例 worktree。"""
    root = find_root()
    wt_path = root / ".tmp" / "worktrees" / f"instance-{instance_id}"
    wt_path.parent.mkdir(parents=True, exist_ok=True)

    rc, _, stderr = git_worktree_add(root, wt_path, base_ref)
    if rc != 0:
        raise WorktreeError(f"Failed to create instance worktree: {stderr}", code="WORKTREE_CREATE_FAILED")

    return wt_path


def remove_instance_worktree(instance_id: str) -> None:
    """移除实例 worktree。"""
    root = find_root()
    wt_path = root / ".tmp" / "worktrees" / f"instance-{instance_id}"
    if wt_path.exists():
        git_worktree_remove(root, wt_path, force=True)


def create_stage_worktree(instance_id: str, stage_instance_id: str) -> Path:
    """创建 stage 级 worktree。"""
    root = find_root()
    inst_wt = root / ".tmp" / "worktrees" / f"instance-{instance_id}"
    stage_wt = root / ".tmp" / "worktrees" / f"stage-{instance_id}-{stage_instance_id}"
    stage_wt.parent.mkdir(parents=True, exist_ok=True)

    # 获取实例 worktree 的 HEAD commit 作为 base_ref
    rc, stdout, _ = git_rev_parse(inst_wt, "HEAD")
    if rc != 0:
        raise WorktreeError("Failed to get instance worktree HEAD", code="WORKTREE_CREATE_FAILED")
    head = stdout.strip()

    branch = f"wf-stage-{instance_id}-{stage_instance_id}"
    rc, _, stderr = git_worktree_add(root, stage_wt, head, branch=branch)
    if rc != 0:
        raise WorktreeError(f"Failed to create stage worktree: {stderr}", code="WORKTREE_CREATE_FAILED")

    return stage_wt


def create_parallel_worktree(instance_id: str, stage_instance_id: str, idx: int) -> Path:
    """创建 parallel 拆分 worktree。"""
    root = find_root()
    inst_wt = root / ".tmp" / "worktrees" / f"instance-{instance_id}"
    para_wt = root / ".tmp" / "worktrees" / f"stage-{instance_id}-{stage_instance_id}_{idx}"
    para_wt.parent.mkdir(parents=True, exist_ok=True)

    # 获取实例 worktree 的 HEAD commit 作为 base_ref
    rc, stdout, _ = git_rev_parse(inst_wt, "HEAD")
    if rc != 0:
        raise WorktreeError("Failed to get instance worktree HEAD", code="WORKTREE_CREATE_FAILED")
    head = stdout.strip()

    branch = f"wf-stage-{instance_id}-{stage_instance_id}_{idx}"
    rc, _, stderr = git_worktree_add(root, para_wt, head, branch=branch)
    if rc != 0:
        raise WorktreeError(f"Failed to create parallel worktree: {stderr}", code="WORKTREE_CREATE_FAILED")

    return para_wt


def merge_stage_worktree(instance_id: str, stage_instance_id: str) -> tuple[bool, list[str]]:
    """将 stage worktree 合并回实例 worktree。

    返回 (success, conflict_files)
    """
    root = find_root()
    inst_wt = root / ".tmp" / "worktrees" / f"instance-{instance_id}"
    stage_wt = root / ".tmp" / "worktrees" / f"stage-{instance_id}-{stage_instance_id}"
    branch = f"wf-stage-{instance_id}-{stage_instance_id}"

    # fetch
    rc, _, stderr = git_fetch(inst_wt, stage_wt, branch)
    if rc != 0:
        raise GitError(f"Fetch failed: {stderr}")

    # merge
    rc, stdout, stderr = git_merge(inst_wt, "FETCH_HEAD", no_ff=True)
    if rc == 0:
        # 无冲突，清理 stage worktree
        git_worktree_remove(root, stage_wt, force=True)
        return True, []

    # 有冲突
    conflict_files = _extract_conflict_files(inst_wt)
    return False, conflict_files


def resolve_conflicts_and_merge(instance_id: str, stage_instance_id: str) -> bool:
    """冲突已解决后重试合并。"""
    root = find_root()
    inst_wt = root / ".tmp" / "worktrees" / f"instance-{instance_id}"
    stage_wt = root / ".tmp" / "worktrees" / f"stage-{instance_id}-{stage_instance_id}"
    branch = f"wf-stage-{instance_id}-{stage_instance_id}"

    rc, _, stderr = git_fetch(inst_wt, stage_wt, branch)
    if rc != 0:
        raise GitError(f"Fetch failed: {stderr}")

    rc, _, stderr = git_merge(inst_wt, "FETCH_HEAD", no_ff=True)
    if rc == 0:
        git_worktree_remove(root, stage_wt, force=True)
        return True
    return False


def merge_instance_to_main(instance_id: str) -> tuple[bool, list[str]]:
    """将实例 worktree 合入主仓库。

    返回 (success, conflict_files)
    """
    root = find_root()
    inst_wt = root / ".tmp" / "worktrees" / f"instance-{instance_id}"

    # 获取实例 worktree 当前分支的 HEAD
    rc, stdout, _ = git_rev_parse(inst_wt, "HEAD")
    if rc != 0:
        raise GitError("Failed to get instance HEAD")
    head = stdout.strip()

    # 合并到主仓库
    rc, _, stderr = git_merge(root, head, no_ff=False)
    if rc == 0:
        return True, []

    conflict_files = _extract_conflict_files(root)
    return False, conflict_files


def tag_anchor(instance_id: str, tag_name: str, worktree: Path | None = None) -> None:
    """在 worktree 内打锚点 tag。"""
    root = find_root()
    wt = worktree or (root / ".tmp" / "worktrees" / f"instance-{instance_id}")
    rc, _, stderr = git_tag(wt, tag_name)
    if rc != 0:
        raise GitError(f"Failed to tag anchor: {stderr}")


def remove_anchor(instance_id: str, tag_name: str, worktree: Path | None = None) -> None:
    """移除锚点 tag。"""
    root = find_root()
    wt = worktree or (root / ".tmp" / "worktrees" / f"instance-{instance_id}")
    rc, _, _ = git_tag_delete(wt, tag_name)
    # 忽略错误，可能 tag 不存在


def checkout_to_anchor(instance_id: str, tag_name: str) -> None:
    """检出到指定锚点。"""
    root = find_root()
    inst_wt = root / ".tmp" / "worktrees" / f"instance-{instance_id}"
    rc, _, stderr = git_checkout(inst_wt, tag_name)
    if rc != 0:
        raise GitError(f"Checkout failed: {stderr}")


def _extract_conflict_files(repo: Path) -> list[str]:
    """从 git status --porcelain 提取冲突文件。"""
    rc, stdout, _ = git_status_porcelain(repo)
    if rc != 0:
        return []
    conflicts: list[str] = []
    for line in stdout.strip().splitlines():
        if line.startswith("UU ") or line.startswith("AA ") or line.startswith("DD ") or line.startswith("AU ") or line.startswith("UA "):
            parts = line.split()
            if len(parts) >= 2:
                conflicts.append(parts[1])
        elif line.startswith("?? "):
            pass
    return conflicts


def backup_instance(instance_id: str) -> bool:
    """在删除前创建备份分支 + 归档实例目录。

    1. 在实例 worktree 的 HEAD 上创建 wf-backup-{instance_id} 分支，防止 commit 被 gc
    2. 将 .agent/instances/{id}/ 移动到 .agent/archive/{id}/
    """
    import shutil

    root = find_root()
    inst_wt = root / ".tmp" / "worktrees" / f"instance-{instance_id}"
    inst_dir = root / ".agent" / "instances" / instance_id
    archive_dir = root / ".agent" / "archive" / instance_id

    # 1. 创建备份分支
    branch_name = f"wf-backup-{instance_id}"
    if inst_wt.exists():
        rc, _, _ = git_rev_parse(inst_wt, f"refs/heads/{branch_name}")
        if rc != 0:
            git_branch(inst_wt, branch_name)
    else:
        # worktree 不存在时，尝试从 final tag 在主仓库创建备份分支
        rc, head_ref, _ = git_rev_parse(root, f"refs/tags/wf-{instance_id}-final")
        if rc == 0:
            git_branch(root, branch_name, head_ref.strip())

    # 2. 归档实例目录
    if inst_dir.exists():
        archive_dir.parent.mkdir(parents=True, exist_ok=True)
        if archive_dir.exists():
            shutil.rmtree(archive_dir, ignore_errors=True)
        shutil.move(str(inst_dir), str(archive_dir))
        return True

    return False


def restore_instance(instance_id: str) -> dict:
    """从归档恢复实例。

    1. 将 .agent/archive/{id}/ 移回 .agent/instances/{id}/
    2. 从 wf-backup-{id} 分支重建 worktree
    3. 重建 anchor tag
    """
    import json
    import shutil

    root = find_root()
    archive_dir = root / ".agent" / "archive" / instance_id
    inst_dir = root / ".agent" / "instances" / instance_id
    inst_wt = root / ".tmp" / "worktrees" / f"instance-{instance_id}"
    inst_json = archive_dir / "instance.json"

    if not archive_dir.exists():
        raise WorktreeError(f"No archive found for instance {instance_id}", code="RESTORE_FAILED")
    if not inst_json.exists():
        raise WorktreeError(f"instance.json missing in archive for {instance_id}", code="RESTORE_FAILED")

    try:
        data = json.loads(inst_json.read_text(encoding="utf-8"))
    except Exception:
        raise WorktreeError(f"Corrupted instance.json in archive for {instance_id}", code="RESTORE_FAILED")

    # 1. 移回实例目录
    if inst_dir.exists():
        shutil.rmtree(inst_dir, ignore_errors=True)
    shutil.move(str(archive_dir), str(inst_dir))

    # 2. 重建 worktree
    branch_name = f"wf-backup-{instance_id}"
    if not inst_wt.exists():
        git_worktree_add(root, inst_wt, branch_name)

    # 3. 重建 anchor tag
    from compat.workflow.registry import load_workflow
    from services.resolver import find_workflow_dir
    wf_dir = find_workflow_dir(data.get("workflow_id", ""), data.get("version", ""))
    spec = load_workflow(wf_dir / "WORKFLOW.yaml")

    for s in data.get("stages", []):
        if s.get("status") in ("DONE", "RUNNING", "AWAITING_CONFIRM"):
            stage_inst_id = s.get("stage_instance_id", s["stage_id"])
            tag_name = f"{spec.anchor_prefix}-{instance_id}-{stage_inst_id}"
            try:
                git_tag_delete(root, tag_name)
            except Exception:
                pass
            try:
                git_tag(root, tag_name)
            except Exception:
                pass

    return {"status": "ok", "instance_id": instance_id}


def _is_worktree_clean(wt: Path) -> bool:
    """检查 worktree 是否有未提交的变更。无法判断时假定干净。"""
    rc, stdout, _ = git_status_porcelain(wt)
    if rc != 0:
        return True
    return stdout.strip() == ""


def _sync_worktree(target: Path, source: Path) -> SyncResult:
    """将 source 的 HEAD 合并到 target。

    fetch 失败或 target 不干净时静默跳过（返回 success=True）。
    """
    if not target.exists():
        return SyncResult(success=True)
    if not _is_worktree_clean(target):
        return SyncResult(success=True)

    rc, _, _ = git_fetch(target, source, "HEAD")
    if rc != 0:
        return SyncResult(success=True)

    rc, _, _ = git_merge(target, "FETCH_HEAD")
    if rc == 0:
        return SyncResult(success=True)

    git_merge_abort(target)
    conflict_files = _extract_conflict_files(target)
    return SyncResult(success=False, conflict_files=conflict_files)


def sync_instance_with_main(instance_id: str) -> SyncResult:
    """Level 1: 同步本地主仓库 HEAD → 实例 worktree。"""
    root = find_root()
    inst_wt = root / ".tmp" / "worktrees" / f"instance-{instance_id}"
    return _sync_worktree(inst_wt, root)


def sync_instance_with_parent(instance_id: str, parent_instance_id: str) -> SyncResult:
    """Level 1.5: 同步父实例 worktree → 子实例 worktree。"""
    root = find_root()
    child_wt = root / ".tmp" / "worktrees" / f"instance-{instance_id}"
    parent_wt = root / ".tmp" / "worktrees" / f"instance-{parent_instance_id}"
    return _sync_worktree(child_wt, parent_wt)


def sync_stage_with_instance(instance_id: str, stage_instance_id: str) -> SyncResult:
    """Level 2: 同步实例 worktree → stage worktree。"""
    root = find_root()
    inst_wt = root / ".tmp" / "worktrees" / f"instance-{instance_id}"
    stage_wt = root / ".tmp" / "worktrees" / f"stage-{instance_id}-{stage_instance_id}"
    return _sync_worktree(stage_wt, inst_wt)


def cleanup_orphan_worktrees() -> list[dict]:
    """清理异常残留的 worktree。"""
    root = find_root()
    wt_dir = root / ".tmp" / "worktrees"
    if not wt_dir.exists():
        return []

    removed: list[dict] = []
    for d in wt_dir.iterdir():
        if not d.is_dir():
            continue
        # 检查是否孤儿
        if d.name.startswith("instance-"):
            inst_id = d.name[len("instance-"):]
            inst_path = root / ".agent" / "instances" / inst_id / "instance.json"
            if not inst_path.exists():
                git_worktree_remove(root, d, force=True)
                removed.append({"worktree": str(d), "reason": "orphan instance"})
        elif d.name.startswith("stage-"):
            # 简化：检查对应 instance 是否 ACTIVE 但无 RUNNING/CONFLICT stage
            parts = d.name[len("stage-"):].split("-")
            if len(parts) >= 2:
                inst_id = parts[0]
                inst_path = root / ".agent" / "instances" / inst_id / "instance.json"
                if inst_path.exists():
                    import json
                    try:
                        data = json.loads(inst_path.read_text(encoding="utf-8"))
                        running = [s for s in data.get("stages", []) if s.get("status") in ("RUNNING", "CONFLICT")]
                        if not running:
                            git_worktree_remove(root, d, force=True)
                            removed.append({"worktree": str(d), "reason": "orphan stage"})
                    except Exception:
                        pass

    return removed
