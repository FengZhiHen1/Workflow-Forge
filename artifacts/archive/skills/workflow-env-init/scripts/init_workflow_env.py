#!/usr/bin/env python3
"""
工作流环境初始化脚本

职责：
1. 在目标目录创建工作流标准目录结构
2. 从工作流生产车间拉取资源（契约、脚本、Skill、工作流定义）
3. 初始化运行时目录（.agent/ 目录）
4. 更新 .gitignore

调用方式：
    python init_workflow_env.py \
        [--target <目标目录>] \
        [--source <源目录>] \
        [--dry-run]

默认源目录：环境变量 WORKFLOW_FACTORY_ROOT 或 E:\Project\workflows
"""

import argparse
import json
import os
import shutil
import sys
from pathlib import Path


DEFAULT_SOURCE = os.environ.get("WORKFLOW_FACTORY_ROOT", r"E:\Project\workflows")

# 标准目录结构（相对于目标根目录）
STANDARD_DIRS = [
    ".claude/contracts",
    ".claude/skills",
    ".claude/workflows",
    ".claude/scripts",
    ".agent/workflows/instances",
    ".agent/messages",
    ".agent/backups",
    ".tmp",
]

# 资源映射：源相对路径 -> 目标相对路径
# 注意：工作流定义（workflows）不由 init 拉取，留给工作流编排器或用户按需配置
RESOURCE_MAP = {
    "results/contracts": ".claude/contracts",
    "results/scripts": ".claude/scripts",
    "results/skills": ".claude/skills",
}

# .gitignore 必须包含的规则
GITIGNORE_RULES = [
    ".agent/",
    ".claude/",
    ".tmp/",
]


EXCLUDED_NAMES = {"__pycache__", ".git", ".tmp", ".venv", "venv", "node_modules", "workflow-env-init-workspace"}


def ensure_dir(path: Path):
    path.mkdir(parents=True, exist_ok=True)


def copy_tree(src: Path, dst: Path, dry_run: bool = False):
    """递归复制目录内容，保留结构，排除 __pycache__ 等临时目录"""
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


def update_gitignore(target_root: Path, dry_run: bool = False):
    """更新 .gitignore，确保包含工作流运行时目录"""
    gitignore_path = target_root / ".gitignore"
    existing_lines = set()
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
    if not source_root.exists():
        print(f"[ERROR] 源目录不存在: {source_root}")
        return False
    required = ["results/contracts", "results/scripts", "results/skills", "results/workflows"]
    missing = [p for p in required if not (source_root / p).exists()]
    if missing:
        print(f"[ERROR] 源目录缺少必要子目录: {missing}")
        return False
    return True


def main():
    parser = argparse.ArgumentParser(description="工作流环境初始化")
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
        if src_rel == "results/skills":
            # Skill 复制：跳过已存在的 skill，并在报告中说明
            if not src.exists():
                print(f"[SKIP] 源不存在: {src}")
                continue
            if not dst.exists() and not args.dry_run:
                ensure_dir(dst)
            skipped_skills = []
            for item in src.iterdir():
                if item.name in EXCLUDED_NAMES:
                    continue
                target_skill = dst / item.name
                if target_skill.exists():
                    skipped_skills.append(item.name)
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
            if skipped_skills:
                print(f"[SKIP] 以下 Skill 已存在，未覆盖: {', '.join(skipped_skills)}")
        else:
            copy_tree(src, dst, args.dry_run)

    # 3. 更新 .gitignore
    update_gitignore(target_root, args.dry_run)

    print("[DONE] 工作流环境初始化完成")


if __name__ == "__main__":
    main()
