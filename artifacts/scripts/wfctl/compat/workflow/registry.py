"""WORKFLOW.yaml 适配器注册表。

按 schema_version 选择适配器，返回标准 WorkflowSpec。
后续版本只需在此注册新适配器。
"""

from pathlib import Path

import yaml

from infrastructure.errors import SchemaError
from domain.workflow.spec import WorkflowSpec
from compat.protocol import WorkflowAdapter
from compat.workflow.v3 import V3WorkflowAdapter


_ADAPTERS: dict[str, WorkflowAdapter] = {
    "3.0.0": V3WorkflowAdapter(),
}


def load_workflow(yaml_path: Path) -> WorkflowSpec:
    """读取 WORKFLOW.yaml，按 schema_version 选择适配器，返回 WorkflowSpec。"""
    if not yaml_path.exists():
        raise SchemaError(f"Workflow file not found: {yaml_path}", code="WORKFLOW_NOT_FOUND")

    try:
        raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    except Exception as e:
        raise SchemaError(f"Failed to parse YAML: {e}", code="SCHEMA_PARSE_ERROR")

    if not isinstance(raw, dict):
        raise SchemaError("YAML root must be a mapping", code="SCHEMA_PARSE_ERROR")

    version = str(raw.get("schema_version", ""))
    if not version:
        raise SchemaError("Missing schema_version", code="SCHEMA_VALIDATION_ERROR")

    adapter = _get_adapter(version)
    return adapter.parse(raw)


def _get_adapter(version: str) -> WorkflowAdapter:
    adapter = _ADAPTERS.get(version)
    if adapter is None:
        raise SchemaError(f"Unsupported schema_version: {version}", code="SCHEMA_VERSION_UNSUPPORTED")
    return adapter
