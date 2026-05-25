"""测试 schema v3 适配器。"""

import pytest

from infrastructure.errors import SchemaError
from domain.workflow.spec import EdgeCondition, StageTargetType
from compat.workflow.v3 import V3WorkflowAdapter


SAMPLE_YAML_DICT = {
    "schema_version": "3.0.0",
    "workflow_id": "test-flow",
    "version": "1.0.0",
    "max_parallel_agents": 4,
    "anchor_prefix": "wf",
    "stages": [
        {"stage_id": "s00-workflow-start", "name": "开始"},
        {
            "stage_id": "s01",
            "name": "分析",
            "skill_id": "analyst",
            "mandatory": True,
            "retry": 2,
        },
        {
            "stage_id": "s02",
            "name": "设计",
            "workflow": "design@1.0.0",
            "mandatory": True,
            "parallel": {"source": "s01", "max_instances": 5},
            "exclusive": True,
        },
        {"stage_id": "s99-workflow-end", "name": "结束"},
    ],
    "edges": [
        {"from": "s00-workflow-start", "to": "s01", "condition": "always"},
        {"from": "s01", "to": "s02", "condition": "success", "choice": "通过"},
        {"from": "s01", "to": "s01", "condition": "success", "max_loop": 3},
        {"from": "s02", "to": "s99-workflow-end", "condition": "success"},
    ],
}


def test_parse_basic():
    adapter = V3WorkflowAdapter()
    spec = adapter.parse(SAMPLE_YAML_DICT)
    assert spec.workflow_id == "test-flow"
    assert spec.version == "1.0.0"
    assert spec.max_parallel_agents == 4
    assert len(spec.stages) == 4
    assert len(spec.edges) == 4


def test_virtual_stage():
    adapter = V3WorkflowAdapter()
    spec = adapter.parse(SAMPLE_YAML_DICT)
    start = spec.stages[0]
    assert start.target_type == StageTargetType.VIRTUAL
    assert start.mandatory is False


def test_skill_stage():
    adapter = V3WorkflowAdapter()
    spec = adapter.parse(SAMPLE_YAML_DICT)
    s01 = spec.stages[1]
    assert s01.target_type == StageTargetType.SKILL
    assert s01.target == "analyst"
    assert s01.retry == 2


def test_workflow_stage():
    adapter = V3WorkflowAdapter()
    spec = adapter.parse(SAMPLE_YAML_DICT)
    s02 = spec.stages[2]
    assert s02.target_type == StageTargetType.WORKFLOW
    assert s02.target == "design@1.0.0"
    assert s02.exclusive is True
    assert s02.parallel is not None
    assert s02.parallel.source == "s01"
    assert s02.parallel.max_instances == 5


def test_missing_required():
    adapter = V3WorkflowAdapter()
    bad = {"schema_version": "3.0.0", "workflow_id": "x"}
    with pytest.raises(SchemaError):
        adapter.parse(bad)


def test_stage_without_target():
    adapter = V3WorkflowAdapter()
    bad = {
        "schema_version": "3.0.0",
        "workflow_id": "x",
        "version": "1.0.0",
        "max_parallel_agents": 1,
        "stages": [{"stage_id": "s01", "name": "x", "mandatory": True, }],
        "edges": [],
    }
    with pytest.raises(SchemaError):
        adapter.parse(bad)
