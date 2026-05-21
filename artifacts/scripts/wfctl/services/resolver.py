"""工作流发现与解析。"""

from pathlib import Path

from core.errors import InputError
from core.project import find_root
from core.schema.interface import WorkflowSpec
from core.schema.loader import load_workflow


def find_workflow_dir(workflow_id: str, version: str | None = None) -> Path:
    """查找工作流目录。目录命名规范：<workflow_id>@<version>/。

    无 version 时匹配该 workflow_id 的最新版本目录（按名称排序取最后）。
    """
    root = find_root()
    workflows_dir = root / ".claude" / "workflows"
    if not workflows_dir.exists():
        raise InputError(f"Workflows directory not found", code="WORKFLOW_NOT_FOUND")

    if version:
        expected = f"{workflow_id}@{version}"
        wf_dir = workflows_dir / expected
        if wf_dir.exists() and wf_dir.is_dir():
            return wf_dir
        # 回退：尝试无版本后缀的目录（兼容旧格式）
        legacy = workflows_dir / workflow_id
        if legacy.exists() and legacy.is_dir():
            return legacy
        raise InputError(f"Workflow not found: {expected}", code="WORKFLOW_NOT_FOUND")

    # 无版本：匹配所有该 workflow_id 开头的目录，取最后一个（最新版本）
    candidates = sorted(
        [d for d in workflows_dir.iterdir() if d.is_dir() and d.name.startswith(f"{workflow_id}@")],
        key=lambda d: d.name,
    )
    if candidates:
        return candidates[-1]

    # 兼容无 @version 后缀的目录
    legacy = workflows_dir / workflow_id
    if legacy.exists() and legacy.is_dir():
        return legacy

    raise InputError(f"Workflow not found: {workflow_id}", code="WORKFLOW_NOT_FOUND")


def resolve() -> list[dict]:
    """无参数调用：扫描可用工作流清单。"""
    root = find_root()
    workflows_dir = root / ".claude" / "workflows"
    if not workflows_dir.exists():
        return []

    results: list[dict] = []
    for wf_dir in sorted(workflows_dir.iterdir()):
        if not wf_dir.is_dir():
            continue
        dir_name = wf_dir.name
        md_file = wf_dir / "WORKFLOW.md"
        yaml_file = wf_dir / "WORKFLOW.yaml"
        if not yaml_file.exists():
            continue

        # 从目录名解析 workflow_id 和 version
        if "@" in dir_name:
            wf_id, wf_ver = dir_name.rsplit("@", 1)
        else:
            wf_id, wf_ver = dir_name, ""

        info = {"workflow_id": wf_id, "version": wf_ver, "description": "", "tags": []}
        if md_file.exists():
            frontmatter = _parse_md_frontmatter(md_file.read_text(encoding="utf-8"))
            info["description"] = frontmatter.get("description", "")
            info["tags"] = frontmatter.get("tags", [])

        # 从 YAML 读取版本（优先于目录名推断）
        try:
            spec = load_workflow(yaml_file)
            info["version"] = spec.version
        except Exception:
            pass

        results.append(info)

    return results


def resolve_workflow(workflow_id: str, version: str | None = None) -> dict:
    """解析单个 WORKFLOW.yaml 的完整结构。"""
    wf_dir = find_workflow_dir(workflow_id, version)

    yaml_file = wf_dir / "WORKFLOW.yaml"
    if not yaml_file.exists():
        raise InputError(f"WORKFLOW.yaml not found for: {workflow_id}", code="WORKFLOW_NOT_FOUND")

    spec = load_workflow(yaml_file)

    return {
        "workflow_id": spec.workflow_id,
        "version": spec.version,
        "schema_version": spec.schema_version,
        "max_parallel_agents": spec.max_parallel_agents,
        "anchor_prefix": spec.anchor_prefix,
        "stages": [
            {
                "stage_id": s.stage_id,
                "name": s.name,
                "target_type": s.target_type.value,
                "target": s.target,
                "mandatory": s.mandatory,
                "confirmation_point": s.confirmation_point,
                "retry": s.retry,
                "timeout_seconds": s.timeout_seconds,
                "model": s.model,
                "exclusive": s.exclusive,
                "parallel": {
                    "source": s.parallel.source,
                    "max_instances": s.parallel.max_instances,
                } if s.parallel else None,
            }
            for s in spec.stages
        ],
        "edges": [
            {
                "from": e.from_stage,
                "to": e.to_stage,
                "condition": e.condition.value,
                "max_loop": e.max_loop,
                "loop_counter_stage": e.loop_counter_stage,
                "choice": e.choice,
                "aggregation": e.aggregation,
            }
            for e in spec.edges
        ],
    }


def _parse_md_frontmatter(text: str) -> dict:
    """简易解析 Markdown YAML frontmatter。"""
    if not text.startswith("---"):
        return {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    try:
        import yaml
        return yaml.safe_load(parts[1]) or {}
    except Exception:
        return {}
