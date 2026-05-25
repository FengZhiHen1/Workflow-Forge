"""wfctl visualize 命令：生成 Mermaid 流程图。"""

from __future__ import annotations

import argparse

from domain.dag.graph import build_adjacency
from infrastructure.errors import InputError
from domain.workflow.spec import EdgeCondition, StageTargetType
from compat.workflow.registry import load_workflow
from services.resolver import find_workflow_dir


def register_visualize(subparsers):
    p = subparsers.add_parser("visualize", help="生成工作流 Mermaid 流程图")
    p.add_argument("--workflow", required=True, help="工作流 ID")
    p.add_argument("--version", default=None, help="版本号")
    p.set_defaults(handler=_handle_visualize)


def _handle_visualize(args) -> dict:
    wf_dir = find_workflow_dir(args.workflow, args.version)
    yaml_file = wf_dir / "WORKFLOW.yaml"
    if not yaml_file.exists():
        raise InputError(f"WORKFLOW.yaml not found: {yaml_file}", code="WORKFLOW_NOT_FOUND")

    spec = load_workflow(yaml_file)
    adj = build_adjacency(spec)
    mermaid = _generate_mermaid(spec, adj)
    return {"status": "ok", "mermaid": mermaid}


def _generate_mermaid(spec, adj) -> str:
    """基于 WorkflowSpec 和 AdjacencyList 生成 Mermaid 语法。"""
    lines = ["graph TD"]

    # 定义节点样式
    for stage in spec.stages:
        sid = stage.stage_id
        label = stage.name or sid
        shape = _get_shape(stage)
        lines.append(f"    {sid}{shape[0]}{label}{shape[1]}")

    # 定义边
    for edge in spec.edges:
        style = _get_edge_style(edge)
        label = edge.condition.value
        if edge.choice:
            label += f"({edge.choice})"
        lines.append(f"    {edge.from_stage} {style}> {edge.to_stage}" + (f" :|{label}|" if label else ""))

    # 样式类
    lines.append("    classDef virtual fill:#f0f0f0,stroke:#999,stroke-dasharray: 5 5")
    lines.append("    classDef skill fill:#e1f5fe,stroke:#0288d1")
    lines.append("    classDef workflow fill:#fff3e0,stroke:#f57c00")
    lines.append("    classDef confirm fill:#fce4ec,stroke:#c2185b")

    for stage in spec.stages:
        cls = _get_class(stage)
        if cls:
            lines.append(f"    class {stage.stage_id} {cls}")

    return "\n".join(lines)


def _get_shape(stage):
    if stage.target_type == StageTargetType.VIRTUAL:
        return ("((", "))")
    if stage.target_type == StageTargetType.WORKFLOW:
        return ("[[", "]]")
    return ("[", "]")


def _get_edge_style(edge):
    if edge.condition == EdgeCondition.ALWAYS:
        return "--"
    if edge.condition == EdgeCondition.FAILURE:
        return "-."
    if edge.condition == EdgeCondition.LOOP_EXCEEDED:
        return "-."
    return "--"


def _get_class(stage):
    if stage.target_type == StageTargetType.VIRTUAL:
        return "virtual"
    if stage.target_type == StageTargetType.WORKFLOW:
        return "workflow"
    return "skill"
