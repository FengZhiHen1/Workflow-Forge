"""权限校验、保护区检测。"""

from pathlib import Path

from infrastructure.errors import ValidationError
from infrastructure.project import find_root


def validate_modified_files(worktree: Path, modified_files: list[str], stage_id: str) -> None:
    """校验变更文件列表是否触及保护区。

    保护区：
    - .agent/
    - .git/
    - .tmp/worktrees/ 下非本 stage 的 worktree
    - 主仓库工作区（非 .tmp/ 下的所有路径）——实际上 SubAgent 在 worktree 内工作
    """
    root = find_root()
    for f in modified_files:
        p = Path(f)
        parts = [part.lower() for part in p.parts]

        if ".agent" in parts:
            raise ValidationError(
                f"Access violation: modified file touches .agent/: {f}",
                code="ACCESS_VIOLATION",
            )
        if ".claude" in parts:
            raise ValidationError(
                f"Access violation: modified file touches .claude/: {f}",
                code="ACCESS_VIOLATION",
            )
        if ".git" in parts:
            raise ValidationError(
                f"Access violation: modified file touches .git/: {f}",
                code="ACCESS_VIOLATION",
            )

        # 检查是否逃逸到 worktree 之外
        resolved = (worktree / p).resolve()
        try:
            resolved.relative_to(worktree.resolve())
        except ValueError:
            raise ValidationError(
                f"Access violation: modified file escapes worktree: {f}",
                code="ACCESS_VIOLATION",
            )

    # 额外：检查是否修改了其他 stage 的 worktree
    other_worktrees = [d for d in (root / ".tmp" / "worktrees").glob("stage-*") if d.resolve() != worktree.resolve()]
    for f in modified_files:
        p = (worktree / f).resolve()
        for other in other_worktrees:
            try:
                p.relative_to(other.resolve())
                raise ValidationError(
                    f"Access violation: modified file touches other worktree: {f}",
                    code="ACCESS_VIOLATION",
                )
            except ValueError:
                continue
