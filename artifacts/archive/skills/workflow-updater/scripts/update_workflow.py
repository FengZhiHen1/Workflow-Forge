#!/usr/bin/env python3
"""
工作流更新脚本

职责：
1. 扫描目标目录已安装的工作流/基础设施 Skill 和生产车间的最新版本
2. 对比差异，识别可更新的对象（工作流定义、配套 Skill、共享资源、基础设施 Skill）
3. 在用户确认后执行更新（支持干运行预览）
4. 生成更新报告（变更清单、冲突提示）

支持两类更新对象：
- 类型 A：工作流专属 Skill + 工作流定义 + 共享资源（源：results/workflows/<id>@<version>/）
- 类型 B：基础设施 Skill（源：results/skills/<skill_id>/，如 workflow-orchestrator）

工作流级共享资源（references/、scripts/）随工作流定义同步更新。

调用方式：
    # 检查所有已安装对象是否有更新
    python update_workflow.py --check

    # 更新指定工作流
    python update_workflow.py --query <workflow_id> [--target <目标目录>]

    # 更新基础设施 Skill（如 workflow-orchestrator）
    python update_workflow.py --query workflow-orchestrator

    # 干运行预览
    python update_workflow.py --query <id> --dry-run

默认源目录：环境变量 WORKFLOW_FACTORY_ROOT 或 E:\Project\workflows
"""

import argparse
import filecmp
import json
import os
import shutil
import sys
from pathlib import Path


DEFAULT_SOURCE = os.environ.get("WORKFLOW_FACTORY_ROOT", r"E:\Project\workflows")
EXCLUDED_NAMES = {"__pycache__", ".git", ".tmp", ".venv", "venv", "node_modules"}
WORKFLOW_DEF_FILES = {"WORKFLOW.md", "WORKFLOW.yaml"}


def ensure_dir(path: Path):
    path.mkdir(parents=True, exist_ok=True)


# ── 基础设施 Skill 扫描 ─────────────────────────────────────

def scan_factory_skills(source_root: Path) -> list[dict]:
    """扫描生产车间中的基础设施 Skill（results/skills/ 下直接放置的 Skill）"""
    skills_dir = source_root / "results" / "skills"
    if not skills_dir.exists():
        return []

    results = []
    for item in skills_dir.iterdir():
        if not item.is_dir() or item.name in EXCLUDED_NAMES:
            continue
        has_skill_md = (item / "SKILL.md").exists()
        if not has_skill_md:
            continue
        results.append({
            "skill_id": item.name,
            "path": item,
            "type": "infrastructure_skill",
        })
    return results


def scan_installed_skills(target_root: Path) -> list[dict]:
    """扫描目标目录中已安装的基础设施 Skill（.claude/skills/）"""
    skills_dir = target_root / ".claude" / "skills"
    if not skills_dir.exists():
        return []

    results = []
    for item in skills_dir.iterdir():
        if not item.is_dir() or item.name in EXCLUDED_NAMES:
            continue
        has_skill_md = (item / "SKILL.md").exists()
        if not has_skill_md:
            continue
        results.append({
            "skill_id": item.name,
            "path": item,
            "type": "infrastructure_skill",
        })
    return results


# ── 工作流扫描 ─────────────────────────────────────────────

def scan_installed_workflows(target_root: Path) -> list[dict]:
    """扫描目标目录中已安装的工作流"""
    workflows_dir = target_root / ".claude" / "workflows"
    if not workflows_dir.exists():
        return []

    results = []
    for item in workflows_dir.iterdir():
        if not item.is_dir():
            continue
        name = item.name
        if "@" in name:
            wf_id, version = name.rsplit("@", 1)
        else:
            wf_id, version = name, "unknown"

        has_md = (item / "WORKFLOW.md").exists()
        has_yaml = (item / "WORKFLOW.yaml").exists()

        skills = []
        skills_dir = item / "skills"
        if skills_dir.exists():
            for s in skills_dir.iterdir():
                if s.is_dir() and s.name not in EXCLUDED_NAMES:
                    skills.append(s.name)

        results.append({
            "dir_name": name,
            "workflow_id": wf_id,
            "version": version,
            "path": item,
            "has_md": has_md,
            "has_yaml": has_yaml,
            "skills": skills,
            "type": "workflow",
        })
    return results


def scan_factory_workflows(source_root: Path) -> list[dict]:
    """扫描生产车间中的所有工作流"""
    workflows_dir = source_root / "results" / "workflows"
    if not workflows_dir.exists():
        return []

    results = []
    for item in workflows_dir.iterdir():
        if not item.is_dir():
            continue
        name = item.name
        if "@" in name:
            wf_id, version = name.rsplit("@", 1)
        else:
            wf_id, version = name, "unknown"

        has_md = (item / "WORKFLOW.md").exists()
        has_yaml = (item / "WORKFLOW.yaml").exists()

        skills = []
        skills_dir = item / "skills"
        if skills_dir.exists():
            for s in skills_dir.iterdir():
                if s.is_dir() and s.name not in EXCLUDED_NAMES:
                    skills.append(s.name)

        results.append({
            "dir_name": name,
            "workflow_id": wf_id,
            "version": version,
            "path": item,
            "has_md": has_md,
            "has_yaml": has_yaml,
            "skills": skills,
            "type": "workflow",
        })
    return results


# ── 通用差异对比 ───────────────────────────────────────────

def compare_file(src: Path, dst: Path) -> bool:
    """对比两个文件是否相同，True=相同"""
    if not src.exists() or not dst.exists():
        return False
    return filecmp.cmp(src, dst, shallow=False)


def diff_directory(src: Path, dst: Path) -> dict:
    """
    对比两个目录的差异（用于 Skill 对比）。
    返回: {identical: bool, new_files: [], modified_files: [], removed_files: []}
    """
    result = {
        "identical": True,
        "new_files": [],
        "modified_files": [],
        "removed_files": [],
    }

    if not dst.exists():
        result["identical"] = False
        result["new_files"] = [str(p.relative_to(src)).replace("\\", "/")
                                for p in src.rglob("*") if p.is_file()]
        return result

    src_files = {p.relative_to(src): p for p in src.rglob("*") if p.is_file()}
    dst_files = {p.relative_to(dst): p for p in dst.rglob("*") if p.is_file()}

    for rel_path in src_files:
        if rel_path not in dst_files:
            result["new_files"].append(str(rel_path).replace("\\", "/"))
            result["identical"] = False

    for rel_path in dst_files:
        if rel_path not in src_files:
            result["removed_files"].append(str(rel_path).replace("\\", "/"))
            result["identical"] = False

    for rel_path, src_path in src_files.items():
        if rel_path in dst_files:
            if not filecmp.cmp(src_path, dst_files[rel_path], shallow=False):
                result["modified_files"].append(str(rel_path).replace("\\", "/"))
                result["identical"] = False

    return result


# ── 查找匹配 ───────────────────────────────────────────────

def find_factory_workflow(factory_workflows: list, query: str) -> dict | None:
    """按查询词匹配生产车间工作流，返回唯一匹配或 None"""
    query_lower = query.lower().strip()

    for wf in factory_workflows:
        if wf["dir_name"].lower() == query_lower:
            return wf

    matches = [wf for wf in factory_workflows if wf["workflow_id"].lower().startswith(query_lower)]
    if not matches:
        matches = [wf for wf in factory_workflows if query_lower in wf["workflow_id"].lower()]

    if len(matches) == 1:
        return matches[0]

    if len(matches) > 1:
        unique_ids = set(wf["workflow_id"] for wf in matches)
        if len(unique_ids) == 1:
            return max(matches, key=lambda x: x["version"])

    return None


def find_installed_workflow(installed: list, wf_id: str) -> dict | None:
    """按 workflow_id 查找已安装的工作流"""
    for wf in installed:
        if wf["workflow_id"] == wf_id:
            return wf
    return None


def find_factory_skill(factory_skills: list, query: str) -> dict | None:
    """按查询词匹配基础设施 Skill"""
    query_lower = query.lower().strip()
    for sk in factory_skills:
        if sk["skill_id"].lower() == query_lower:
            return sk
    for sk in factory_skills:
        if sk["skill_id"].lower().startswith(query_lower):
            return sk
    return None


def find_installed_skill(installed_skills: list, skill_id: str) -> dict | None:
    """按 skill_id 查找已安装的基础设施 Skill"""
    for sk in installed_skills:
        if sk["skill_id"] == skill_id:
            return sk
    return None


# ── 更新执行 ───────────────────────────────────────────────

def copy_tree_with_report(src: Path, dst: Path, dry_run: bool = False) -> list[str]:
    """递归复制并返回复制的文件列表"""
    copied = []
    if not src.exists():
        return copied
    if not dst.exists() and not dry_run:
        ensure_dir(dst)

    for item in src.iterdir():
        if item.name in EXCLUDED_NAMES:
            continue
        target = dst / item.name
        if item.is_dir():
            copied.extend(copy_tree_with_report(item, target, dry_run))
        else:
            if not dry_run:
                ensure_dir(target.parent)
                shutil.copy2(item, target)
            copied.append(str(item.relative_to(src)).replace("\\", "/"))
    return copied


def update_workflow(wf_factory: dict, target_root: Path, installed_wf: dict | None,
                    dry_run: bool = False, workflow_only: bool = False,
                    skills_only: bool = False) -> dict:
    """执行单个工作流的更新，返回报告"""
    report = {
        "target_type": "workflow",
        "workflow_id": wf_factory["workflow_id"],
        "target_version": wf_factory["version"],
        "actions": [],
    }

    wf_src = wf_factory["path"]

    if installed_wf:
        wf_dst = installed_wf["path"]
        report["previous_version"] = installed_wf["version"]
    else:
        wf_dst = target_root / ".claude" / "workflows" / wf_factory["dir_name"]
        report["previous_version"] = None
        if not dry_run:
            ensure_dir(wf_dst)

    # 1. 更新工作流定义
    if not skills_only:
        for fname in WORKFLOW_DEF_FILES:
            src_file = wf_src / fname
            dst_file = wf_dst / fname
            if src_file.exists():
                same = compare_file(src_file, dst_file)
                if same:
                    report["actions"].append(f"[SKIP] {fname} 无变化")
                else:
                    if not dry_run:
                        shutil.copy2(src_file, dst_file)
                    action = "[UPDATE]" if dst_file.exists() else "[COPY]"
                    report["actions"].append(f"{action} {fname}")

    # 2. 更新 Skills
    if not workflow_only:
        skills_src = wf_src / "skills"
        skills_dst = wf_dst / "skills"
        target_skills_root = target_root / ".claude" / "skills"

        if skills_src.exists():
            for skill_item in skills_src.iterdir():
                if not skill_item.is_dir() or skill_item.name in EXCLUDED_NAMES:
                    continue

                skill_name = skill_item.name
                wf_skill_dst = skills_dst / skill_name
                global_skill_dst = target_skills_root / skill_name

                diff = diff_directory(skill_item, global_skill_dst)

                if diff["identical"]:
                    report["actions"].append(f"[SKIP] Skill '{skill_name}' 无变化")
                    continue

                changes = []
                if diff["new_files"]:
                    changes.append(f"新增 {len(diff['new_files'])} 个文件")
                if diff["modified_files"]:
                    changes.append(f"修改 {len(diff['modified_files'])} 个文件")
                if diff["removed_files"]:
                    changes.append(f"删除 {len(diff['removed_files'])} 个文件")

                if not dry_run:
                    if wf_skill_dst.exists():
                        shutil.rmtree(wf_skill_dst)
                    copy_tree_with_report(skill_item, wf_skill_dst)
                    if global_skill_dst.exists():
                        shutil.rmtree(global_skill_dst)
                    copy_tree_with_report(skill_item, global_skill_dst)

                report["actions"].append(f"[UPDATE] Skill '{skill_name}' ({', '.join(changes)})")

    # 3. 同步工作流级共享资源（references/ 和 scripts/）
    if not skills_only:
        for shared_dir_name in ["references", "scripts"]:
            shared_src = wf_src / shared_dir_name
            if not shared_src.exists() or not shared_src.is_dir():
                continue
            shared_dst = wf_dst / shared_dir_name

            diff = diff_directory(shared_src, shared_dst)

            if diff["identical"]:
                report["actions"].append(f"[SKIP] 共享资源 '{shared_dir_name}/' 无变化")
                continue

            changes = []
            if diff["new_files"]:
                changes.append(f"新增 {len(diff['new_files'])} 个文件")
            if diff["modified_files"]:
                changes.append(f"修改 {len(diff['modified_files'])} 个文件")
            if diff["removed_files"]:
                changes.append(f"删除 {len(diff['removed_files'])} 个文件")

            if not dry_run:
                if shared_dst.exists():
                    shutil.rmtree(shared_dst)
                copy_tree_with_report(shared_src, shared_dst)

            report["actions"].append(f"[UPDATE] 共享资源 '{shared_dir_name}/' ({', '.join(changes)})")

    return report


def update_infrastructure_skill(skill_factory: dict, target_root: Path, installed_skill: dict | None,
                                dry_run: bool = False) -> dict:
    """执行基础设施 Skill 的更新，返回报告"""
    report = {
        "target_type": "infrastructure_skill",
        "skill_id": skill_factory["skill_id"],
        "actions": [],
    }

    src = skill_factory["path"]
    dst = target_root / ".claude" / "skills" / skill_factory["skill_id"]

    diff = diff_directory(src, dst)

    if diff["identical"]:
        report["actions"].append("[SKIP] 无变化")
        return report

    changes = []
    if diff["new_files"]:
        changes.append(f"新增 {len(diff['new_files'])} 个文件")
    if diff["modified_files"]:
        changes.append(f"修改 {len(diff['modified_files'])} 个文件")
    if diff["removed_files"]:
        changes.append(f"删除 {len(diff['removed_files'])} 个文件")

    if not dry_run:
        if dst.exists():
            shutil.rmtree(dst)
        copy_tree_with_report(src, dst)

    report["actions"].append(f"[UPDATE] 基础设施 Skill '{skill_factory['skill_id']}' ({', '.join(changes)})")
    return report


# ── 检查更新 ───────────────────────────────────────────────

def check_workflow_updates(installed: list, factory: list) -> list[dict]:
    """对比已安装与生产车间，返回可更新的工作流列表"""
    updates = []
    for inst in installed:
        wf_id = inst["workflow_id"]
        factory_versions = [f for f in factory if f["workflow_id"] == wf_id]
        if not factory_versions:
            continue
        latest = max(factory_versions, key=lambda x: x["version"])
        if latest["version"] != inst["version"]:
            updates.append({
                "type": "workflow",
                "id": wf_id,
                "installed_version": inst["version"],
                "latest_version": latest["version"],
            })
    return updates


def check_skill_updates(installed_skills: list, factory_skills: list) -> list[dict]:
    """对比已安装与生产车间，返回可更新的基础设施 Skill 列表"""
    updates = []
    for inst in installed_skills:
        sk_id = inst["skill_id"]
        factory_match = next((f for f in factory_skills if f["skill_id"] == sk_id), None)
        if not factory_match:
            continue
        diff = diff_directory(factory_match["path"], inst["path"])
        if not diff["identical"]:
            changes = []
            if diff["new_files"]:
                changes.append(f"新增 {len(diff['new_files'])}")
            if diff["modified_files"]:
                changes.append(f"修改 {len(diff['modified_files'])}")
            if diff["removed_files"]:
                changes.append(f"删除 {len(diff['removed_files'])}")
            updates.append({
                "type": "infrastructure_skill",
                "id": sk_id,
                "changes": ", ".join(changes),
            })
    return updates


# ── 主函数 ─────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="工作流更新工具")
    parser.add_argument("--target", default=os.getcwd(), help="目标目录（默认当前目录）")
    parser.add_argument("--source", default=DEFAULT_SOURCE, help="工作流生产车间根目录")
    parser.add_argument("--query", help="工作流 ID、Skill ID 或关键词")
    parser.add_argument("--check", action="store_true", help="检查所有已安装对象是否有更新")
    parser.add_argument("--dry-run", action="store_true", help="干运行，仅打印操作不执行")
    parser.add_argument("--workflow-only", action="store_true", help="仅更新工作流定义（不碰 Skill）")
    parser.add_argument("--skills-only", action="store_true", help="仅更新配套 Skill（工作流模式下）")
    parser.add_argument("--force", action="store_true", help="强制覆盖，不提示确认")
    args = parser.parse_args()

    target_root = Path(args.target).resolve()
    source_root = Path(args.source).resolve()

    print(f"[INFO] 目标目录: {target_root}")
    print(f"[INFO] 源目录: {source_root}")
    if args.dry_run:
        print("[INFO] 干运行模式，不执行实际写入")

    # 扫描所有源
    installed_workflows = scan_installed_workflows(target_root)
    factory_workflows = scan_factory_workflows(source_root)
    installed_skills = scan_installed_skills(target_root)
    factory_skills = scan_factory_skills(source_root)

    # 模式 1：--check 检查更新
    if args.check:
        wf_updates = check_workflow_updates(installed_workflows, factory_workflows)
        sk_updates = check_skill_updates(installed_skills, factory_skills)

        if not wf_updates and not sk_updates:
            print("[OK] 所有已安装工作流和 Skill 均为最新")
            sys.exit(0)

        if wf_updates:
            print(f"\n[WORKFLOW UPDATES] 发现 {len(wf_updates)} 个工作流可更新:")
            for u in wf_updates:
                print(f"  - {u['id']}: {u['installed_version']} → {u['latest_version']}")

        if sk_updates:
            print(f"\n[SKILL UPDATES] 发现 {len(sk_updates)} 个基础设施 Skill 可更新:")
            for u in sk_updates:
                print(f"  - {u['id']}: {u['changes']}")
        sys.exit(0)

    # 模式 2：--query 更新指定对象
    if not args.query:
        print("[ERROR] 请指定 --query <id> 或 --check")
        sys.exit(1)

    query = args.query.lower().strip()

    # 同时查询两类对象
    matched_skill = find_factory_skill(factory_skills, query)
    matched_workflow = find_factory_workflow(factory_workflows, query)

    # 判定用户意图
    target_type = None

    if matched_skill and matched_workflow:
        # 两边都匹配：向用户展示选项（但在脚本层面，优先按 Skill 处理，因为用户明确说了"workflow-orchestrator"）
        # 实际上 workflow-orchestrator 不太可能同时也是工作流名，这里留个安全判断
        print(f"[AMBIGUOUS] '{args.query}' 同时匹配到:")
        print(f"  A. 基础设施 Skill: {matched_skill['skill_id']}")
        print(f"  B. 工作流: {matched_workflow['dir_name']}")
        print("[ERROR] 请使用更精确的 --query 指定，或配合 --workflow-only / --skills-only")
        sys.exit(1)

    elif matched_skill:
        target_type = "skill"
    elif matched_workflow:
        target_type = "workflow"
    else:
        print(f"[ERROR] 未找到匹配 '{args.query}' 的对象")
        print("[INFO] 可用工作流:")
        for wf in factory_workflows:
            print(f"  - {wf['dir_name']}")
        print("[INFO] 可用基础设施 Skill:")
        for sk in factory_skills:
            print(f"  - {sk['skill_id']}")
        sys.exit(1)

    # 执行更新
    if target_type == "skill":
        installed = find_installed_skill(installed_skills, matched_skill["skill_id"])
        report = update_infrastructure_skill(
            matched_skill, target_root, installed,
            dry_run=args.dry_run,
        )
        print(f"\n[REPORT] 更新结果: {report['skill_id']} (基础设施 Skill)")
        for action in report["actions"]:
            print(f"  {action}")

    elif target_type == "workflow":
        installed_wf = find_installed_workflow(installed_workflows, matched_workflow["workflow_id"])

        if installed_wf and installed_wf["version"] == matched_workflow["version"]:
            print(f"[INFO] 工作流 '{matched_workflow['workflow_id']}' 已是最新版本 ({matched_workflow['version']})")
            print("[INFO] 仍执行差异检查...")

        report = update_workflow(
            matched_workflow, target_root, installed_wf,
            dry_run=args.dry_run,
            workflow_only=args.workflow_only,
            skills_only=args.skills_only,
        )
        print(f"\n[REPORT] 更新结果: {report['workflow_id']} (工作流)")
        if report.get("previous_version"):
            print(f"  版本: {report['previous_version']} → {report['target_version']}")
        else:
            print(f"  版本: 新安装 {report['target_version']}")
        for action in report["actions"]:
            print(f"  {action}")

    if not report["actions"]:
        print("  无任何变更")

    print("[DONE] 更新完成")


if __name__ == "__main__":
    main()
