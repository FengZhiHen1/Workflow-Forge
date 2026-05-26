"""权限校验、保护区检测。"""

from pathlib import Path

from infrastructure.errors import ValidationError
from infrastructure.project import find_root
from infrastructure.temp_files import is_temp_file


def _normalize_modified_files(raw: list) -> list[dict]:
    """兼容旧格式（字符串列表）和新格式（对象列表）。"""
    if not raw:
        return []
    if isinstance(raw[0], str):
        return [{"path": p, "status": "M"} for p in raw]
    return raw


def validate_modified_files(worktree: Path, modified_files: list, stage_id: str) -> None:
    """校验变更文件列表是否触及保护区。

    保护区：
    - .agent/
    - .git/
    - .claude/   (非本 stage 的 .claude/ 内容)
    - .tmp/worktrees/ 下非本 stage 的 worktree

    自动生成文件（__pycache__, *.pyc, .pytest_cache 等）会在校验前过滤。
    """
    root = find_root()
    entries = _normalize_modified_files(modified_files)
    for entry in entries:
        f = entry["path"]
        if is_temp_file(f):
            continue
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
    for entry in entries:
        f = entry["path"]
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
