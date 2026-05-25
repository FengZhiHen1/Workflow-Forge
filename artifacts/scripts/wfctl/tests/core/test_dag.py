"""测试 DAG 引擎。"""

import pytest

from domain.dag.graph import build_adjacency, collect_downstream, compute_ready
from domain.workflow.spec import (
    EdgeCondition,
    EdgeSpec,
    StageSpec,
    StageStatus,
    StageTargetType,
    WorkflowSpec,
)
from state.model import InstanceState, StageState


def _ids(ready: list[tuple[str, str]]) -> list[str]:
    return [sid for sid, _ in ready]


def _make_state(stages: list[dict]) -> InstanceState:
    """从 dict 列表快速构建 InstanceState。"""
    return InstanceState(
        instance_id="test",
        stages=[
            StageState(
                stage_id=s["stage_id"],
                stage_instance_id=s.get("stage_instance_id", s["stage_id"]),
                status=StageStatus(s["status"]),
                exit_condition=s.get("exit_condition", ""),
                routing_choice=s.get("routing_choice", ""),
            )
            for s in stages
        ],
    )


def make_spec() -> WorkflowSpec:
    return WorkflowSpec(
        schema_version="3.0.0",
        workflow_id="test",
        version="1.0.0",
        max_parallel_agents=4,
        stages=[
            StageSpec(stage_id="s00", name="start", target_type=StageTargetType.VIRTUAL),
            StageSpec(stage_id="s01", name="a", target_type=StageTargetType.SKILL, target="skill-a", mandatory=True, ),
            StageSpec(stage_id="s02", name="b", target_type=StageTargetType.SKILL, target="skill-b", mandatory=True, ),
            StageSpec(stage_id="s03", name="c", target_type=StageTargetType.SKILL, target="skill-c", mandatory=True, ),
            StageSpec(stage_id="s99", name="end", target_type=StageTargetType.VIRTUAL),
        ],
        edges=[
            EdgeSpec(from_stage="s00", to_stage="s01", condition=EdgeCondition.ALWAYS),
            EdgeSpec(from_stage="s01", to_stage="s02", condition=EdgeCondition.SUCCESS),
            EdgeSpec(from_stage="s01", to_stage="s03", condition=EdgeCondition.SUCCESS),
            EdgeSpec(from_stage="s02", to_stage="s99", condition=EdgeCondition.SUCCESS),
            EdgeSpec(from_stage="s03", to_stage="s99", condition=EdgeCondition.SUCCESS),
        ],
    )


def test_build_adjacency():
    spec = make_spec()
    adj = build_adjacency(spec)
    assert set(adj.outgoing.keys()) == {"s00", "s01", "s02", "s03", "s99"}
    assert len(adj.outgoing["s01"]) == 2


def test_compute_ready_initial():
    spec = make_spec()
    adj = build_adjacency(spec)
    state = _make_state([
        {"stage_id": "s00", "status": "DONE"},
        {"stage_id": "s01", "status": "PENDING"},
        {"stage_id": "s02", "status": "PENDING"},
        {"stage_id": "s03", "status": "PENDING"},
        {"stage_id": "s99", "status": "PENDING"},
    ])
    ready = compute_ready(adj, state)
    assert _ids(ready) == ["s01"]


def test_compute_ready_after_s01():
    spec = make_spec()
    adj = build_adjacency(spec)
    state = _make_state([
        {"stage_id": "s00", "status": "DONE"},
        {"stage_id": "s01", "status": "DONE"},
        {"stage_id": "s02", "status": "PENDING"},
        {"stage_id": "s03", "status": "PENDING"},
        {"stage_id": "s99", "status": "PENDING"},
    ])
    ready = compute_ready(adj, state)
    assert set(_ids(ready)) == {"s02", "s03"}


def test_collect_downstream():
    spec = make_spec()
    adj = build_adjacency(spec)
    downstream = collect_downstream(adj, "s01", set())
    assert downstream == {"s02", "s03", "s99"}


def test_collect_downstream_exclude_failure():
    spec = make_spec()
    adj = build_adjacency(spec)
    downstream = collect_downstream(adj, "s01", {EdgeCondition.FAILURE})
    assert downstream == {"s02", "s03", "s99"}


def test_always_chain_does_not_cascade():
    spec = WorkflowSpec(
        schema_version="3.0.0",
        workflow_id="test-always-chain",
        version="1.0.0",
        max_parallel_agents=4,
        stages=[
            StageSpec(stage_id="s00", name="start", target_type=StageTargetType.VIRTUAL),
            StageSpec(stage_id="s01", name="a", target_type=StageTargetType.SKILL, target="skill-a", mandatory=True, ),
            StageSpec(stage_id="s02", name="b", target_type=StageTargetType.SKILL, target="skill-b", mandatory=True, ),
            StageSpec(stage_id="s03", name="c", target_type=StageTargetType.SKILL, target="skill-c", mandatory=True, ),
            StageSpec(stage_id="s99", name="end", target_type=StageTargetType.VIRTUAL),
        ],
        edges=[
            EdgeSpec(from_stage="s00", to_stage="s01", condition=EdgeCondition.ALWAYS),
            EdgeSpec(from_stage="s01", to_stage="s02", condition=EdgeCondition.ALWAYS),
            EdgeSpec(from_stage="s02", to_stage="s03", condition=EdgeCondition.ALWAYS),
            EdgeSpec(from_stage="s03", to_stage="s99", condition=EdgeCondition.ALWAYS),
        ],
    )
    adj = build_adjacency(spec)
    state = _make_state([
        {"stage_id": "s00", "status": "DONE"},
        {"stage_id": "s01", "status": "PENDING"},
        {"stage_id": "s02", "status": "PENDING"},
        {"stage_id": "s03", "status": "PENDING"},
        {"stage_id": "s99", "status": "PENDING"},
    ])
    ready = compute_ready(adj, state)
    assert _ids(ready) == ["s01"], f"expected only s01, got {ready}"

    # s01 → DONE
    state = _make_state([
        {"stage_id": "s00", "status": "DONE"},
        {"stage_id": "s01", "status": "DONE"},
        {"stage_id": "s02", "status": "PENDING"},
        {"stage_id": "s03", "status": "PENDING"},
        {"stage_id": "s99", "status": "PENDING"},
    ])
    ready = compute_ready(adj, state)
    assert _ids(ready) == ["s02"], f"expected only s02, got {ready}"


def test_always_edge_requires_upstream_done():
    spec = WorkflowSpec(
        schema_version="3.0.0",
        workflow_id="test-always-upstream",
        version="1.0.0",
        max_parallel_agents=6,
        stages=[
            StageSpec(stage_id="s00", name="start", target_type=StageTargetType.VIRTUAL),
            StageSpec(stage_id="p0", name="init", target_type=StageTargetType.SKILL, target="skill-init", mandatory=True, ),
            StageSpec(stage_id="p1a", name="analysis", target_type=StageTargetType.SKILL, target="skill-a", mandatory=True, ),
            StageSpec(stage_id="p1b", name="decompose", target_type=StageTargetType.SKILL, target="skill-b", mandatory=True, ),
            StageSpec(stage_id="s99", name="end", target_type=StageTargetType.VIRTUAL),
        ],
        edges=[
            EdgeSpec(from_stage="s00", to_stage="p0", condition=EdgeCondition.ALWAYS),
            EdgeSpec(from_stage="p0", to_stage="p1a", condition=EdgeCondition.ALWAYS),
            EdgeSpec(from_stage="p1a", to_stage="p1b", condition=EdgeCondition.SUCCESS),
            EdgeSpec(from_stage="p1b", to_stage="s99", condition=EdgeCondition.SUCCESS),
        ],
    )
    adj = build_adjacency(spec)
    state = _make_state([
        {"stage_id": "s00", "status": "DONE"},
        {"stage_id": "p0", "status": "RUNNING"},
        {"stage_id": "p1a", "status": "PENDING"},
        {"stage_id": "p1b", "status": "PENDING"},
        {"stage_id": "s99", "status": "PENDING"},
    ])
    ready = compute_ready(adj, state)
    assert ready == [], f"p0 is RUNNING, no stage should be ready, got {ready}"


def test_multiple_incoming_or_semantics():
    spec = WorkflowSpec(
        schema_version="3.0.0",
        workflow_id="test-or-semantics",
        version="1.0.0",
        max_parallel_agents=1,
        stages=[
            StageSpec(stage_id="p4", name="验证评估", target_type=StageTargetType.SKILL, target="skill-a", mandatory=True, ),
            StageSpec(stage_id="p4-adv", name="验证对抗审查", target_type=StageTargetType.SKILL, target="skill-b", mandatory=True, ),
            StageSpec(stage_id="emerg", name="应急降级", target_type=StageTargetType.SKILL, target="skill-c", mandatory=False, ),
            StageSpec(stage_id="p5", name="完成", target_type=StageTargetType.SKILL, target="skill-d", mandatory=True, ),
            StageSpec(stage_id="s99", name="end", target_type=StageTargetType.VIRTUAL),
        ],
        edges=[
            EdgeSpec(from_stage="p4", to_stage="p4-adv", condition=EdgeCondition.SUCCESS),
            EdgeSpec(from_stage="p4", to_stage="emerg", condition=EdgeCondition.LOOP_EXCEEDED),
            EdgeSpec(from_stage="p4-adv", to_stage="p5", condition=EdgeCondition.SUCCESS),
            EdgeSpec(from_stage="emerg", to_stage="p5", condition=EdgeCondition.ALWAYS),
            EdgeSpec(from_stage="p5", to_stage="s99", condition=EdgeCondition.SUCCESS),
        ],
    )
    adj = build_adjacency(spec)
    state = _make_state([
        {"stage_id": "p4", "status": "DONE"},
        {"stage_id": "p4-adv", "status": "DONE"},
        {"stage_id": "emerg", "status": "PENDING"},
        {"stage_id": "p5", "status": "PENDING"},
        {"stage_id": "s99", "status": "PENDING"},
    ])
    ready = compute_ready(adj, state)
    assert _ids(ready) == ["p5"], f"p4-adv DONE should unlock p5 via OR, got {ready}"


def test_only_special_edges_not_ready():
    spec = WorkflowSpec(
        schema_version="3.0.0",
        workflow_id="test-special-edges",
        version="1.0.0",
        max_parallel_agents=1,
        stages=[
            StageSpec(stage_id="p4", name="验证评估", target_type=StageTargetType.SKILL, target="skill-a", mandatory=True, ),
            StageSpec(stage_id="p4-repair", name="修复路由", target_type=StageTargetType.SKILL, target="skill-b", mandatory=True, ),
            StageSpec(stage_id="emerg", name="应急降级", target_type=StageTargetType.SKILL, target="skill-c", mandatory=False, ),
        ],
        edges=[
            EdgeSpec(from_stage="p4", to_stage="p4-repair", condition=EdgeCondition.FAILURE),
            EdgeSpec(from_stage="p4", to_stage="emerg", condition=EdgeCondition.LOOP_EXCEEDED),
        ],
    )
    adj = build_adjacency(spec)
    state = _make_state([
        {"stage_id": "p4", "status": "RUNNING"},
        {"stage_id": "p4-repair", "status": "PENDING"},
        {"stage_id": "emerg", "status": "PENDING"},
    ])
    ready = compute_ready(adj, state)
    assert ready == [], f"special-only stages should not be ready, got {ready}"


# ─── 对抗性边角测试 ───────────────────────────────────────────────────


def test_diamond_dependency():
    spec = WorkflowSpec(
        schema_version="3.0.0",
        workflow_id="test-diamond",
        version="1.0.0",
        max_parallel_agents=4,
        stages=[
            StageSpec(stage_id="s00", name="start", target_type=StageTargetType.VIRTUAL),
            StageSpec(stage_id="A", name="a", target_type=StageTargetType.SKILL, target="skill-a", mandatory=True),
            StageSpec(stage_id="B", name="b", target_type=StageTargetType.SKILL, target="skill-b", mandatory=True),
            StageSpec(stage_id="C", name="c", target_type=StageTargetType.SKILL, target="skill-c", mandatory=True),
            StageSpec(stage_id="D", name="d", target_type=StageTargetType.SKILL, target="skill-d", mandatory=True),
            StageSpec(stage_id="s99", name="end", target_type=StageTargetType.VIRTUAL),
        ],
        edges=[
            EdgeSpec(from_stage="s00", to_stage="A", condition=EdgeCondition.ALWAYS),
            EdgeSpec(from_stage="A", to_stage="B", condition=EdgeCondition.SUCCESS),
            EdgeSpec(from_stage="A", to_stage="C", condition=EdgeCondition.SUCCESS),
            EdgeSpec(from_stage="B", to_stage="D", condition=EdgeCondition.SUCCESS),
            EdgeSpec(from_stage="C", to_stage="D", condition=EdgeCondition.SUCCESS),
            EdgeSpec(from_stage="D", to_stage="s99", condition=EdgeCondition.SUCCESS),
        ],
    )
    adj = build_adjacency(spec)
    state = _make_state([
        {"stage_id": "s00", "status": "DONE"},
        {"stage_id": "A", "status": "DONE"},
        {"stage_id": "B", "status": "DONE"},
        {"stage_id": "C", "status": "PENDING"},
        {"stage_id": "D", "status": "PENDING"},
        {"stage_id": "s99", "status": "PENDING"},
    ])
    ready = compute_ready(adj, state)
    assert set(_ids(ready)) == {"C", "D"}, f"OR semantics: C and D should both be ready, got {ready}"

    # B PENDING, C DONE → B, D 就绪
    state = _make_state([
        {"stage_id": "s00", "status": "DONE"},
        {"stage_id": "A", "status": "DONE"},
        {"stage_id": "B", "status": "PENDING"},
        {"stage_id": "C", "status": "DONE"},
        {"stage_id": "D", "status": "PENDING"},
        {"stage_id": "s99", "status": "PENDING"},
    ])
    ready = compute_ready(adj, state)
    assert set(_ids(ready)) == {"B", "D"}, f"OR semantics: B and D should both be ready, got {ready}"


def test_unreachable_stage_is_ready():
    spec = WorkflowSpec(
        schema_version="3.0.0",
        workflow_id="test-unreachable",
        version="1.0.0",
        max_parallel_agents=4,
        stages=[
            StageSpec(stage_id="s00", name="start", target_type=StageTargetType.VIRTUAL),
            StageSpec(stage_id="orphan", name="orphan", target_type=StageTargetType.SKILL, target="skill-o", mandatory=True),
            StageSpec(stage_id="s01", name="a", target_type=StageTargetType.SKILL, target="skill-a", mandatory=True),
            StageSpec(stage_id="s99", name="end", target_type=StageTargetType.VIRTUAL),
        ],
        edges=[
            EdgeSpec(from_stage="s00", to_stage="s01", condition=EdgeCondition.ALWAYS),
            EdgeSpec(from_stage="s01", to_stage="s99", condition=EdgeCondition.SUCCESS),
        ],
    )
    adj = build_adjacency(spec)
    state = _make_state([
        {"stage_id": "s00", "status": "DONE"},
        {"stage_id": "orphan", "status": "PENDING"},
        {"stage_id": "s01", "status": "PENDING"},
        {"stage_id": "s99", "status": "PENDING"},
    ])
    ready = compute_ready(adj, state)
    assert "orphan" in _ids(ready), f"stage with no incoming edges should be ready, got {ready}"


def test_success_edge_with_choice_routing():
    """SUCCESS + choice 边的 routing_choice 匹配与不匹配。"""
    spec = WorkflowSpec(
        schema_version="3.0.0",
        workflow_id="test-success-choice",
        version="1.0.0",
        max_parallel_agents=4,
        stages=[
            StageSpec(stage_id="s00", name="start", target_type=StageTargetType.VIRTUAL),
            StageSpec(stage_id="s01", name="a", target_type=StageTargetType.SKILL, target="skill-a", mandatory=True),
            StageSpec(stage_id="s02", name="b", target_type=StageTargetType.SKILL, target="skill-b", mandatory=True),
            StageSpec(stage_id="s99", name="end", target_type=StageTargetType.VIRTUAL),
        ],
        edges=[
            EdgeSpec(from_stage="s00", to_stage="s01", condition=EdgeCondition.ALWAYS),
            EdgeSpec(from_stage="s01", to_stage="s02", condition=EdgeCondition.SUCCESS, choice="通过"),
            EdgeSpec(from_stage="s02", to_stage="s99", condition=EdgeCondition.SUCCESS),
        ],
    )
    adj = build_adjacency(spec)

    # routing_choice 不匹配 → s02 不应就绪
    state = _make_state([
        {"stage_id": "s00", "status": "DONE"},
        {"stage_id": "s01", "status": "DONE", "exit_condition": "success", "routing_choice": "other"},
        {"stage_id": "s02", "status": "PENDING"},
        {"stage_id": "s99", "status": "PENDING"},
    ])
    ready = compute_ready(adj, state)
    assert _ids(ready) == [], f"mismatched routing_choice should block s02, got {ready}"

    # routing_choice 匹配 → s02 就绪
    state = _make_state([
        {"stage_id": "s00", "status": "DONE"},
        {"stage_id": "s01", "status": "DONE", "exit_condition": "success", "routing_choice": "通过"},
        {"stage_id": "s02", "status": "PENDING"},
        {"stage_id": "s99", "status": "PENDING"},
    ])
    ready = compute_ready(adj, state)
    assert _ids(ready) == ["s02"], f"matched routing_choice should unlock s02, got {ready}"


def test_success_edge_with_choices_needs_routing_choice():
    spec = WorkflowSpec(
        schema_version="3.0.0",
        workflow_id="test-success-choices",
        version="1.0.0",
        max_parallel_agents=4,
        stages=[
            StageSpec(stage_id="s00", name="start", target_type=StageTargetType.VIRTUAL),
            StageSpec(stage_id="s01", name="a", target_type=StageTargetType.SKILL, target="skill-a", mandatory=True),
            StageSpec(stage_id="s02", name="b", target_type=StageTargetType.SKILL, target="skill-b", mandatory=True),
            StageSpec(stage_id="s03", name="c", target_type=StageTargetType.SKILL, target="skill-c", mandatory=True),
            StageSpec(stage_id="s99", name="end", target_type=StageTargetType.VIRTUAL),
        ],
        edges=[
            EdgeSpec(from_stage="s00", to_stage="s01", condition=EdgeCondition.ALWAYS),
            EdgeSpec(from_stage="s01", to_stage="s02", condition=EdgeCondition.SUCCESS, choice="path-a"),
            EdgeSpec(from_stage="s01", to_stage="s03", condition=EdgeCondition.SUCCESS, choice="path-b"),
            EdgeSpec(from_stage="s02", to_stage="s99", condition=EdgeCondition.SUCCESS),
            EdgeSpec(from_stage="s03", to_stage="s99", condition=EdgeCondition.SUCCESS),
        ],
    )
    adj = build_adjacency(spec)
    state = _make_state([
        {"stage_id": "s00", "status": "DONE"},
        {"stage_id": "s01", "status": "DONE", "exit_condition": "success", "routing_choice": "wrong"},
        {"stage_id": "s02", "status": "PENDING"},
        {"stage_id": "s03", "status": "PENDING"},
        {"stage_id": "s99", "status": "PENDING"},
    ])
    ready = compute_ready(adj, state)
    assert ready == [], f"unmatched routing_choice should not unlock any stage, got {ready}"

    state = _make_state([
        {"stage_id": "s00", "status": "DONE"},
        {"stage_id": "s01", "status": "DONE", "exit_condition": "success", "routing_choice": "path-a"},
        {"stage_id": "s02", "status": "PENDING"},
        {"stage_id": "s03", "status": "PENDING"},
        {"stage_id": "s99", "status": "PENDING"},
    ])
    ready = compute_ready(adj, state)
    assert _ids(ready) == ["s02"], f"routing_choice=path-a should unlock s02, got {ready}"


def test_downstream_collection_respects_all_branches():
    spec = WorkflowSpec(
        schema_version="3.0.0",
        workflow_id="test-branches",
        version="1.0.0",
        max_parallel_agents=4,
        stages=[
            StageSpec(stage_id="s00", name="start", target_type=StageTargetType.VIRTUAL),
            StageSpec(stage_id="s01", name="a", target_type=StageTargetType.SKILL, target="skill-a", mandatory=True),
            StageSpec(stage_id="s02", name="b", target_type=StageTargetType.SKILL, target="skill-b", mandatory=True),
            StageSpec(stage_id="s03", name="c", target_type=StageTargetType.SKILL, target="skill-c", mandatory=True),
            StageSpec(stage_id="s99", name="end", target_type=StageTargetType.VIRTUAL),
        ],
        edges=[
            EdgeSpec(from_stage="s00", to_stage="s01", condition=EdgeCondition.ALWAYS),
            EdgeSpec(from_stage="s01", to_stage="s02", condition=EdgeCondition.SUCCESS),
            EdgeSpec(from_stage="s01", to_stage="s03", condition=EdgeCondition.FAILURE),
            EdgeSpec(from_stage="s02", to_stage="s99", condition=EdgeCondition.SUCCESS),
            EdgeSpec(from_stage="s03", to_stage="s99", condition=EdgeCondition.SUCCESS),
        ],
    )
    adj = build_adjacency(spec)
    downstream = collect_downstream(adj, "s01", set())
    assert downstream == {"s02", "s03", "s99"}
    downstream_no_failure = collect_downstream(adj, "s01", {EdgeCondition.FAILURE})
    assert downstream_no_failure == {"s02", "s99"}


def test_single_stage_workflow():
    spec = WorkflowSpec(
        schema_version="3.0.0",
        workflow_id="test-single",
        version="1.0.0",
        max_parallel_agents=4,
        stages=[
            StageSpec(stage_id="s00", name="start", target_type=StageTargetType.VIRTUAL),
            StageSpec(stage_id="A", name="a", target_type=StageTargetType.SKILL, target="skill-a", mandatory=True),
            StageSpec(stage_id="s99", name="end", target_type=StageTargetType.VIRTUAL),
        ],
        edges=[
            EdgeSpec(from_stage="s00", to_stage="A", condition=EdgeCondition.ALWAYS),
            EdgeSpec(from_stage="A", to_stage="s99", condition=EdgeCondition.SUCCESS),
        ],
    )
    adj = build_adjacency(spec)
    state = _make_state([
        {"stage_id": "s00", "status": "DONE"},
        {"stage_id": "A", "status": "PENDING"},
        {"stage_id": "s99", "status": "PENDING"},
    ])
    ready = compute_ready(adj, state)
    assert _ids(ready) == ["A"], f"expected only A, got {ready}"


def test_rejected_edge_not_in_ready_computation():
    spec = WorkflowSpec(
        schema_version="3.0.0",
        workflow_id="test-rejected",
        version="1.0.0",
        max_parallel_agents=4,
        stages=[
            StageSpec(stage_id="s00", name="start", target_type=StageTargetType.VIRTUAL),
            StageSpec(stage_id="s01", name="a", target_type=StageTargetType.SKILL, target="skill-a", mandatory=True),
            StageSpec(stage_id="s02", name="b", target_type=StageTargetType.SKILL, target="skill-b", mandatory=True),
            StageSpec(stage_id="s99", name="end", target_type=StageTargetType.VIRTUAL),
        ],
        edges=[
            EdgeSpec(from_stage="s00", to_stage="s01", condition=EdgeCondition.ALWAYS),
            EdgeSpec(from_stage="s01", to_stage="s02", condition=EdgeCondition.SUCCESS, choice="通过"),
            EdgeSpec(from_stage="s01", to_stage="s99", condition=EdgeCondition.SUCCESS, choice="放弃"),
            EdgeSpec(from_stage="s02", to_stage="s99", condition=EdgeCondition.SUCCESS),
        ],
    )
    adj = build_adjacency(spec)
    state = _make_state([
        {"stage_id": "s00", "status": "DONE"},
        {"stage_id": "s01", "status": "DONE", "exit_condition": "rejected"},
        {"stage_id": "s02", "status": "PENDING"},
        {"stage_id": "s99", "status": "PENDING"},
    ])
    ready = compute_ready(adj, state)
    assert ready == [], f"rejected edge should not trigger ready, got {ready}"


def test_compute_ready_parallel_instances():
    """Parallel 场景：同一 stage_id 有多个 PENDING 实例，且上游不同。"""
    spec = WorkflowSpec(
        schema_version="3.0.0",
        workflow_id="test-parallel",
        version="1.0.0",
        max_parallel_agents=4,
        stages=[
            StageSpec(stage_id="s00", name="start", target_type=StageTargetType.VIRTUAL),
            StageSpec(stage_id="s01", name="split", target_type=StageTargetType.SKILL, target="skill-a"),
            StageSpec(stage_id="s02", name="join", target_type=StageTargetType.VIRTUAL),
        ],
        edges=[
            EdgeSpec(from_stage="s00", to_stage="s01", condition=EdgeCondition.ALWAYS),
            EdgeSpec(from_stage="s01", to_stage="s02", condition=EdgeCondition.SUCCESS),
        ],
    )
    adj = build_adjacency(spec)
    state = InstanceState(
        instance_id="test",
        stages=[
            StageState(stage_id="s00", stage_instance_id="s00", status=StageStatus.DONE),
            StageState(stage_id="s01", stage_instance_id="s01_0", status=StageStatus.DONE, exit_condition="success"),
            StageState(stage_id="s01", stage_instance_id="s01_1", status=StageStatus.PENDING),
            StageState(stage_id="s01", stage_instance_id="s01_2", status=StageStatus.PENDING),
            StageState(stage_id="s02", stage_instance_id="s02", status=StageStatus.PENDING),
        ],
    )
    ready = compute_ready(adj, state)
    ids = _ids(ready)
    # s01_1 and s01_2 都是 PENDING s01，但上游 s01_0 DONE 已满足 → s02 就绪
    assert "s02" in ids, f"s02 should be ready, got {ids}"
