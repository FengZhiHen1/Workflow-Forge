"""cleanup 命令——清理僵尸实例、孤儿 worktree、残留 tag。"""

import json
import shutil
from pathlib import Path

from infrastructure.errors import InputError
from runtime.worktree.git import _git, git_tag_delete, git_worktree_list, git_worktree_prune, git_worktree_remove
from infrastructure.project import find_root


def register_cleanup(subparsers):
    p = subparsers.add_parser("cleanup", help="清理僵尸实例、孤儿 worktree 和残留 tag")
    p.add_argument("--instance", default=None, help="仅清理指定实例（可选，不指定则清理全部僵尸）")
    p.add_argument("--dry-run", action="store_true", help="仅列出，不执行清理")
    p.add_argument("--force", action="store_true", help="强制清理，跳过安全确认")
    p.set_defaults(handler=_handle_cleanup)


def _handle_cleanup(args) -> dict:
    root = find_root()
    removed: list[dict] = []
    skipped: list[dict] = []

    # 1. 清理 git worktree 注册（目录已丢失的）
    _prune_stale_worktrees(root, removed, args.dry_run)

    # 2. 清理僵尸实例目录
    _cleanup_zombie_instances(root, removed, skipped, args.dry_run, args.instance, args.force)

    # 3. 清理残留 anchor tag（无对应实例的）
    _cleanup_stale_tags(root, removed, args.dry_run, args.instance)

    return {
        "status": "ok",
        "removed": removed,
        "skipped": skipped,
        "dry_run": args.dry_run,
    }


def _prune_stale_worktrees(root: Path, removed: list[dict], dry_run: bool) -> None:
    """清理目录已丢失但 git 注册还在的 worktree。"""
    git_worktree_prune(root)

    # 同时清理孤儿 worktree（目录在但无对应活跃实例）
    wt_dir = root / ".tmp" / "worktrees"
    if not wt_dir.exists():
        return

    instances_dir = root / ".agent" / "instances"
    for d in wt_dir.iterdir():
        if not d.is_dir():
            continue
        if d.name.startswith("instance-"):
            inst_id = d.name[len("instance-"):]
            inst_path = instances_dir / inst_id / "instance.json"
            if inst_path.exists():
                continue  # 实例还在，跳过
            if dry_run:
                removed.append({"worktree": str(d), "reason": "orphan worktree", "action": "would remove"})
            else:
                git_worktree_remove(root, d, force=True)
                removed.append({"worktree": str(d), "reason": "orphan worktree", "action": "removed"})

        elif d.name.startswith("stage-"):
            # stage 级 worktree：对应实例不存在 / 已终态 / 无活跃 stage → 清理
            should_remove = False
            reason = ""
            inst_id = _extract_instance_id_from_stage_worktree(d.name)
            inst_path = instances_dir / inst_id / "instance.json"
            if not inst_path.exists():
                should_remove = True
                reason = "orphan stage worktree (no instance)"
            else:
                try:
                    data = json.loads(inst_path.read_text(encoding="utf-8"))
                    status = data.get("status", "")
                    if status in ("FAILED", "COMPLETED"):
                        should_remove = True
                        reason = f"stage worktree for {status} instance"
                    elif status == "PAUSED":
                        # PAUSED 也没有活跃 stage，可以安全清理
                        should_remove = True
                        reason = "stage worktree for paused instance"
                    else:
                        running = [s for s in data.get("stages", []) if s.get("status") in ("RUNNING", "CONFLICT")]
                        if not running:
                            should_remove = True
                            reason = "stage worktree with no active stages"
                except Exception:
                    should_remove = True
                    reason = "stage worktree (unreadable instance)"

            if should_remove:
                if dry_run:
                    removed.append({"worktree": str(d), "reason": reason, "action": "would remove"})
                else:
                    git_worktree_remove(root, d, force=True)
                    removed.append({"worktree": str(d), "reason": reason, "action": "removed"})


def _extract_instance_id_from_stage_worktree(dir_name: str) -> str:
    """从 stage worktree 目录名提取 instance_id。

    目录名格式：stage-<instance_id>-<stage_instance_id>[_<idx>]
    例：stage-20260518-002-p1b-problem-analysis_0 → 20260518-002
    """
    # 去掉 stage- 前缀
    rest = dir_name[len("stage-"):]
    # instance_id 格式为 YYYYMMDD-NNN
    parts = rest.split("-", 2)
    if len(parts) >= 2:
        return f"{parts[0]}-{parts[1]}"
    return rest  # fallback


def _cleanup_zombie_instances(root: Path, removed: list[dict], skipped: list[dict],
                               dry_run: bool, target_instance: str | None, force: bool) -> None:
    """清理僵尸实例——instance.json 存在但无对应 worktree 或 status 已终态。"""
    from runtime.worktree.manager import backup_instance as backup

    instances_dir = root / ".agent" / "instances"
    if not instances_dir.exists():
        return

    for d in instances_dir.iterdir():
        if not d.is_dir():
            continue
        if target_instance and d.name != target_instance:
            continue

        inst_json = d / "instance.json"
        if not inst_json.exists():
            continue

        try:
            data = json.loads(inst_json.read_text(encoding="utf-8"))
        except Exception:
            continue

        status = data.get("status", "")
        instance_id = data.get("instance_id", d.name)
        is_root = not data.get("parent_instance_id")

        # 检查是否有对应 worktree
        wt_path = root / ".tmp" / "worktrees" / f"instance-{instance_id}"
        has_worktree = wt_path.exists()

        if status in ("FAILED", "COMPLETED"):
            # 一级 FAILED 实例未合入且非 force → 跳过
            if is_root and status == "FAILED" and not force:
                skipped.append({"instance": instance_id, "reason": "root instance not merged, use --force to cleanup"})
                continue

            # 删除前备份
            if not dry_run:
                try:
                    backup(instance_id)
                except Exception:
                    pass

            if has_worktree:
                if dry_run:
                    removed.append({"worktree": str(wt_path), "reason": f"instance {status}", "action": "would remove"})
                else:
                    git_worktree_remove(root, wt_path, force=True)
                    removed.append({"worktree": str(wt_path), "reason": f"instance {status}", "action": "removed"})
            _cleanup_instance_tags(root, instance_id, removed, dry_run)
            if dry_run:
                removed.append({"instance": instance_id, "reason": f"instance {status}", "action": "would remove directory"})
            else:
                shutil.rmtree(d, ignore_errors=True)
                removed.append({"instance": instance_id, "reason": f"instance {status}", "action": "removed directory"})

        elif status == "ACTIVE":
            running = [s for s in data.get("stages", []) if s.get("status") in ("RUNNING", "CONFLICT")]
            if not running and not has_worktree:
                # 一级 ACTIVE 实例未合入且非 force → 跳过
                if is_root and not force:
                    skipped.append({"instance": instance_id, "reason": "root active zombie not merged, use --force to cleanup"})
                    continue

                # 删除前备份
                if not dry_run:
                    try:
                        backup(instance_id)
                    except Exception:
                        pass

                if dry_run:
                    removed.append({"instance": instance_id, "reason": "zombie (ACTIVE, no worktree, no running stage)", "action": "would remove"})
                else:
                    shutil.rmtree(d, ignore_errors=True)
                    _cleanup_instance_tags(root, instance_id, removed, dry_run)
                    removed.append({"instance": instance_id, "reason": "zombie (ACTIVE, no worktree, no running stage)", "action": "removed"})


def _cleanup_instance_tags(root: Path, instance_id: str, removed: list[dict], dry_run: bool) -> None:
    """清理指定实例的所有 anchor tag。"""
    # 使用 git tag -l 查找该实例的所有 tag
    rc, stdout, _ = git_worktree_list(root)
    if rc != 0:
        return

    # 直接使用 git tag -l 匹配
    for prefix in ("wf-",):
        pattern = f"{prefix}{instance_id}-*"
        rc, stdout, _ = _git(root, "tag", "-l", pattern)
        if rc == 0 and stdout.strip():
            for tag_name in stdout.strip().splitlines():
                tag_name = tag_name.strip()
                if not tag_name:
                    continue
                if dry_run:
                    removed.append({"tag": tag_name, "reason": "stale anchor tag", "action": "would delete"})
                else:
                    git_tag_delete(root, tag_name)
                    removed.append({"tag": tag_name, "reason": "stale anchor tag", "action": "deleted"})


def _cleanup_stale_tags(root: Path, removed: list[dict], dry_run: bool, target_instance: str | None) -> None:
    """清理无对应实例目录的残留 anchor tag。"""
    instances_dir = root / ".agent" / "instances"
    known_ids: set[str] = set()
    if instances_dir.exists():
        for d in instances_dir.iterdir():
            if not d.is_dir():
                continue
            inst_json = d / "instance.json"
            if inst_json.exists():
                try:
                    data = json.loads(inst_json.read_text(encoding="utf-8"))
                    known_ids.add(data.get("instance_id", d.name))
                except Exception:
                    pass
            known_ids.add(d.name)

    rc, stdout, _ = _git(root, "tag", "-l", "wf-*")
    if rc != 0 or not stdout.strip():
        return

    for tag_name in stdout.strip().splitlines():
        tag_name = tag_name.strip()
        if not tag_name:
            continue
        # 从 tag 名提取 instance_id：wf-<instance_id>-<stage>
        parts = tag_name.split("-", 2)  # ["wf", "<instance_id>", "<stage>"]
        if len(parts) < 3:
            continue
        inst_id = parts[1]
        if target_instance and inst_id != target_instance:
            continue
        if inst_id not in known_ids:
            if dry_run:
                removed.append({"tag": tag_name, "reason": "stale tag (no instance)", "action": "would delete"})
            else:
                git_tag_delete(root, tag_name)
                removed.append({"tag": tag_name, "reason": "stale tag (no instance)", "action": "deleted"})
