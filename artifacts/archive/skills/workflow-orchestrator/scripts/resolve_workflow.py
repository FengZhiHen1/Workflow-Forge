#!/usr/bin/env python3
"""
Workflow Resolver (v2)

扫描 .claude/workflows/ 目录，解析所有工作流 Reference 目录的元数据。
每个工作流是一个目录：.claude/workflows/<workflow_id>@<version>/
目录内包含：
  - WORKFLOW.md   : 人类可读（名称、概览、Mermaid 流程图）
  - WORKFLOW.yaml : 机器规范（stages、edges、并发规则）

支持精确匹配（workflow_id@version）和模糊匹配（关键词）。

用法:
    python resolve_workflow.py --query "refactor"
    python resolve_workflow.py --query "refactor-pipeline@v1.2.0"
    python resolve_workflow.py --list-all
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path


def find_workflows_dir() -> Path:
    """定位 .claude/workflows/ 目录，支持从项目根目录开始搜索。"""
    cwd = Path.cwd()
    candidate = cwd / ".claude" / "workflows"
    if candidate.exists() and candidate.is_dir():
        return candidate

    for parent in [cwd.parent, cwd.parent.parent]:
        candidate = parent / ".claude" / "workflows"
        if candidate.exists() and candidate.is_dir():
            return candidate

    # 回退到当前目录下的 workflows/（用于测试/开发环境）
    candidate = cwd / "workflows"
    if candidate.exists() and candidate.is_dir():
        return candidate

    return None


def parse_yaml_content(yaml_text: str) -> dict:
    """
    解析 YAML 文本。优先使用 PyYAML，不可用时回退到手写解析器。
    """
    # 优先使用 PyYAML（能正确处理嵌套对象、复杂列表等）
    try:
        import yaml
        data = yaml.safe_load(yaml_text)
        return data if isinstance(data, dict) else {}
    except Exception:
        pass

    # 回退：手写解析器（仅支持列表+简单键值，不支持嵌套对象）
    result = {}
    lines = yaml_text.split("\n")
    i = 0
    current_list = None
    current_list_key = None
    current_obj = None

    while i < len(lines):
        line = lines[i]
        stripped = line.rstrip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue

        indent = len(line) - len(line.lstrip())

        # 顶层键
        if indent == 0 and ":" in stripped:
            key = stripped.split(":", 1)[0].strip()
            rest = stripped.split(":", 1)[1].strip()
            current_list = None
            current_obj = None

            if rest == "":
                result[key] = []
                current_list_key = key
                current_list = result[key]
            else:
                val = rest.strip('"\'')
                if val.lower() == "true":
                    result[key] = True
                elif val.lower() == "false":
                    result[key] = False
                elif val.isdigit():
                    result[key] = int(val)
                else:
                    result[key] = val

        # 列表项
        elif stripped.startswith("-") and current_list is not None:
            item_text = stripped[1:].strip()
            if ":" in item_text:
                obj = {}
                key, val = item_text.split(":", 1)
                v = val.strip().strip('"\'')
                if v.lower() == "true":
                    obj[key.strip()] = True
                elif v.lower() == "false":
                    obj[key.strip()] = False
                elif v.isdigit():
                    obj[key.strip()] = int(v)
                else:
                    obj[key.strip()] = v

                # 读取后续缩进行
                j = i + 1
                while j < len(lines):
                    next_line = lines[j]
                    next_stripped = next_line.rstrip()
                    if not next_stripped or next_stripped.startswith("#"):
                        j += 1
                        continue
                    next_indent = len(next_line) - len(next_line.lstrip())
                    if next_indent <= 2 and next_stripped and not next_stripped.startswith("-"):
                        break
                    if ":" in next_stripped and not next_stripped.startswith("-"):
                        k, v = next_stripped.split(":", 1)
                        vv = v.strip().strip('"\'')
                        if vv.lower() == "true":
                            obj[k.strip()] = True
                        elif vv.lower() == "false":
                            obj[k.strip()] = False
                        elif vv.isdigit():
                            obj[k.strip()] = int(vv)
                        else:
                            obj[k.strip()] = vv
                    elif ":" in next_stripped and next_stripped.startswith("-"):
                        break
                    j += 1
                current_list.append(obj)
                current_obj = obj
                i = j - 1
            else:
                val = item_text.strip('"\'')
                if val.lower() == "true":
                    current_list.append(True)
                elif val.lower() == "false":
                    current_list.append(False)
                elif val.isdigit():
                    current_list.append(int(val))
                else:
                    current_list.append(val)

        # 子对象属性（缩进，属于当前列表最后一个对象）
        elif indent >= 2 and current_obj is not None and ":" in stripped:
            k, v = stripped.split(":", 1)
            vv = v.strip().strip('"\'')
            if vv.lower() == "true":
                current_obj[k.strip()] = True
            elif vv.lower() == "false":
                current_obj[k.strip()] = False
            elif vv.isdigit():
                current_obj[k.strip()] = int(vv)
            else:
                current_obj[k.strip()] = vv

        i += 1

    return result


def parse_workflow_dir(wf_dir: Path) -> dict:
    """
    解析单个工作流目录，提取元数据。
    """
    md_path = wf_dir / "WORKFLOW.md"
    yaml_path = wf_dir / "WORKFLOW.yaml"

    # 从目录名提取 workflow_id 和 version
    # 格式: <workflow_id>@<version>
    dir_name = wf_dir.name
    if "@" in dir_name:
        file_workflow_id, file_version = dir_name.rsplit("@", 1)
    else:
        file_workflow_id = dir_name
        file_version = "unknown"

    meta = {
        "workflow_id": file_workflow_id,
        "version": file_version,
        "dir_name": dir_name,
        "path": str(wf_dir).replace("\\", "/"),
        "has_md": md_path.exists(),
        "has_yaml": yaml_path.exists(),
    }

    # 解析 WORKFLOW.md：提取标题和概览
    if md_path.exists():
        md_content = md_path.read_text(encoding="utf-8")
        title_match = re.search(r"^#\s+(.+)$", md_content, re.MULTILINE)
        meta["title"] = title_match.group(1).strip() if title_match else file_workflow_id

        goal_match = re.search(r"[\-\*]\s*目标[：:]\s*(.+)$", md_content, re.MULTILINE)
        if goal_match:
            meta["goal"] = goal_match.group(1).strip()

    # 解析 WORKFLOW.yaml：提取机器规范
    if yaml_path.exists():
        yaml_content = yaml_path.read_text(encoding="utf-8")
        yaml_data = parse_yaml_content(yaml_content)

        meta["workflow_id"] = yaml_data.get("workflow_id", file_workflow_id)
        meta["version"] = yaml_data.get("version", file_version)
        meta["description"] = yaml_data.get("description", "")

        stages = yaml_data.get("stages", [])
        meta["stage_count"] = len(stages)
        meta["stages"] = [s.get("stage_id", "unknown") for s in stages if isinstance(s, dict)]

        # 提取并发上限
        concurrency = yaml_data.get("concurrency_rules", {})
        meta["max_parallel_agents"] = concurrency.get("max_parallel_agents", 1)

    return meta


def scan_all_workflows(workflows_dir: Path) -> list:
    """扫描目录下所有子目录（每个子目录即一个工作流）。"""
    if not workflows_dir or not workflows_dir.exists():
        return []

    results = []
    for entry in sorted(workflows_dir.iterdir()):
        if entry.is_dir() and "@" in entry.name:
            try:
                meta = parse_workflow_dir(entry)
                results.append(meta)
            except Exception as e:
                results.append({
                    "dir_name": entry.name,
                    "error": str(e),
                    "path": str(entry).replace("\\", "/"),
                })
    return results


def compute_match_score(query: str, meta: dict) -> float:
    """
    计算查询字符串与工作流元数据的匹配分数。
    返回 0-1 之间的分数，越高越匹配。
    """
    query_lower = query.lower()
    query_parts = query_lower.split()

    score = 0.0
    text_to_search = " ".join([
        meta.get("workflow_id", ""),
        meta.get("title", ""),
        meta.get("description", ""),
        meta.get("goal", ""),
    ]).lower()

    # 精确匹配 workflow_id 或版本 -> 最高分
    full_id = f"{meta.get('workflow_id', '')}@{meta.get('version', '')}"
    if query_lower == full_id.lower() or query_lower == meta.get("workflow_id", "").lower():
        return 1.0

    # 部分匹配 workflow_id
    if query_lower in meta.get("workflow_id", "").lower():
        score += 0.8

    # 标题匹配
    if query_lower in meta.get("title", "").lower():
        score += 0.6

    # 描述/目标匹配
    if query_lower in text_to_search:
        score += 0.4

    # 关键词分词匹配
    part_scores = []
    for part in query_parts:
        if len(part) < 2:
            continue
        if part in meta.get("workflow_id", "").lower():
            part_scores.append(0.5)
        elif part in text_to_search:
            part_scores.append(0.2)
    if part_scores:
        score += sum(part_scores) / len(query_parts) * 0.3

    return min(score, 1.0)


def resolve_workflow(query: str, workflows_dir: Path) -> dict:
    """
    主解析函数。
    返回格式:
    {
        "exact_match": {...} or null,
        "candidates": [{...}, ...]  # 按匹配分数降序
    }
    """
    all_workflows = scan_all_workflows(workflows_dir)

    if not all_workflows:
        return {"exact_match": None, "candidates": [], "error": "No workflow directories found"}

    # 先尝试精确匹配（包含版本号或不包含）
    for wf in all_workflows:
        if "error" in wf:
            continue
        full_id = f"{wf.get('workflow_id', '')}@{wf.get('version', '')}"
        if query.lower() == full_id.lower() or query.lower() == wf.get("workflow_id", "").lower():
            return {"exact_match": wf, "candidates": [wf]}

    # 模糊匹配
    scored = []
    for wf in all_workflows:
        if "error" in wf:
            continue
        score = compute_match_score(query, wf)
        if score > 0:
            scored.append({**wf, "match_score": round(score, 3)})

    scored.sort(key=lambda x: x["match_score"], reverse=True)

    return {
        "exact_match": None,
        "candidates": scored,
        "total_scanned": len(all_workflows),
    }


def main():
    parser = argparse.ArgumentParser(description="Resolve workflow reference directories (v2)")
    parser.add_argument("--query", type=str, default="", help="Search query (workflow_id or keywords)")
    parser.add_argument("--list-all", action="store_true", help="List all workflows without filtering")
    parser.add_argument("--workflows-dir", type=str, default="", help="Override .claude/workflows/ path")
    args = parser.parse_args()

    if args.workflows_dir:
        workflows_dir = Path(args.workflows_dir)
    else:
        workflows_dir = find_workflows_dir()

    if not workflows_dir or not workflows_dir.exists():
        print(json.dumps({
            "error": f"Workflows directory not found: {workflows_dir}",
            "exact_match": None,
            "candidates": [],
        }, ensure_ascii=False, indent=2))
        sys.exit(1)

    if args.list_all:
        all_wf = scan_all_workflows(workflows_dir)
        result = {
            "exact_match": None,
            "candidates": [{k: v for k, v in wf.items() if k != "error"} for wf in all_wf if "error" not in wf],
            "errors": [wf for wf in all_wf if "error" in wf],
            "total_scanned": len(all_wf),
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if not args.query:
        print(json.dumps({
            "error": "No query provided. Use --query or --list-all",
            "exact_match": None,
            "candidates": [],
        }, ensure_ascii=False, indent=2))
        sys.exit(1)

    result = resolve_workflow(args.query, workflows_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
