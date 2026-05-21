#!/usr/bin/env python3
"""
Python import 完整性检查器。

对修改过的 Python 文件，解析其顶层 import 语句并尝试解析模块路径，
确保合并后的文件不会因 import 失败而无法运行。

仅处理 Python 文件（.py），非 Python 文件直接 SKIP。

调用方式：
    python check_imports.py --worktree <path> --files <file1> <file2> ...
"""

import argparse
import ast
import importlib.util
import json
import sys
from pathlib import Path


def extract_top_level_imports(filepath: Path) -> list[str]:
    """从 Python 文件中提取顶层 import 的模块名列表。"""
    try:
        tree = ast.parse(filepath.read_text(encoding="utf-8"), filename=str(filepath))
    except SyntaxError:
        return []  # 语法错误由 verify_syntax 处理，这里不重复报

    modules: list[str] = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.append(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                modules.append(node.module.split(".")[0])
    return modules


def check_module_resolvable(module_name: str, worktree: Path) -> dict:
    """尝试解析模块。返回 {module, resolvable, reason}。"""
    try:
        spec = importlib.util.find_spec(module_name)
        if spec is not None:
            return {"module": module_name, "resolvable": True, "reason": ""}
        return {"module": module_name, "resolvable": False, "reason": f"未找到模块 '{module_name}'"}
    except (ImportError, ModuleNotFoundError) as e:
        return {"module": module_name, "resolvable": False, "reason": str(e)}
    except Exception as e:
        return {"module": module_name, "resolvable": False, "reason": f"解析异常: {e}"}


def check_file(filepath: Path, worktree: Path) -> dict:
    """检查单个 Python 文件的 import 完整性。"""
    if filepath.suffix.lower() != ".py":
        return {"file": str(filepath), "result": "SKIP", "reason": "非 Python 文件"}

    modules = extract_top_level_imports(filepath)
    if not modules:
        return {"file": str(filepath), "result": "PASS", "reason": "无顶层 import"}

    checks = [check_module_resolvable(m, worktree) for m in modules]
    unresolvable = [c for c in checks if not c["resolvable"]]
    if unresolvable:
        return {
            "file": str(filepath),
            "result": "FAIL",
            "modules_checked": len(checks),
            "unresolvable": unresolvable,
        }
    return {"file": str(filepath), "result": "PASS", "modules_checked": len(checks)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Python import 完整性检查器")
    parser.add_argument("--worktree", required=True, help="worktree 根路径")
    parser.add_argument("--files", nargs="*", default=[], help="要检查的文件列表（相对路径）")
    args = parser.parse_args()

    worktree = Path(args.worktree).resolve()
    # 将 worktree 加入 sys.path，使项目内部模块可解析
    sys.path.insert(0, str(worktree))

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
        r = check_file(fp, worktree)
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
