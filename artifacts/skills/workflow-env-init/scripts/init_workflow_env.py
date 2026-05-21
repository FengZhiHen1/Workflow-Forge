#!/usr/bin/env python3
"""
工作流环境初始化脚本 (v3)

职责：
1. 在目标目录创建 v3 工作流标准目录结构
2. 从工作流生产车间 artifacts/ 拉取资源（契约、wfctl、Skill）
3. 初始化运行时目录（.agent/instances/）
4. 更新 .gitignore（.agent/ 和 .tmp/，不含 .claude/）

调用方式：
    python init_workflow_env.py \
        [--target <目标目录>] \
        [--source <源目录>] \
        [--dry-run]

默认源目录：环境变量 WORKFLOW_FACTORY_ROOT 或在脚本中配置。
"""

import argparse
import os
import shutil
import sys
from pathlib import Path


# 默认生产车间根目录
DEFAULT_SOURCE = os.environ.get("WORKFLOW_FACTORY_ROOT", r"E:\Project\Workflows")

# v3 标准目录结构（相对于目标根目录）
STANDARD_DIRS = [
    ".claude/contracts",
    ".claude/scripts",
    ".claude/skills",
    ".claude/workflows",
    ".agent/instances",
    ".tmp/worktrees",
]

# 资源映射：源相对路径（相对于 source_root） -> 目标相对路径（相对于 target_root）
RESOURCE_MAP = {
    "artifacts/contracts": ".claude/contracts",
    "artifacts/scripts": ".claude/scripts",
    "artifacts/skills": ".claude/skills",
}

# .gitignore 必须包含的规则（v3：.claude/ 纳入版本控制）
GITIGNORE_RULES = [
    ".agent/",
    ".tmp/",
]

# 递归复制时跳过的目录名
EXCLUDED_NAMES = {
    "__pycache__", ".git", ".tmp", ".venv", "venv",
    "node_modules", ".pytest_cache", ".mypy_cache",
}


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def copy_tree(src: Path, dst: Path, dry_run: bool = False) -> None:
    """递归复制目录内容，跳过临时目录。"""
    if not src.exists():
        print(f"[SKIP] 源不存在: {src}")
        return
    if not dst.exists() and not dry_run:
        ensure_dir(dst)
    for item in src.iterdir():
        if item.name in EXCLUDED_NAMES:
            continue
        target = dst / item.name
        if item.is_dir():
            copy_tree(item, target, dry_run)
        else:
            if dry_run:
                print(f"[COPY] {item} -> {target}")
            else:
                ensure_dir(target.parent)
                shutil.copy2(item, target)
                print(f"[COPY] {item.name}")


def update_gitignore(target_root: Path, dry_run: bool = False) -> None:
    """更新 .gitignore，确保包含 .agent/ 和 .tmp/，不含 .claude/。"""
    gitignore_path = target_root / ".gitignore"
    existing_lines: set[str] = set()
    if gitignore_path.exists():
        with open(gitignore_path, "r", encoding="utf-8") as f:
            existing_lines = {line.strip() for line in f}

    additions = [rule for rule in GITIGNORE_RULES if rule not in existing_lines]
    if not additions:
        print("[SKIP] .gitignore 已包含必要规则")
        return

    if not dry_run:
        with open(gitignore_path, "a", encoding="utf-8") as f:
            if existing_lines and "" not in existing_lines:
                f.write("\n")
            for rule in additions:
                f.write(f"{rule}\n")
    for rule in additions:
        print(f"[GITIGNORE] + {rule}")


def validate_source(source_root: Path) -> bool:
    """验证源目录结构完整性。"""
    if not source_root.exists():
        print(f"[ERROR] 源目录不存在: {source_root}")
        return False
    required = ["artifacts/contracts", "artifacts/scripts", "artifacts/skills"]
    missing = [p for p in required if not (source_root / p).exists()]
    if missing:
        print(f"[ERROR] 源目录缺少必要子目录: {missing}")
        return False
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="工作流环境初始化 (v3)")
    parser.add_argument("--target", default=os.getcwd(), help="目标目录（默认当前目录）")
    parser.add_argument("--source", default=DEFAULT_SOURCE, help="工作流生产车间根目录")
    parser.add_argument("--dry-run", action="store_true", help="干运行，仅打印操作不执行")
    args = parser.parse_args()

    target_root = Path(args.target).resolve()
    source_root = Path(args.source).resolve()

    print(f"[INFO] 目标目录: {target_root}")
    print(f"[INFO] 源目录: {source_root}")
    if args.dry_run:
        print("[INFO] 干运行模式，不执行实际写入")

    if not validate_source(source_root):
        sys.exit(1)

    # 1. 创建标准目录
    for rel_dir in STANDARD_DIRS:
        d = target_root / rel_dir
        if args.dry_run:
            print(f"[MKDIR] {d}")
        else:
            ensure_dir(d)
            print(f"[MKDIR] {rel_dir}")

    # 2. 拉取资源
    for src_rel, dst_rel in RESOURCE_MAP.items():
        src = source_root / src_rel
        dst = target_root / dst_rel
        if not src.exists():
            print(f"[SKIP] 源不存在: {src}")
            continue
        if not dst.exists() and not args.dry_run:
            ensure_dir(dst)

        if src_rel == "artifacts/skills":
            # Skill 复制：跳过已存在的 Skill，避免覆盖用户的本地修改
            skipped: list[str] = []
            for item in src.iterdir():
                if item.name in EXCLUDED_NAMES:
                    continue
                target_skill = dst / item.name
                if target_skill.exists():
                    skipped.append(item.name)
                    continue
                if item.is_dir():
                    copy_tree(item, target_skill, args.dry_run)
                else:
                    if args.dry_run:
                        print(f"[COPY] {item} -> {target_skill}")
                    else:
                        ensure_dir(target_skill.parent)
                        shutil.copy2(item, target_skill)
                        print(f"[COPY] {item.name}")
            if skipped:
                print(f"[SKIP] 以下 Skill 已存在，未覆盖: {', '.join(skipped)}")
        else:
            copy_tree(src, dst, args.dry_run)

    # 3. 更新 .gitignore
    update_gitignore(target_root, args.dry_run)

    print("[DONE] 工作流环境初始化完成 (v3)")


if __name__ == "__main__":
    main()
