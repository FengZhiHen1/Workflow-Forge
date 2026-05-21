#!/usr/bin/env python3
"""
工作流拉取脚本 (v3)

职责：
1. 扫描生产车间 artifacts/workflows/ 下的工作流
2. 按用户查询匹配工作流
3. 将工作流定义复制到 .claude/workflows/<id>/
4. 将配套 skills 复制到 .claude/skills/（跳过已存在的）
5. 将共享资源（references/、scripts/）复制到 .claude/workflows/<id>/
6. 自动解析 WORKFLOW.yaml 中的 workflow: 引用，递归拉取子工作流（最大深度 3）

v3 变化：源路径从 results/ 改为 artifacts/，目录名不含 @version。

调用方式：
    python pull_workflow.py --query <关键词或ID> [--target <目标目录>] [--dry-run] [--no-recursive]
"""

import argparse
import os
import re
import shutil
import sys
from pathlib import Path


DEFAULT_SOURCE = os.environ.get("WORKFLOW_FACTORY_ROOT", r"E:\Project\Workflows")
EXCLUDED_NAMES = {"__pycache__", ".git", ".tmp", ".venv", "venv", "node_modules"}
MAX_CHILD_DEPTH = 3  # 与 wfctl 子工作流嵌套深度上限一致


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def copy_tree(src: Path, dst: Path, dry_run: bool = False) -> None:
    """递归复制目录内容，跳过临时目录。"""
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


def _parse_workflow_id(dir_name: str) -> str:
    """从目录名提取 workflow_id。兼容旧版 @version 后缀。"""
    return dir_name.split("@")[0]


# ── 子工作流解析 ────────────────────────────────────────────

def extract_child_workflow_refs(yaml_path: Path) -> list[str]:
    """从 WORKFLOW.yaml 的 stages 中提取 workflow: 引用列表。"""
    if not yaml_path.exists():
        return []
    refs: list[str] = []
    for line in yaml_path.read_text(encoding="utf-8").split("\n"):
        if "#" in line:
            line = line.split("#")[0]
        m = re.match(r"^\s+workflow:\s+(.+?)\s*$", line)
        if not m:
            continue
        ref = m.group(1).strip()
        if ref and not ref.startswith("<"):
            refs.append(ref)
    return refs


def _parse_ref(ref: str) -> tuple[str, str | None]:
    """解析引用字符串为 (workflow_id, version_or_none)。"""
    if "@" in ref:
        wf_id, ver = ref.rsplit("@", 1)
        return wf_id, ver
    return ref, None


def find_factory_workflow_by_ref(ref: str, factory: list[dict]) -> dict | None:
    """按引用字符串在工厂工作流列表中查找。"""
    wf_id, ver = _parse_ref(ref)
    candidates = [f for f in factory if f["id"] == wf_id]
    if not candidates:
        return None
    if ver:
        for c in candidates:
            if c["dir_name"] == f"{wf_id}@{ver}":
                return c
        return None
    return sorted(candidates, key=lambda c: c["dir_name"])[-1]


def resolve_child_workflow_chain(
    wf_factory: dict,
    factory_wfs: list[dict],
    visited: set | None = None,
    depth: int = 0,
) -> list[dict]:
    """递归解析工作流的子工作流链（深度优先），返回子工作流列表（不含自身）。

    深度上限 MAX_CHILD_DEPTH（3），超过则截断并警告。检测循环引用。
    """
    if visited is None:
        visited = set()
    wf_id = wf_factory["id"]
    if wf_id in visited:
        print(f"[WARN] 检测到循环引用，跳过: {wf_id}")
        return []
    if depth >= MAX_CHILD_DEPTH:
        print(f"[WARN] 子工作流嵌套深度已达上限 {MAX_CHILD_DEPTH}，跳过: {wf_id}")
        return []

    visited.add(wf_id)
    yaml_path = wf_factory["path"] / "WORKFLOW.yaml"
    refs = extract_child_workflow_refs(yaml_path)
    children: list[dict] = []

    for ref in refs:
        child = find_factory_workflow_by_ref(ref, factory_wfs)
        if not child:
            print(f"[WARN] 未找到子工作流 '{ref}'，跳过")
            continue
        if child["id"] in visited:
            print(f"[WARN] 检测到循环引用，跳过: {child['id']}")
            continue
        grandchildren = resolve_child_workflow_chain(child, factory_wfs, visited.copy(), depth + 1)
        children.extend(grandchildren)
        if child not in children:
            children.append(child)

    return children


def scan_workflows(source_root: Path) -> list[dict]:
    """扫描生产车间 artifacts/workflows/，返回可用工作流列表。"""
    workflows_dir = source_root / "artifacts" / "workflows"
    if not workflows_dir.exists():
        return []
    results: list[dict] = []
    for item in sorted(workflows_dir.iterdir()):
        if not item.is_dir():
            continue
        wf_id = _parse_workflow_id(item.name)
        md_file = item / "WORKFLOW.md"
        yaml_file = item / "WORKFLOW.yaml"
        skills_dir = item / "skills"
        skill_count = sum(1 for s in skills_dir.iterdir() if s.is_dir()) if skills_dir.exists() else 0
        results.append({
            "id": wf_id,
            "dir_name": item.name,
            "path": item,
            "has_md": md_file.exists(),
            "has_yaml": yaml_file.exists(),
            "skill_count": skill_count,
        })
    return results


def match_workflow(query: str, workflows: list[dict]) -> list[dict]:
    """按查询词匹配工作流。支持精确匹配、前缀匹配、子串匹配。"""
    query_lower = query.lower().strip()
    # 精确匹配
    matches = [wf for wf in workflows if wf["id"].lower() == query_lower]
    if matches:
        return matches
    # 前缀匹配
    matches = [wf for wf in workflows if wf["id"].lower().startswith(query_lower)]
    if matches:
        return matches
    # 子串匹配
    return [wf for wf in workflows if query_lower in wf["id"].lower()]


def pull_workflow(wf: dict, target_root: Path, dry_run: bool = False) -> dict:
    """拉取单个工作流及其 skills 和共享资源，返回报告。"""
    report = {
        "workflow_id": wf["id"],
        "workflow_copied": False,
        "skills_copied": [],
        "skills_skipped": [],
        "shared_copied": [],
    }

    wf_src = wf["path"]
    wf_dst = target_root / ".claude" / "workflows" / wf["id"]

    if dry_run:
        print(f"[WORKFLOW] {wf_src} -> {wf_dst}")
    else:
        ensure_dir(wf_dst)

    # 1. 复制工作流定义
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

    # 2. 复制配套 skills（跳过已存在的）
    skills_src = wf_src / "skills"
    skills_dst = target_root / ".claude" / "skills"
    if skills_src.exists() and skills_src.is_dir():
        for skill_item in sorted(skills_src.iterdir()):
            if not skill_item.is_dir() or skill_item.name in EXCLUDED_NAMES:
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

    # 3. 复制共享资源（references/ 和 scripts/）
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


def pull_workflow_recursive(
    wf: dict,
    factory_wfs: list[dict],
    target_root: Path,
    dry_run: bool = False,
    no_recursive: bool = False,
) -> list[dict]:
    """递归拉取工作流：先子后父，返回所有报告列表。"""
    reports: list[dict] = []

    if not no_recursive:
        children = resolve_child_workflow_chain(wf, factory_wfs)
        for child in children:
            child_report = pull_workflow(child, target_root, dry_run=dry_run)
            child_report["_is_child"] = True
            reports.append(child_report)

    parent_report = pull_workflow(wf, target_root, dry_run=dry_run)
    parent_report["_is_child"] = False
    reports.append(parent_report)
    return reports


def main() -> None:
    parser = argparse.ArgumentParser(description="工作流拉取 (v3)")
    parser.add_argument("--query", required=True, help="工作流关键词或ID")
    parser.add_argument("--target", default=os.getcwd(), help="目标目录（默认当前目录）")
    parser.add_argument("--source", default=DEFAULT_SOURCE, help="工作流生产车间根目录")
    parser.add_argument("--dry-run", action="store_true", help="干运行，仅打印不执行")
    parser.add_argument("--no-recursive", action="store_true", help="不递归拉取子工作流")
    args = parser.parse_args()

    target_root = Path(args.target).resolve()
    source_root = Path(args.source).resolve()

    print(f"[INFO] 目标目录: {target_root}")
    print(f"[INFO] 源目录: {source_root}")
    print(f"[INFO] 查询: {args.query}")
    if args.dry_run:
        print("[INFO] 干运行模式，不执行实际写入")

    workflows = scan_workflows(source_root)
    if not workflows:
        print("[ERROR] 未在 artifacts/workflows/ 下发现任何工作流定义")
        sys.exit(1)

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

    # 先解析子工作流链（干运行时用于展示）
    children = [] if args.no_recursive else resolve_child_workflow_chain(wf, workflows)
    if children:
        print(f"\n[CHILD WORKFLOWS] 检测到 {len(children)} 个子工作流:")
        for child in children:
            print(f"  - {child['id']} ({child['dir_name']})")

    reports = pull_workflow_recursive(
        wf, workflows, target_root,
        dry_run=args.dry_run,
        no_recursive=args.no_recursive,
    )

    for report in reports:
        tag = " [子工作流]" if report.get("_is_child") else ""
        print(f"\n[REPORT] 拉取结果: {report['workflow_id']}{tag}")
        print(f"  工作流定义: {'已复制' if report['workflow_copied'] else '未复制'}")
        if report["skills_copied"]:
            print(f"  新增 Skills: {', '.join(report['skills_copied'])}")
        if report["skills_skipped"]:
            print(f"  跳过 Skills: {', '.join(report['skills_skipped'])}")
        if report["shared_copied"]:
            print(f"  共享资源: {', '.join(report['shared_copied'])}")

    print("[DONE] 工作流拉取完成")


if __name__ == "__main__":
    main()
