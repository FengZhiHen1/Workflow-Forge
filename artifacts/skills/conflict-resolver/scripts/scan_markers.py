#!/usr/bin/env python3
"""
冲突标记扫描器。

两种模式：
1. 扫描模式：输出每个冲突文件的冲突段位置和内容（JSON）
2. 检查模式 (--check-clean)：验证指定文件是否还有残留冲突标记

调用方式：
    python scan_markers.py --worktree <path> --files <file1> <file2> ...
    python scan_markers.py --worktree <path> --check-clean [--files <file1> ...]
"""

import argparse
import json
import sys
from pathlib import Path


CONFLICT_START = "<<<<<<<"
CONFLICT_SEP = "======="
CONFLICT_END = ">>>>>>>"


def scan_file(filepath: Path) -> list[dict]:
    """扫描单个文件的所有冲突段，返回冲突段列表。"""
    if not filepath.exists():
        return []
    conflicts: list[dict] = []
    lines = filepath.read_text(encoding="utf-8").split("\n")

    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if stripped.startswith(CONFLICT_START) and not stripped.startswith(CONFLICT_END):
            start_line = i + 1  # 1-indexed
            label_ours = stripped[len(CONFLICT_START):].strip()
            ours: list[str] = []
            theirs: list[str] = []
            sep_line = -1
            end_line = -1

            j = i + 1
            while j < len(lines):
                s = lines[j].strip()
                if s.startswith(CONFLICT_SEP):
                    sep_line = j + 1
                    j += 1
                    continue
                if s.startswith(CONFLICT_END):
                    end_line = j + 1
                    label_theirs = s[len(CONFLICT_END):].strip()
                    break
                if sep_line < 0:
                    ours.append(lines[j])
                else:
                    theirs.append(lines[j])
                j += 1

            conflicts.append({
                "file": str(filepath),
                "start_line": start_line,
                "sep_line": sep_line,
                "end_line": end_line,
                "label_ours": label_ours,
                "label_theirs": label_theirs,
                "ours": "\n".join(ours),
                "theirs": "\n".join(theirs),
                "context_before": "\n".join(lines[max(0, i - 3):i]),
                "context_after": "\n".join(lines[end_line:min(len(lines), end_line + 3)]) if end_line > 0 else "",
            })
            i = j + 1 if end_line > 0 else i + 1
        else:
            i += 1

    return conflicts


def check_clean(files: list[Path]) -> tuple[bool, list[str]]:
    """检查文件是否残留冲突标记。返回 (is_clean, dirty_files)。"""
    dirty: list[str] = []
    for fp in files:
        if not fp.exists():
            continue
        text = fp.read_text(encoding="utf-8")
        if CONFLICT_START in text and CONFLICT_SEP in text and CONFLICT_END in text:
            dirty.append(str(fp))
    return len(dirty) == 0, dirty


def main() -> None:
    parser = argparse.ArgumentParser(description="冲突标记扫描器")
    parser.add_argument("--worktree", required=True, help="worktree 根路径")
    parser.add_argument("--files", nargs="*", default=[], help="要扫描的文件列表（相对路径）")
    parser.add_argument("--check-clean", action="store_true", help="检查模式：验证无残留标记")
    args = parser.parse_args()

    worktree = Path(args.worktree).resolve()
    if not args.files:
        print(json.dumps({"conflicts": [], "files_scanned": 0}))
        sys.exit(0)

    resolved_files = [worktree / f for f in args.files]

    if args.check_clean:
        is_clean, dirty = check_clean(resolved_files)
        output = {"clean": is_clean, "dirty_files": dirty}
        print(json.dumps(output, ensure_ascii=False))
        sys.exit(0 if is_clean else 1)

    all_conflicts: list[dict] = []
    for fp in resolved_files:
        all_conflicts.extend(scan_file(fp))

    output = {
        "conflicts": all_conflicts,
        "files_scanned": len(args.files),
        "files_with_conflicts": len({c["file"] for c in all_conflicts}),
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
