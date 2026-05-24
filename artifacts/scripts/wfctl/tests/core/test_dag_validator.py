"""测试 DAG 静态分析器。"""

import pytest

from core.dag_validator import validate_workflow, ValidationIssue
from core.schema.interface import (
    EdgeCondition,
    EdgeSpec,
    ParallelSpec,
    StageSpec,
    StageTargetType,
    WorkflowSpec,
)


def _make_spec(stages: list[StageSpec], edges: list[EdgeSpec]) -> WorkflowSpec:
    return WorkflowSpec(
        schema_version="3.0.0",
        workflow_id="test",
        version="1.0.0",
        max_parallel_agents=4,
        anchor_prefix="wf",
        stages=stages,
        edges=edges,
    )


class TestValidateWorkflow:
    def test_valid_workflow(self):
        spec = _make_spec(
            stages=[
                StageSpec(stage_id="s00-start", name="start", target_type=StageTargetType.VIRTUAL),
                StageSpec(stage_id="s01", name="a", target_type=StageTargetType.SKILL, target="skill-a"),
                StageSpec(stage_id="s99-end", name="end", target_type=StageTargetType.VIRTUAL),
            ],
            edges=[
                EdgeSpec(from_stage="s00-start", to_stage="s01", condition=EdgeCondition.ALWAYS),
                EdgeSpec(from_stage="s01", to_stage="s99-end", condition=EdgeCondition.SUCCESS),
            ],
        )
        result = validate_workflow(spec)
        assert not result.has_errors

    def test_unreachable_stage(self):
        spec = _make_spec(
            stages=[
                StageSpec(stage_id="s00-start", name="start", target_type=StageTargetType.VIRTUAL),
                StageSpec(stage_id="s01", name="a", target_type=StageTargetType.SKILL, target="skill-a"),
                StageSpec(stage_id="s02", name="b", target_type=StageTargetType.SKILL, target="skill-b"),
            ],
            edges=[
                EdgeSpec(from_stage="s00-start", to_stage="s01", condition=EdgeCondition.ALWAYS),
            ],
        )
        result = validate_workflow(spec)
        assert result.has_errors
        assert any(i.category == "UNREACHABLE_STAGE" and i.stage_id == "s02" for i in result.issues)

    def test_dangling_edge(self):
        spec = _make_spec(
            stages=[
                StageSpec(stage_id="s01", name="a", target_type=StageTargetType.SKILL, target="skill-a"),
            ],
            edges=[
                EdgeSpec(from_stage="s01", to_stage="s99", condition=EdgeCondition.SUCCESS),
            ],
        )
        result = validate_workflow(spec)
        assert result.has_errors
        assert any(i.category == "DANGLING_EDGE" for i in result.issues)

    def test_confirmation_gap(self):
        spec = _make_spec(
            stages=[
                StageSpec(stage_id="s01", name="a", target_type=StageTargetType.SKILL, target="skill-a", confirmation_point=True),
            ],
            edges=[
                EdgeSpec(from_stage="s01", to_stage="s02", condition=EdgeCondition.SUCCESS),
            ],
        )
        result = validate_workflow(spec)
        assert result.has_errors
        assert any(i.category == "CONFIRMATION_GAP" for i in result.issues)

    def test_unbounded_loop(self):
        spec = _make_spec(
            stages=[
                StageSpec(stage_id="s01", name="a", target_type=StageTargetType.SKILL, target="skill-a"),
            ],
            edges=[
                EdgeSpec(from_stage="s01", to_stage="s01", condition=EdgeCondition.FAILURE),
            ],
        )
        result = validate_workflow(spec)
        assert result.has_errors
        assert any(i.category == "UNBOUNDED_LOOP" for i in result.issues)

    def test_parallel_source_missing(self):
        spec = _make_spec(
            stages=[
                StageSpec(stage_id="s01", name="a", target_type=StageTargetType.SKILL, target="skill-a"),
                StageSpec(stage_id="s02", name="b", target_type=StageTargetType.SKILL, target="skill-b",
                          parallel=ParallelSpec(source="s99")),
            ],
            edges=[
                EdgeSpec(from_stage="s01", to_stage="s02", condition=EdgeCondition.SUCCESS),
            ],
        )
        result = validate_workflow(spec)
        assert result.has_errors
        assert any(i.category == "PARALLEL_SOURCE_MISSING" for i in result.issues)
