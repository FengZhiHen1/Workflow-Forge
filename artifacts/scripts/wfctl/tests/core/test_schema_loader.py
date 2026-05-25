"""测试 schema loader。"""

import pytest

from infrastructure.errors import SchemaError
from compat.workflow.registry import _get_adapter, load_workflow
from compat.workflow.v3 import V3WorkflowAdapter


SAMPLE_YAML = """
schema_version: "3.0.0"
workflow_id: "test-flow"
version: "1.0.0"
max_parallel_agents: 2
anchor_prefix: "wf"
stages:
  - stage_id: s00-workflow-start
    name: "开始"
  - stage_id: s01
    name: "分析"
    skill_id: analyst
    mandatory: true
edges:
  - from: s00-workflow-start
    to: s01
    condition: always
"""


def test_load_workflow_success(tmp_path):
    yaml_path = tmp_path / "WORKFLOW.yaml"
    yaml_path.write_text(SAMPLE_YAML, encoding="utf-8")
    spec = load_workflow(yaml_path)
    assert spec.workflow_id == "test-flow"
    assert spec.schema_version == "3.0.0"
    assert len(spec.stages) == 2


def test_load_workflow_file_not_found(tmp_path):
    with pytest.raises(SchemaError) as exc_info:
        load_workflow(tmp_path / "nonexistent.yaml")
    assert exc_info.value.code == "WORKFLOW_NOT_FOUND"


def test_load_workflow_invalid_yaml(tmp_path):
    yaml_path = tmp_path / "WORKFLOW.yaml"
    yaml_path.write_text("{invalid", encoding="utf-8")
    with pytest.raises(SchemaError) as exc_info:
        load_workflow(yaml_path)
    assert exc_info.value.code == "SCHEMA_PARSE_ERROR"


def test_load_workflow_missing_schema_version(tmp_path):
    yaml_path = tmp_path / "WORKFLOW.yaml"
    yaml_path.write_text("workflow_id: x\n", encoding="utf-8")
    with pytest.raises(SchemaError) as exc_info:
        load_workflow(yaml_path)
    assert exc_info.value.code == "SCHEMA_VALIDATION_ERROR"


def test_unsupported_schema_version(tmp_path):
    yaml_path = tmp_path / "WORKFLOW.yaml"
    yaml_path.write_text('schema_version: "99.0.0"\nworkflow_id: x\n', encoding="utf-8")
    with pytest.raises(SchemaError) as exc_info:
        load_workflow(yaml_path)
    assert exc_info.value.code == "SCHEMA_VERSION_UNSUPPORTED"


def test_get_adapter_v3():
    adapter = _get_adapter("3.0.0")
    assert isinstance(adapter, V3WorkflowAdapter)


def test_get_adapter_unknown():
    with pytest.raises(SchemaError) as exc_info:
        _get_adapter("2.0.0")
    assert exc_info.value.code == "SCHEMA_VERSION_UNSUPPORTED"
