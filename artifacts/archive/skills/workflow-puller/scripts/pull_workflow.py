#!/usr/bin/env python3
"""
工作流拉取脚本

职责：
1. 扫描生产车间的工作流目录
2. 按用户查询匹配工作流
3. 将工作流定义复制到目标目录的 .claude/workflows/
4. 将工作流配套的 skills 复制到 .claude/skills/（跳过已存在的 skill）
5. 将工作流级共享资源（references/、scripts/）复制到 .claude/workflows/<id>/
6. 生成拉取报告

调用方式：
    python pull_workflow.py \
        --query <工作流关键词或ID> \
        [--target <目标目录>] \
        [--source <生产车间根目录>] \
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
EXCLUDED_NAMES = {"__pycache__", ".git", ".tmp", ".venv", "venv", "node_modules"}


def ensure_dir(path: Path):
    path.mkdir(parents=True, exist_ok=True)


def copy_tree(src: Path, dst: Path, dry_run: bool = False):
    """递归复制目录内容，保留结构"""
    if not src.exists():
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
                print(f"  [COPY] {item.name}")
            else:
                ensure_dir(target.parent)
                shutil.copy2(item, target)
                print(f"  [COPY] {item.name}")


def scan_workflows(source_root: Path) -> list[dict]:
    """扫描生产车间，返回可用工作流列表"""
    workflows_dir = source_root / "results" / "workflows"
    if not workflows_dir.exists():
        return []
    results = []
    for item in workflows_dir.iterdir():
        if not item.is_dir():
            continue
        wf_id = item.name
        md_file = item / "WORKFLOW.md"
        yaml_file = item / "WORKFLOW.yaml"
        has_md = md_file.exists()
        has_yaml = yaml_file.exists()
        skills_dir = item / "skills"
        skill_count = sum(1 for _ in skills_dir.iterdir() if _.is_dir()) if skills_dir.exists() else 0
        results.append({
            "id": wf_id,
            "path": item,
            "has_md": has_md,
            "has_yaml": has_yaml,
            "skill_count": skill_count,
        })
    return results


def match_workflow(query: str, workflows: list[dict]) -> list[dict]:
    """按查询词匹配工作流，返回匹配列表"""
    query_lower = query.lower().strip()
    matches = []
    for wf in workflows:
        wf_id_lower = wf["id"].lower()
        # 精确匹配或前缀匹配
        if wf_id_lower == query_lower or wf_id_lower.startswith(query_lower):
            matches.append(wf)
    # 如果精确/前缀匹配不到，尝试子串匹配
    if not matches:
        for wf in workflows:
            if query_lower in wf["id"].lower():
                matches.append(wf)
    return matches


def pull_workflow(wf: dict, target_root: Path, dry_run: bool = False) -> dict:
    """拉取单个工作流及其 skills 和共享资源，返回报告"""
    report = {
        "workflow_id": wf["id"],
        "workflow_copied": False,
        "skills_copied": [],
        "skills_skipped": [],
        "shared_copied": [],
    }

    # 1. 复制工作流定义
    wf_src = wf["path"]
    wf_dst = target_root / ".claude" / "workflows" / wf["id"]
    if dry_run:
        print(f"[WORKFLOW] {wf_src} -> {wf_dst}")
    else:
        ensure_dir(wf_dst)

    for fname in ["WORKFLOW.md", "WORKFLOW.yaml"]:
        src_file = wf_src / fname
        if src_file.exists():
            dst_file = wf_dst / fname
            if dry_run:
                print(f"  [COPY] {fname}")
            else:
                shutil.copy2(src_file, dst_file)
                print(f"  [COPY] {fname}")
            report["workflow_copied"] = True

    # 2. 复制工作流配套的 skills
    skills_src = wf_src / "skills"
    skills_dst = target_root / ".claude" / "skills"
    if skills_src.exists() and skills_src.is_dir():
        for skill_item in skills_src.iterdir():
            if not skill_item.is_dir():
                continue
            if skill_item.name in EXCLUDED_NAMES:
                continue
            target_skill = skills_dst / skill_item.name
            if target_skill.exists():
                report["skills_skipped"].append(skill_item.name)
                print(f"  [SKIP] Skill '{skill_item.name}' 已存在，未覆盖")
                continue
            if dry_run:
                print(f"  [SKILL] {skill_item.name}")
            else:
                ensure_dir(target_skill)
            copy_tree(skill_item, target_skill, dry_run)
            report["skills_copied"].append(skill_item.name)

    # 3. 复制工作流级共享资源（references/ 和 scripts/）
    for shared_dir_name in ["references", "scripts"]:
        shared_src = wf_src / shared_dir_name
        if shared_src.exists() and shared_src.is_dir():
            shared_dst = wf_dst / shared_dir_name
            if dry_run:
                print(f"  [SHARED] {shared_dir_name}/")
                copy_tree(shared_src, shared_dst, dry_run)
            else:
                if shared_dst.exists():
                    shutil.rmtree(shared_dst)
                copy_tree(shared_src, shared_dst, dry_run)
            report["shared_copied"].append(shared_dir_name)

    return report


def main():
    parser = argparse.ArgumentParser(description="工作流拉取")
    parser.add_argument("--query", required=True, help="工作流关键词或ID（如 'mathematical-model'）")
    parser.add_argument("--target", default=os.getcwd(), help="目标目录（默认当前目录）")
    parser.add_argument("--source", default=DEFAULT_SOURCE, help="工作流生产车间根目录")
    parser.add_argument("--dry-run", action="store_true", help="干运行，仅打印操作不执行")
    args = parser.parse_args()

    target_root = Path(args.target).resolve()
    source_root = Path(args.source).resolve()

    print(f"[INFO] 目标目录: {target_root}")
    print(f"[INFO] 源目录: {source_root}")
    print(f"[INFO] 查询: {args.query}")
    if args.dry_run:
        print("[INFO] 干运行模式，不执行实际写入")

    # 扫描可用工作流
    workflows = scan_workflows(source_root)
    if not workflows:
        print("[ERROR] 未在源目录发现任何工作流定义")
        sys.exit(1)

    # 匹配工作流
    matches = match_workflow(args.query, workflows)
    if not matches:
        print(f"[ERROR] 未找到匹配 '{args.query}' 的工作流")
        print("[INFO] 可用工作流列表:")
        for wf in workflows:
            print(f"  - {wf['id']} (skills: {wf['skill_count']})")
        sys.exit(1)

    if len(matches) > 1:
        print(f"[WARN] 查询 '{args.query}' 匹配到多个工作流，请选择其中一个:")
        for i, wf in enumerate(matches, 1):
            print(f"  {i}. {wf['id']} (skills: {wf['skill_count']})")
        sys.exit(1)

    wf = matches[0]
    print(f"[INFO] 匹配到工作流: {wf['id']}")

    # 拉取
    report = pull_workflow(wf, target_root, args.dry_run)

    # 报告
    print("\n[REPORT] 拉取结果:")
    print(f"  工作流: {report['workflow_id']}")
    print(f"  工作流定义: {'已复制' if report['workflow_copied'] else '未复制'}")
    if report['skills_copied']:
        print(f"  新增 Skills: {', '.join(report['skills_copied'])}")
    if report['skills_skipped']:
        print(f"  跳过 Skills: {', '.join(report['skills_skipped'])}")
    if report['shared_copied']:
        print(f"  共享资源: {', '.join(report['shared_copied'])}")
    if not report['skills_copied'] and not report['skills_skipped']:
        print("  配套 Skills: 无")

    print("[DONE] 工作流拉取完成")


if __name__ == "__main__":
    main()
