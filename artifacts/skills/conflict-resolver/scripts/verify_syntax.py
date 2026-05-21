#!/usr/bin/env python3
"""
语法验证器。

按文件扩展名选择语法检查器。对修改过的文件逐个运行语言级语法检查，
确保合并后的文件在语法上是合法的。

支持的扩展名映射：
    .py  → py_compile
    .js  → node --check
    .ts  → node --check (需 typescript 可用，否则跳过)
    .mjs → node --check
    .cjs → node --check
    其他  → SKIP（不阻塞）

调用方式：
    python verify_syntax.py --worktree <path> --files <file1> <file2> ...
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path


EXT_CHECKERS = {
    ".py": "python -m py_compile",
    ".js": "node --check",
    ".ts": "node --check",       # npx tsc --noEmit 可能更好但较重
    ".mjs": "node --check",
    ".cjs": "node --check",
}

# 部分 checker 可能不可用，预先探测
_AVAILABLE: dict[str, bool] = {}


def _checker_available(checker_cmd: str) -> bool:
    """检测命令是否可用。"""
    if checker_cmd in _AVAILABLE:
        return _AVAILABLE[checker_cmd]
    exe = checker_cmd.split()[0]
    result = subprocess.run([exe, "--version"], capture_output=True, text=True)
    _AVAILABLE[checker_cmd] = result.returncode == 0
    return _AVAILABLE[checker_cmd]


def verify_file(filepath: Path) -> dict:
    """对单个文件运行语法检查，返回结果字典。"""
    ext = filepath.suffix.lower()
    checker_cmd = EXT_CHECKERS.get(ext)

    if checker_cmd is None:
        return {"file": str(filepath), "result": "SKIP", "reason": f"不支持的扩展名: {ext}"}

    if not _checker_available(checker_cmd):
        return {"file": str(filepath), "result": "SKIP", "reason": f"检查器不可用: {checker_cmd}"}

    if ext == ".py":
        return _verify_python(filepath)
    if ext in (".js", ".ts", ".mjs", ".cjs"):
        return _verify_node(filepath)

    return {"file": str(filepath), "result": "SKIP", "reason": "未知扩展名"}


def _verify_python(filepath: Path) -> dict:
    try:
        subprocess.run(
            [sys.executable, "-m", "py_compile", str(filepath)],
            capture_output=True, text=True, check=True,
        )
        return {"file": str(filepath), "result": "PASS"}
    except subprocess.CalledProcessError as e:
        return {"file": str(filepath), "result": "FAIL", "error": e.stderr.strip()}


def _verify_node(filepath: Path) -> dict:
    try:
        subprocess.run(
            ["node", "--check", str(filepath)],
            capture_output=True, text=True, check=True,
        )
        return {"file": str(filepath), "result": "PASS"}
    except subprocess.CalledProcessError as e:
        return {"file": str(filepath), "result": "FAIL", "error": e.stderr.strip()}


def main() -> None:
    parser = argparse.ArgumentParser(description="语法验证器")
    parser.add_argument("--worktree", required=True, help="worktree 根路径（用于拼接完整路径）")
    parser.add_argument("--files", nargs="*", default=[], help="要验证的文件列表（相对路径）")
    args = parser.parse_args()

    worktree = Path(args.worktree).resolve()
    results: list[dict] = []
    passed = 0
    failed = 0
    skipped = 0

    for f in args.files:
        fp = (worktree / f).resolve()
        if not fp.exists():
            results.append({"file": f, "result": "SKIP", "reason": "文件不存在"})
            skipped += 1
            continue
        r = verify_file(fp)
        results.append(r)
        if r["result"] == "PASS":
            passed += 1
        elif r["result"] == "FAIL":
            failed += 1
        else:
            skipped += 1

    summary = {
        "total": len(args.files),
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "overall": "PASS" if failed == 0 else "FAIL",
        "files": results,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
