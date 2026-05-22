#!/usr/bin/env python3
"""
工作流更新脚本 (v3)

职责：
1. 扫描目标目录已安装的工作流/Skill 和生产车间最新版本
2. 对比差异，识别可更新的对象
3. 在用户确认后执行更新
4. 自动解析 WORKFLOW.yaml 中的 workflow: 引用，递归更新子工作流（最大深度 3）

v3 变化：源路径从 results/ 改为 artifacts/；目录名不含 @version。

调用方式：
    python update_workflow.py --check [--target <目录>] [--no-recursive]
    python update_workflow.py --query <id> [--target <目录>] [--dry-run] [--no-recursive]
"""

import argparse
import filecmp
import os
import re
import shutil
import sys
from pathlib import Path


DEFAULT_SOURCE = os.environ.get("WORKFLOW_FACTORY_ROOT", r"E:\Project\Workflows")
EXCLUDED_NAMES = {"__pycache__", ".git", ".tmp", ".venv", "venv", "node_modules"}
WORKFLOW_DEF_FILES = {"WORKFLOW.md", "WORKFLOW.yaml"}
MAX_CHILD_DEPTH = 3  # 与 wfctl 子工作流嵌套深度上限一致


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _parse_workflow_id(dir_name: str) -> str:
    """从目录名提取 workflow_id。兼容旧版 @version 后缀。"""
    return dir_name.split("@")[0]


# ── 子工作流解析 ────────────────────────────────────────────

def extract_child_workflow_refs(yaml_path: Path) -> list[str]:
    """从 WORKFLOW.yaml 的 stages 中提取 workflow: 引用列表。

    每个引用格式为 '<workflow_id>@<version>'（精确版本）或 '<workflow_id>'（浮动最新）。
    """
    if not yaml_path.exists():
        return []
    refs: list[str] = []
    for line in yaml_path.read_text(encoding="utf-8").split("\n"):
        # 去掉行内注释
        if "#" in line:
            line = line.split("#")[0]
        m = re.match(r"^\s+workflow:\s+(.+?)\s*$", line)
        if not m:
            continue
        ref = m.group(1).strip()
        # 跳过模板占位符 <...> 和空值
        if ref and not ref.startswith("<"):
            refs.append(ref)
    return refs


def extract_skill_refs(yaml_path: Path) -> list[str]:
    """从 WORKFLOW.yaml 的 stages 中提取 skill_id 引用列表（去重）。"""
    if not yaml_path.exists():
        return []
    refs: list[str] = []
    for line in yaml_path.read_text(encoding="utf-8").split("\n"):
        if "#" in line:
            line = line.split("#")[0]
        m = re.match(r"^\s+skill_id:\s+(.+?)\s*$", line)
        if not m:
            continue
        ref = m.group(1).strip()
        if ref and not ref.startswith("<"):
            refs.append(ref)
    return list(set(refs))


def _parse_ref(ref: str) -> tuple[str, str | None]:
    """解析引用字符串为 (workflow_id, version_or_none)。"""
    if "@" in ref:
        wf_id, ver = ref.rsplit("@", 1)
        return wf_id, ver
    return ref, None


def find_factory_workflow_by_ref(ref: str, factory: list[dict]) -> dict | None:
    """按引用字符串在工厂工作流列表中查找。

    若有 @version 精确匹配；若无 version 取匹配 workflow_id 的最新版本（简单字符串比较）。
    """
    wf_id, ver = _parse_ref(ref)
    candidates = [f for f in factory if f["workflow_id"] == wf_id]
    if not candidates:
        return None
    if ver:
        for c in candidates:
            if c["dir_name"] == f"{wf_id}@{ver}" or c.get("version") == ver:
                return c
        return None
    # 无版本：取最新（按 dir_name 排序，最后一个通常是最新版本）
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
    wf_id = wf_factory["workflow_id"]
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
            wf_id_ref, ver = _parse_ref(ref)
            print(f"[WARN] 未找到子工作流 '{ref}'，跳过")
            continue
        if child["workflow_id"] in visited:
            print(f"[WARN] 检测到循环引用，跳过: {child['workflow_id']}")
            continue
        # 先递归收集孙子工作流
        grandchildren = resolve_child_workflow_chain(child, factory_wfs, visited.copy(), depth + 1)
        children.extend(grandchildren)
        if child not in children:
            children.append(child)

    return children


# ── 扫描 ────────────────────────────────────────────────────

def scan_factory_skills(source_root: Path) -> list[dict]:
    """扫描生产车间 artifacts/skills/ 下的基础设施 Skill。"""
    skills_dir = source_root / "artifacts" / "skills"
    if not skills_dir.exists():
        return []
    results: list[dict] = []
    for item in sorted(skills_dir.iterdir()):
        if not item.is_dir() or item.name in EXCLUDED_NAMES:
            continue
        if not (item / "SKILL.md").exists():
            continue
        results.append({"skill_id": item.name, "path": item, "type": "infrastructure_skill"})
    return results


def scan_installed_skills(target_root: Path) -> list[dict]:
    """扫描目标目录 .claude/skills/ 下已安装的 Skill。"""
    skills_dir = target_root / ".claude" / "skills"
    if not skills_dir.exists():
        return []
    results: list[dict] = []
    for item in sorted(skills_dir.iterdir()):
        if not item.is_dir() or item.name in EXCLUDED_NAMES:
            continue
        if not (item / "SKILL.md").exists():
            continue
        results.append({"skill_id": item.name, "path": item, "type": "infrastructure_skill"})
    return results


def scan_installed_workflows(target_root: Path) -> list[dict]:
    """扫描目标目录 .claude/workflows/ 下已安装的工作流。"""
    workflows_dir = target_root / ".claude" / "workflows"
    if not workflows_dir.exists():
        return []
    results: list[dict] = []
    for item in sorted(workflows_dir.iterdir()):
        if not item.is_dir():
            continue
        wf_id = _parse_workflow_id(item.name)
        skills: list[str] = []
        skills_dir = item / "skills"
        if skills_dir.exists():
            skills = [s.name for s in skills_dir.iterdir()
                      if s.is_dir() and s.name not in EXCLUDED_NAMES]
        results.append({
            "dir_name": item.name,
            "workflow_id": wf_id,
            "path": item,
            "has_md": (item / "WORKFLOW.md").exists(),
            "has_yaml": (item / "WORKFLOW.yaml").exists(),
            "skills": skills,
            "type": "workflow",
        })
    return results


def scan_factory_workflows(source_root: Path) -> list[dict]:
    """扫描生产车间 artifacts/workflows/ 下的所有工作流。"""
    workflows_dir = source_root / "artifacts" / "workflows"
    if not workflows_dir.exists():
        return []
    results: list[dict] = []
    for item in sorted(workflows_dir.iterdir()):
        if not item.is_dir():
            continue
        wf_id = _parse_workflow_id(item.name)
        skills: list[str] = []
        skills_dir = item / "skills"
        if skills_dir.exists():
            skills = [s.name for s in skills_dir.iterdir()
                      if s.is_dir() and s.name not in EXCLUDED_NAMES]
        results.append({
            "dir_name": item.name,
            "workflow_id": wf_id,
            "path": item,
            "has_md": (item / "WORKFLOW.md").exists(),
            "has_yaml": (item / "WORKFLOW.yaml").exists(),
            "skills": skills,
            "type": "workflow",
        })
    return results


# ── 差异对比 ────────────────────────────────────────────────

def diff_directory(src: Path, dst: Path) -> dict:
    """对比两个目录差异。返回 {identical, new_files, modified_files, removed_files}。"""
    result: dict = {"identical": True, "new_files": [], "modified_files": [], "removed_files": []}

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


def copy_tree_with_report(src: Path, dst: Path, dry_run: bool = False) -> list[str]:
    """递归复制并返回复制的文件相对路径列表。"""
    copied: list[str] = []
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


# ── 查找匹配 ────────────────────────────────────────────────

def find_factory_workflow(factory: list, query: str) -> dict | None:
    """按查询词匹配生产车间工作流。"""
    query_lower = query.lower().strip()
    for wf in factory:
        if wf["workflow_id"].lower() == query_lower:
            return wf
    matches = [wf for wf in factory if wf["workflow_id"].lower().startswith(query_lower)]
    if not matches:
        matches = [wf for wf in factory if query_lower in wf["workflow_id"].lower()]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        unique_ids = {wf["workflow_id"] for wf in matches}
        if len(unique_ids) == 1:
            return matches[0]
    return None


def find_installed_workflow(installed: list, wf_id: str) -> dict | None:
    for wf in installed:
        if wf["workflow_id"] == wf_id:
            return wf
    return None


def find_factory_skill(factory: list, query: str) -> dict | None:
    query_lower = query.lower().strip()
    for sk in factory:
        if sk["skill_id"].lower() == query_lower:
            return sk
    for sk in factory:
        if sk["skill_id"].lower().startswith(query_lower):
            return sk
    return None


def find_installed_skill(installed: list, skill_id: str) -> dict | None:
    for sk in installed:
        if sk["skill_id"] == skill_id:
            return sk
    return None


# ── 更新执行 ────────────────────────────────────────────────

def update_workflow(wf_factory: dict, target_root: Path,
                    dry_run: bool = False, workflow_only: bool = False,
                    skills_only: bool = False) -> dict:
    """执行工作流更新，返回报告。"""
    report: dict = {
        "target_type": "workflow",
        "workflow_id": wf_factory["workflow_id"],
        "actions": [],
    }
    wf_src = wf_factory["path"]
    wf_dst = target_root / ".claude" / "workflows" / wf_factory["workflow_id"]
    if not dry_run:
        ensure_dir(wf_dst)

    # 1. 工作流定义
    if not skills_only:
        for fname in WORKFLOW_DEF_FILES:
            src_file = wf_src / fname
            dst_file = wf_dst / fname
            if not src_file.exists():
                continue
            if dst_file.exists() and filecmp.cmp(src_file, dst_file, shallow=False):
                report["actions"].append(f"[SKIP] {fname} 无变化")
            else:
                if not dry_run:
                    shutil.copy2(src_file, dst_file)
                action = "[UPDATE]" if dst_file.exists() else "[COPY]"
                report["actions"].append(f"{action} {fname}")

    # 2. 配套 Skills
    if not workflow_only:
        skills_src = wf_src / "skills"
        if skills_src.exists():
            _update_skills_dir(skills_src,
                               target_root / ".claude" / "skills",
                               report, dry_run)

    # 2.5 全局 Skill（从 YAML skill_id 引用中解析，不在 workflow skills/ 目录下的）
    if not workflow_only:
        _update_global_skills_for_workflow(wf_factory, target_root, report, dry_run)

    # 3. 共享资源
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
            changes = _diff_summary(diff)
            if not dry_run:
                if shared_dst.exists():
                    shutil.rmtree(shared_dst)
                copy_tree_with_report(shared_src, shared_dst)
            report["actions"].append(f"[UPDATE] 共享资源 '{shared_dir_name}/' ({changes})")

    return report


def update_infrastructure_skill(skill_factory: dict, target_root: Path,
                                dry_run: bool = False) -> dict:
    """执行基础设施 Skill 更新，返回报告。"""
    report: dict = {
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

    changes = _diff_summary(diff)
    if not dry_run:
        if dst.exists():
            shutil.rmtree(dst)
        copy_tree_with_report(src, dst)
    report["actions"].append(f"[UPDATE] Skill '{skill_factory['skill_id']}' ({changes})")
    return report


def _update_skills_dir(skills_src: Path, global_skills_dst: Path,
                       report: dict, dry_run: bool) -> None:
    """更新 workflow 专属 skills/ 目录下的 Skill，写入全局 .claude/skills/。"""
    for skill_item in sorted(skills_src.iterdir()):
        if not skill_item.is_dir() or skill_item.name in EXCLUDED_NAMES:
            continue
        skill_name = skill_item.name
        target = global_skills_dst / skill_name
        diff = diff_directory(skill_item, target)
        if diff["identical"]:
            report["actions"].append(f"[SKIP] Skill '{skill_name}' 无变化")
            continue
        changes = _diff_summary(diff)
        if not dry_run:
            if target.exists():
                shutil.rmtree(target)
            copy_tree_with_report(skill_item, target)
        report["actions"].append(f"[UPDATE] Skill '{skill_name}' ({changes})")


def _update_global_skills_for_workflow(wf_factory: dict, target_root: Path,
                                        report: dict, dry_run: bool) -> None:
    """更新工作流 YAML 中 skill_id 引用的全局 Skill。

    从 WORKFLOW.yaml 解析 skill_id 引用，过滤掉已在 workflow 本地 skills/ 目录中的，
    只处理 artifacts/skills/ 下的全局 Skill。
    """
    yaml_path = wf_factory["path"] / "WORKFLOW.yaml"
    skill_refs = extract_skill_refs(yaml_path)
    if not skill_refs:
        return

    # workflow 本地 skills/ 中已有的 skill 名称
    local_skill_names: set[str] = set()
    local_skills_dir = wf_factory["path"] / "skills"
    if local_skills_dir.exists():
        local_skill_names = {s.name for s in local_skills_dir.iterdir()
                            if s.is_dir() and s.name not in EXCLUDED_NAMES}

    # 推导 artifacts/skills/ 路径（wf_factory["path"] = .../artifacts/workflows/<dir>/）
    artifacts_dir = wf_factory["path"].parent.parent  # artifacts/
    global_skills_src = artifacts_dir / "skills"

    # 过滤出需作为全局 Skill 处理的引用
    global_skill_refs = [ref for ref in skill_refs if ref not in local_skill_names]

    for skill_name in global_skill_refs:
        skill_src = global_skills_src / skill_name
        if not skill_src.exists() or not (skill_src / "SKILL.md").exists():
            report["actions"].append(
                f"[WARN] 全局 Skill '{skill_name}' 在 artifacts/skills/ 中未找到，跳过")
            continue

        target = target_root / ".claude" / "skills" / skill_name
        diff = diff_directory(skill_src, target)
        if diff["identical"]:
            report["actions"].append(f"[SKIP] 全局 Skill '{skill_name}' 无变化")
            continue

        changes = _diff_summary(diff)
        if not dry_run:
            if target.exists():
                shutil.rmtree(target)
            copy_tree_with_report(skill_src, target)
        report["actions"].append(f"[UPDATE] 全局 Skill '{skill_name}' ({changes})")


def _diff_summary(diff: dict) -> str:
    parts = []
    if diff["new_files"]:
        parts.append(f"新增 {len(diff['new_files'])}")
    if diff["modified_files"]:
        parts.append(f"修改 {len(diff['modified_files'])}")
    if diff["removed_files"]:
        parts.append(f"删除 {len(diff['removed_files'])}")
    return ", ".join(parts)


# ── 检查更新 ────────────────────────────────────────────────

def check_workflow_updates(installed: list, factory: list) -> list[dict]:
    """对比已安装与生产车间，返回可更新的工作流列表。"""
    updates: list[dict] = []
    for inst in installed:
        factory_match = next((f for f in factory if f["workflow_id"] == inst["workflow_id"]), None)
        if not factory_match:
            continue
        # 对比目录内容差异
        diff_wf = diff_directory(factory_match["path"], inst["path"])
        if not diff_wf["identical"]:
            updates.append({
                "type": "workflow",
                "id": inst["workflow_id"],
                "changes": _diff_summary(diff_wf),
            })
    return updates


def check_skill_updates(installed: list, factory: list) -> list[dict]:
    """对比已安装与生产车间，返回可更新的 Skill 列表。"""
    updates: list[dict] = []
    for inst in installed:
        factory_match = next((f for f in factory if f["skill_id"] == inst["skill_id"]), None)
        if not factory_match:
            continue
        diff = diff_directory(factory_match["path"], inst["path"])
        if not diff["identical"]:
            updates.append({
                "type": "infrastructure_skill",
                "id": inst["skill_id"],
                "changes": _diff_summary(diff),
            })
    return updates


# ── 递归更新包装 ────────────────────────────────────────────

def update_workflow_recursive(
    wf_factory: dict,
    factory_wfs: list[dict],
    target_root: Path,
    dry_run: bool = False,
    workflow_only: bool = False,
    skills_only: bool = False,
    no_recursive: bool = False,
) -> list[dict]:
    """递归更新工作流：先子后父，返回所有报告列表。"""
    reports: list[dict] = []

    if not no_recursive:
        children = resolve_child_workflow_chain(wf_factory, factory_wfs)
        for child in children:
            child_report = update_workflow(
                child, target_root, dry_run=dry_run,
                workflow_only=workflow_only, skills_only=skills_only,
            )
            child_report["_is_child"] = True
            reports.append(child_report)

    parent_report = update_workflow(
        wf_factory, target_root, dry_run=dry_run,
        workflow_only=workflow_only, skills_only=skills_only,
    )
    parent_report["_is_child"] = False
    reports.append(parent_report)
    return reports


def check_workflow_updates_recursive(
    installed: list[dict],
    factory: list[dict],
    target_root: Path,
    no_recursive: bool = False,
    factory_skills: list[dict] | None = None,
    installed_skills: list[dict] | None = None,
) -> list[dict]:
    """对比已安装与生产车间，返回可更新的工作流列表（含子工作流）。"""
    updates = check_workflow_updates(installed, factory)

    if no_recursive:
        return updates

    # 对每个已安装且可更新的工作流，检查其子工作流链
    seen_ids = {u["id"] for u in updates}
    for inst in installed:
        factory_match = next((f for f in factory if f["workflow_id"] == inst["workflow_id"]), None)
        if not factory_match:
            continue
        children = resolve_child_workflow_chain(factory_match, factory)
        for child in children:
            child_inst = find_installed_workflow(installed, child["workflow_id"])
            if child_inst:
                # 子工作流已安装，检查是否需要更新
                diff = diff_directory(child["path"], child_inst["path"])
                if not diff["identical"] and child["workflow_id"] not in seen_ids:
                    seen_ids.add(child["workflow_id"])
                    updates.append({
                        "type": "workflow",
                        "id": child["workflow_id"],
                        "changes": _diff_summary(diff),
                        "_is_child": True,
                        "_parent": inst["workflow_id"],
                    })
            else:
                # 子工作流未安装
                if child["workflow_id"] not in seen_ids:
                    seen_ids.add(child["workflow_id"])
                    updates.append({
                        "type": "workflow",
                        "id": child["workflow_id"],
                        "changes": "未安装（将作为子工作流拉取）",
                        "_is_child": True,
                        "_parent": inst["workflow_id"],
                    })

            # 检查子工作流的全局 Skill 依赖
            if factory_skills is not None and installed_skills is not None:
                yaml_path = child["path"] / "WORKFLOW.yaml"
                skill_refs = extract_skill_refs(yaml_path)
                child_local_names: set[str] = set()
                child_skills_dir = child["path"] / "skills"
                if child_skills_dir.exists():
                    child_local_names = {
                        s.name for s in child_skills_dir.iterdir()
                        if s.is_dir() and s.name not in EXCLUDED_NAMES}
                for skill_name in skill_refs:
                    if skill_name in child_local_names:
                        continue
                    factory_skill = next(
                        (s for s in factory_skills if s["skill_id"] == skill_name), None)
                    if not factory_skill:
                        updates.append({
                            "type": "workflow",
                            "id": child["workflow_id"],
                            "changes":
                                f"全局 Skill '{skill_name}' 在 artifacts/skills/ 中未找到",
                            "_is_child": True,
                            "_parent": inst["workflow_id"],
                        })
                        continue
                    installed_skill = find_installed_skill(installed_skills, skill_name)
                    if not installed_skill:
                        updates.append({
                            "type": "workflow",
                            "id": child["workflow_id"],
                            "changes": f"需拉取全局 Skill '{skill_name}'",
                            "_is_child": True,
                            "_parent": inst["workflow_id"],
                        })
                    else:
                        sk_diff = diff_directory(factory_skill["path"],
                                                  installed_skill["path"])
                        if not sk_diff["identical"]:
                            updates.append({
                                "type": "workflow",
                                "id": child["workflow_id"],
                                "changes":
                                    f"全局 Skill '{skill_name}' ({_diff_summary(sk_diff)})",
                                "_is_child": True,
                                "_parent": inst["workflow_id"],
                            })

    return updates


# ── 主函数 ─────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="工作流更新工具 (v3)")
    parser.add_argument("--target", default=os.getcwd(), help="目标目录（默认当前目录）")
    parser.add_argument("--source", default=DEFAULT_SOURCE, help="工作流生产车间根目录")
    parser.add_argument("--query", help="工作流 ID、Skill ID 或关键词")
    parser.add_argument("--check", action="store_true", help="检查所有已安装对象是否有更新")
    parser.add_argument("--dry-run", action="store_true", help="干运行，仅打印不执行")
    parser.add_argument("--workflow-only", action="store_true", help="仅更新工作流定义")
    parser.add_argument("--skills-only", action="store_true", help="仅更新配套 Skill")
    parser.add_argument("--no-recursive", action="store_true", help="不递归拉取/更新子工作流")
    args = parser.parse_args()

    target_root = Path(args.target).resolve()
    source_root = Path(args.source).resolve()

    print(f"[INFO] 目标目录: {target_root}")
    print(f"[INFO] 源目录: {source_root}")
    if args.dry_run:
        print("[INFO] 干运行模式，不执行实际写入")

    installed_wfs = scan_installed_workflows(target_root)
    factory_wfs = scan_factory_workflows(source_root)
    installed_skills = scan_installed_skills(target_root)
    factory_skills = scan_factory_skills(source_root)

    if args.check:
        wf_updates = check_workflow_updates_recursive(
            installed_wfs, factory_wfs, target_root,
            no_recursive=args.no_recursive,
            factory_skills=factory_skills,
            installed_skills=installed_skills,
        )
        sk_updates = check_skill_updates(installed_skills, factory_skills)

        # 检查每个已安装工作流的全局 Skill 依赖
        _seen_global_skill_alerts: set[tuple[str, str]] = set()
        for inst in installed_wfs:
            factory_match = next((f for f in factory_wfs
                                  if f["workflow_id"] == inst["workflow_id"]), None)
            if not factory_match:
                continue
            yaml_path = factory_match["path"] / "WORKFLOW.yaml"
            skill_refs = extract_skill_refs(yaml_path)
            if not skill_refs:
                continue
            # 过滤掉已在 workflow 本地 skills/ 目录中的
            local_names: set[str] = set()
            local_skills_dir = factory_match["path"] / "skills"
            if local_skills_dir.exists():
                local_names = {s.name for s in local_skills_dir.iterdir()
                               if s.is_dir() and s.name not in EXCLUDED_NAMES}
            for skill_name in skill_refs:
                if skill_name in local_names:
                    continue
                key = (inst["workflow_id"], skill_name)
                if key in _seen_global_skill_alerts:
                    continue
                _seen_global_skill_alerts.add(key)
                factory_skill = next(
                    (s for s in factory_skills if s["skill_id"] == skill_name), None)
                if not factory_skill:
                    wf_updates.append({
                        "type": "workflow",
                        "id": inst["workflow_id"],
                        "changes":
                            f"全局 Skill '{skill_name}' 在 artifacts/skills/ 中未找到",
                    })
                    continue
                installed_skill = find_installed_skill(installed_skills, skill_name)
                if not installed_skill:
                    wf_updates.append({
                        "type": "workflow",
                        "id": inst["workflow_id"],
                        "changes": f"需拉取全局 Skill '{skill_name}'",
                    })
                else:
                    diff = diff_directory(factory_skill["path"],
                                          installed_skill["path"])
                    if not diff["identical"]:
                        wf_updates.append({
                            "type": "workflow",
                            "id": inst["workflow_id"],
                            "changes":
                                f"全局 Skill '{skill_name}' ({_diff_summary(diff)})",
                        })

        if not wf_updates and not sk_updates:
            print("[OK] 所有已安装工作流和 Skill 均为最新")
            sys.exit(0)

        if wf_updates:
            print(f"\n[WORKFLOW UPDATES] 发现 {len(wf_updates)} 个工作流可更新:")
            for u in wf_updates:
                tag = " [子工作流]" if u.get("_is_child") else ""
                parent = f" (父: {u.get('_parent')})" if u.get("_parent") else ""
                print(f"  - {u['id']}{tag}{parent}: {u['changes']}")

        if sk_updates:
            print(f"\n[SKILL UPDATES] 发现 {len(sk_updates)} 个 Skill 可更新:")
            for u in sk_updates:
                print(f"  - {u['id']}: {u['changes']}")
        sys.exit(0)

    if not args.query:
        print("[ERROR] 请指定 --query <id> 或 --check")
        sys.exit(1)

    query = args.query.lower().strip()
    matched_skill = find_factory_skill(factory_skills, query)
    matched_workflow = find_factory_workflow(factory_wfs, query)

    if matched_skill and matched_workflow:
        print(f"[AMBIGUOUS] '{args.query}' 同时匹配到 Skill 和工作流，请精确指定")
        sys.exit(1)

    if matched_skill:
        report = update_infrastructure_skill(matched_skill, target_root, dry_run=args.dry_run)
        print(f"\n[REPORT] 更新结果: {report['skill_id']} (基础设施 Skill)")
        for action in report["actions"]:
            print(f"  {action}")

    elif matched_workflow:
        # 先解析子工作流链（干运行时用于展示）
        children = [] if args.no_recursive else resolve_child_workflow_chain(matched_workflow, factory_wfs)
        if children and args.dry_run:
            print(f"\n[CHILD WORKFLOWS] 检测到 {len(children)} 个子工作流:")
            for child in children:
                print(f"  - {child['workflow_id']} ({child['dir_name']})")

        reports = update_workflow_recursive(
            matched_workflow, factory_wfs, target_root,
            dry_run=args.dry_run,
            workflow_only=args.workflow_only,
            skills_only=args.skills_only,
            no_recursive=args.no_recursive,
        )
        for report in reports:
            tag = " [子工作流]" if report.get("_is_child") else ""
            print(f"\n[REPORT] 更新结果: {report['workflow_id']} ({report.get('target_type', 'workflow')}){tag}")
            for action in report["actions"]:
                print(f"  {action}")

    else:
        print(f"[ERROR] 未找到匹配 '{args.query}' 的对象")
        print("[INFO] 可用工作流:")
        for wf in factory_wfs:
            print(f"  - {wf['workflow_id']}")
        print("[INFO] 可用基础设施 Skill:")
        for sk in factory_skills:
            print(f"  - {sk['skill_id']}")
        sys.exit(1)

    print("[DONE] 更新完成")


if __name__ == "__main__":
    main()
