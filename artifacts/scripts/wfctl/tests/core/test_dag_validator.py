"""测试 DAG 静态分析器。"""

import pytest

from domain.dag.validator import validate_workflow, ValidationIssue
from domain.workflow.spec import (
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

    def test_multi_node_cycle_allowed(self):
        """多节点环本身被允许，不报错。"""
        spec = _make_spec(
            stages=[
                StageSpec(stage_id="s01", name="a", target_type=StageTargetType.SKILL, target="skill-a"),
                StageSpec(stage_id="s02", name="b", target_type=StageTargetType.SKILL, target="skill-b"),
                StageSpec(stage_id="s03", name="c", target_type=StageTargetType.SKILL, target="skill-c"),
            ],
            edges=[
                EdgeSpec(from_stage="s01", to_stage="s02", condition=EdgeCondition.SUCCESS),
                EdgeSpec(from_stage="s02", to_stage="s03", condition=EdgeCondition.SUCCESS),
                EdgeSpec(from_stage="s03", to_stage="s01", condition=EdgeCondition.SUCCESS),
            ],
        )
        result = validate_workflow(spec)
        assert not any(i.category == "MULTI_NODE_CYCLE_MAX_LOOP" for i in result.issues)

    def test_multi_node_cycle_max_loop_forbidden(self):
        """多节点环中的边设置 max_loop 应报错（max_loop 仅限于自环边）。"""
        spec = _make_spec(
            stages=[
                StageSpec(stage_id="s01", name="a", target_type=StageTargetType.SKILL, target="skill-a"),
                StageSpec(stage_id="s02", name="b", target_type=StageTargetType.SKILL, target="skill-b"),
                StageSpec(stage_id="s03", name="c", target_type=StageTargetType.SKILL, target="skill-c"),
            ],
            edges=[
                EdgeSpec(from_stage="s01", to_stage="s02", condition=EdgeCondition.SUCCESS),
                EdgeSpec(from_stage="s02", to_stage="s03", condition=EdgeCondition.SUCCESS),
                EdgeSpec(from_stage="s03", to_stage="s01", condition=EdgeCondition.SUCCESS, max_loop=3),
            ],
        )
        result = validate_workflow(spec)
        assert any(i.category == "MULTI_NODE_CYCLE_MAX_LOOP" for i in result.issues)

    def test_choice_duplicate(self):
        spec = _make_spec(
            stages=[
                StageSpec(stage_id="s01", name="a", target_type=StageTargetType.SKILL, target="skill-a"),
                StageSpec(stage_id="s02", name="b", target_type=StageTargetType.SKILL, target="skill-b"),
                StageSpec(stage_id="s03", name="c", target_type=StageTargetType.SKILL, target="skill-c"),
            ],
            edges=[
                EdgeSpec(from_stage="s01", to_stage="s02", condition=EdgeCondition.SUCCESS, choice="optA"),
                EdgeSpec(from_stage="s01", to_stage="s03", condition=EdgeCondition.SUCCESS, choice="optA"),
            ],
        )
        result = validate_workflow(spec)
        assert any(i.category == "CHOICE_INCONSISTENCY" for i in result.issues)

    def test_ambiguous_routing(self):
        spec = _make_spec(
            stages=[
                StageSpec(stage_id="s01", name="a", target_type=StageTargetType.SKILL, target="skill-a"),
                StageSpec(stage_id="s02", name="b", target_type=StageTargetType.SKILL, target="skill-b"),
                StageSpec(stage_id="s03", name="c", target_type=StageTargetType.SKILL, target="skill-c"),
            ],
            edges=[
                EdgeSpec(from_stage="s01", to_stage="s02", condition=EdgeCondition.SUCCESS, choice="optA"),
                EdgeSpec(from_stage="s01", to_stage="s03", condition=EdgeCondition.SUCCESS),
            ],
        )
        result = validate_workflow(spec)
        assert any(i.category == "AMBIGUOUS_ROUTING" for i in result.issues)

    def test_dead_failure_edge(self):
        """failure_edge 但 retry=0 → 永远不会触发。"""
        spec = _make_spec(
            stages=[
                StageSpec(stage_id="s01", name="a", target_type=StageTargetType.SKILL, target="skill-a", retry=0),
                StageSpec(stage_id="s02", name="b", target_type=StageTargetType.SKILL, target="skill-b"),
            ],
            edges=[
                EdgeSpec(from_stage="s01", to_stage="s02", condition=EdgeCondition.FAILURE),
            ],
        )
        result = validate_workflow(spec)
        assert any(i.category == "DEAD_FAILURE_EDGE" for i in result.issues)

    def test_orphan_loop_exceeded(self):
        """loop_exceeded_edge 但无 failure_edge。"""
        spec = _make_spec(
            stages=[
                StageSpec(stage_id="s01", name="a", target_type=StageTargetType.SKILL, target="skill-a", retry=3),
                StageSpec(stage_id="s02", name="b", target_type=StageTargetType.SKILL, target="skill-b"),
            ],
            edges=[
                EdgeSpec(from_stage="s01", to_stage="s02", condition=EdgeCondition.LOOP_EXCEEDED),
            ],
        )
        result = validate_workflow(spec)
        assert any(i.category == "ORPHAN_LOOP_EXCEEDED" for i in result.issues)

    def test_terminal_leak(self):
        """终态 stage 有非 ALWAYS 出边。"""
        spec = _make_spec(
            stages=[
                StageSpec(stage_id="s01", name="a", target_type=StageTargetType.SKILL, target="skill-a"),
                StageSpec(stage_id="s99-workflow-end", name="工作流终止", target_type=StageTargetType.VIRTUAL),
            ],
            edges=[
                EdgeSpec(from_stage="s01", to_stage="s99-workflow-end", condition=EdgeCondition.SUCCESS),
                EdgeSpec(from_stage="s99-workflow-end", to_stage="s01", condition=EdgeCondition.FAILURE),
            ],
        )
        result = validate_workflow(spec)
        assert any(i.category == "TERMINAL_LEAK" for i in result.issues)

    def test_duplicate_stage_id(self):
        spec = _make_spec(
            stages=[
                StageSpec(stage_id="s01", name="a", target_type=StageTargetType.SKILL, target="skill-a"),
                StageSpec(stage_id="s01", name="a_dup", target_type=StageTargetType.SKILL, target="skill-b"),
            ],
            edges=[],
        )
        result = validate_workflow(spec)
        assert any(i.category == "DUPLICATE_STAGE_ID" for i in result.issues)

