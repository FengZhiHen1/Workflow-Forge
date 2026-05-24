"""测试 DAG 引擎。"""

import pytest

from core.dag import build_adjacency, collect_downstream, compute_ready
from core.schema.interface import (
    EdgeCondition,
    EdgeSpec,
    StageSpec,
    StageTargetType,
    WorkflowSpec,
)


def make_spec() -> WorkflowSpec:
    return WorkflowSpec(
        schema_version="3.0.0",
        workflow_id="test",
        version="1.0.0",
        max_parallel_agents=4,
        stages=[
            StageSpec(stage_id="s00", name="start", target_type=StageTargetType.VIRTUAL),
            StageSpec(stage_id="s01", name="a", target_type=StageTargetType.SKILL, target="skill-a", mandatory=True, confirmation_point=False),
            StageSpec(stage_id="s02", name="b", target_type=StageTargetType.SKILL, target="skill-b", mandatory=True, confirmation_point=False),
            StageSpec(stage_id="s03", name="c", target_type=StageTargetType.SKILL, target="skill-c", mandatory=True, confirmation_point=False),
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
    assert len(adj.outgoing["s01"]) == 2  # s01 → s02, s03


def test_compute_ready_initial():
    spec = make_spec()
    adj = build_adjacency(spec)
    instance = {
        "stages": [
            {"stage_id": "s00", "status": "DONE"},
            {"stage_id": "s01", "status": "PENDING"},
            {"stage_id": "s02", "status": "PENDING"},
            {"stage_id": "s03", "status": "PENDING"},
            {"stage_id": "s99", "status": "PENDING"},
        ]
    }
    ready = compute_ready(adj, instance)
    assert ready == ["s01"]


def test_compute_ready_after_s01():
    spec = make_spec()
    adj = build_adjacency(spec)
    instance = {
        "stages": [
            {"stage_id": "s00", "status": "DONE"},
            {"stage_id": "s01", "status": "DONE"},
            {"stage_id": "s02", "status": "PENDING"},
            {"stage_id": "s03", "status": "PENDING"},
            {"stage_id": "s99", "status": "PENDING"},
        ]
    }
    ready = compute_ready(adj, instance)
    assert set(ready) == {"s02", "s03"}


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
    """回归测试：always 边链不应级联放行未完成的上游。

    s00→s01→s02→s03→s99，全部 always。
    s00 DONE 时，仅 s01 就该绪。s02/s03/s99 必须等各自上游 DONE。
    """
    spec = WorkflowSpec(
        schema_version="3.0.0",
        workflow_id="test-always-chain",
        version="1.0.0",
        max_parallel_agents=4,
        stages=[
            StageSpec(stage_id="s00", name="start", target_type=StageTargetType.VIRTUAL),
            StageSpec(stage_id="s01", name="a", target_type=StageTargetType.SKILL, target="skill-a", mandatory=True, confirmation_point=False),
            StageSpec(stage_id="s02", name="b", target_type=StageTargetType.SKILL, target="skill-b", mandatory=True, confirmation_point=False),
            StageSpec(stage_id="s03", name="c", target_type=StageTargetType.SKILL, target="skill-c", mandatory=True, confirmation_point=False),
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

    # 仅 s00 DONE，只有 s01 该就绪
    instance = {
        "stages": [
            {"stage_id": "s00", "status": "DONE"},
            {"stage_id": "s01", "status": "PENDING"},
            {"stage_id": "s02", "status": "PENDING"},
            {"stage_id": "s03", "status": "PENDING"},
            {"stage_id": "s99", "status": "PENDING"},
        ]
    }
    ready = compute_ready(adj, instance)
    assert ready == ["s01"], f"expected only s01, got {ready}"

    # s00 和 s01 都 DONE，s02 该就绪
    instance["stages"][1]["status"] = "DONE"  # s01 → DONE
    ready = compute_ready(adj, instance)
    assert ready == ["s02"], f"expected only s02, got {ready}"


def test_always_edge_requires_upstream_done():
    """回归测试：always 边必须有上游 DONE 才算满足。

    模拟 real-world 场景：p0(RUNNING)→p1a(always)，p1a 不应就绪。
    """
    spec = WorkflowSpec(
        schema_version="3.0.0",
        workflow_id="test-always-upstream",
        version="1.0.0",
        max_parallel_agents=6,
        stages=[
            StageSpec(stage_id="s00", name="start", target_type=StageTargetType.VIRTUAL),
            StageSpec(stage_id="p0", name="init", target_type=StageTargetType.SKILL, target="skill-init", mandatory=True, confirmation_point=False),
            StageSpec(stage_id="p1a", name="analysis", target_type=StageTargetType.SKILL, target="skill-a", mandatory=True, confirmation_point=True),
            StageSpec(stage_id="p1b", name="decompose", target_type=StageTargetType.SKILL, target="skill-b", mandatory=True, confirmation_point=False),
            StageSpec(stage_id="s99", name="end", target_type=StageTargetType.VIRTUAL),
        ],
        edges=[
            EdgeSpec(from_stage="s00", to_stage="p0", condition=EdgeCondition.ALWAYS),
            EdgeSpec(from_stage="p0", to_stage="p1a", condition=EdgeCondition.ALWAYS),
            EdgeSpec(from_stage="p1a", to_stage="p1b", condition=EdgeCondition.CONFIRMED),
            EdgeSpec(from_stage="p1b", to_stage="s99", condition=EdgeCondition.SUCCESS),
        ],
    )
    adj = build_adjacency(spec)

    # s00 DONE, p0 RUNNING —— p0→p1a(always) 不应满足
    instance = {
        "stages": [
            {"stage_id": "s00", "status": "DONE"},
            {"stage_id": "p0", "status": "RUNNING"},
            {"stage_id": "p1a", "status": "PENDING"},
            {"stage_id": "p1b", "status": "PENDING"},
            {"stage_id": "s99", "status": "PENDING"},
        ]
    }
    ready = compute_ready(adj, instance)
    assert ready == [], f"p0 is RUNNING, no stage should be ready, got {ready}"


def test_multiple_incoming_or_semantics():
    """回归测试：多入边应取 OR 语义——任一路径畅通即可解锁。

    模拟 p5-complete 有两条到达路径：
      p4-adv→p5 (success)   emerg→p5 (always)
    p4-adv DONE 时 p5 就该绪，不管 emerg 状态。
    """
    spec = WorkflowSpec(
        schema_version="3.0.0",
        workflow_id="test-or-semantics",
        version="1.0.0",
        max_parallel_agents=1,
        stages=[
            StageSpec(stage_id="p4", name="验证评估", target_type=StageTargetType.SKILL, target="skill-a", mandatory=True, confirmation_point=False),
            StageSpec(stage_id="p4-adv", name="验证对抗审查", target_type=StageTargetType.SKILL, target="skill-b", mandatory=True, confirmation_point=False),
            StageSpec(stage_id="emerg", name="应急降级", target_type=StageTargetType.SKILL, target="skill-c", mandatory=False, confirmation_point=False),
            StageSpec(stage_id="p5", name="完成", target_type=StageTargetType.SKILL, target="skill-d", mandatory=True, confirmation_point=True),
            StageSpec(stage_id="s99", name="end", target_type=StageTargetType.VIRTUAL),
        ],
        edges=[
            EdgeSpec(from_stage="p4", to_stage="p4-adv", condition=EdgeCondition.SUCCESS),
            EdgeSpec(from_stage="p4", to_stage="emerg", condition=EdgeCondition.LOOP_EXCEEDED),
            EdgeSpec(from_stage="p4-adv", to_stage="p5", condition=EdgeCondition.SUCCESS),
            EdgeSpec(from_stage="emerg", to_stage="p5", condition=EdgeCondition.ALWAYS),
            EdgeSpec(from_stage="p5", to_stage="s99", condition=EdgeCondition.CONFIRMED),
        ],
    )
    adj = build_adjacency(spec)

    # p4 DONE, p4-adv DONE, emerg PENDING → p5 就该绪（OR：p4-adv 路径已通）
    instance = {
        "stages": [
            {"stage_id": "p4", "status": "DONE"},
            {"stage_id": "p4-adv", "status": "DONE"},
            {"stage_id": "emerg", "status": "PENDING"},
            {"stage_id": "p5", "status": "PENDING"},
            {"stage_id": "s99", "status": "PENDING"},
        ]
    }
    ready = compute_ready(adj, instance)
    assert ready == ["p5"], f"p4-adv DONE should unlock p5 via OR, got {ready}"


def test_only_special_edges_not_ready():
    """回归测试：仅有 failure/loop_exceeded 入边的 stage 不应出现在就绪列表。

    模拟 p4-repair（仅 failure 入边）和 emergency-fallback（仅 loop_exceeded 入边）。
    """
    spec = WorkflowSpec(
        schema_version="3.0.0",
        workflow_id="test-special-edges",
        version="1.0.0",
        max_parallel_agents=1,
        stages=[
            StageSpec(stage_id="p4", name="验证评估", target_type=StageTargetType.SKILL, target="skill-a", mandatory=True, confirmation_point=False),
            StageSpec(stage_id="p4-repair", name="修复路由", target_type=StageTargetType.SKILL, target="skill-b", mandatory=True, confirmation_point=True),
            StageSpec(stage_id="emerg", name="应急降级", target_type=StageTargetType.SKILL, target="skill-c", mandatory=False, confirmation_point=False),
        ],
        edges=[
            EdgeSpec(from_stage="p4", to_stage="p4-repair", condition=EdgeCondition.FAILURE),
            EdgeSpec(from_stage="p4", to_stage="emerg", condition=EdgeCondition.LOOP_EXCEEDED),
        ],
    )
    adj = build_adjacency(spec)

    # p4 RUNNING。p4-repair（仅 failure 入边）和 emerg（仅 loop_exceeded 入边）
    # 都没有激活边 → 都不该就绪
    instance = {
        "stages": [
            {"stage_id": "p4", "status": "RUNNING"},
            {"stage_id": "p4-repair", "status": "PENDING"},
            {"stage_id": "emerg", "status": "PENDING"},
        ]
    }
    ready = compute_ready(adj, instance)
    assert ready == [], f"special-only stages should not be ready, got {ready}"


# ─── 对抗性边角测试 ───────────────────────────────────────────────────


def test_diamond_dependency():
    """A→B, A→C, B→D, C→D: DAG 使用 OR 语义，任一路径畅通即可解锁 D。"""
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

    # A DONE, B DONE, C PENDING → D 就绪（OR 语义：B→D 路径已畅通）
    instance = {
        "stages": [
            {"stage_id": "s00", "status": "DONE"},
            {"stage_id": "A", "status": "DONE"},
            {"stage_id": "B", "status": "DONE"},
            {"stage_id": "C", "status": "PENDING"},
            {"stage_id": "D", "status": "PENDING"},
            {"stage_id": "s99", "status": "PENDING"},
        ]
    }
    ready = compute_ready(adj, instance)
    assert set(ready) == {"C", "D"}, f"OR semantics: C and D should both be ready, got {ready}"

    # A DONE, B PENDING, C DONE → D 就绪（OR 语义：C→D 路径已畅通）
    instance["stages"][2]["status"] = "PENDING"    # B → PENDING
    instance["stages"][3]["status"] = "DONE"       # C → DONE
    ready = compute_ready(adj, instance)
    assert set(ready) == {"B", "D"}, f"OR semantics: B and D should both be ready, got {ready}"


def test_unreachable_stage_is_ready():
    """无入边的 stage 就绪——上游依赖真空满足。"""
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
            # orphan 无任何入边 → 真空满足，视为就绪
        ],
    )
    adj = build_adjacency(spec)

    instance = {
        "stages": [
            {"stage_id": "s00", "status": "DONE"},
            {"stage_id": "orphan", "status": "PENDING"},
            {"stage_id": "s01", "status": "PENDING"},
            {"stage_id": "s99", "status": "PENDING"},
        ]
    }
    ready = compute_ready(adj, instance)
    assert "orphan" in ready, f"stage with no incoming edges should be ready, got {ready}"


def test_confirmed_edge_empty_exit_condition_compat():
    """confirmed 边在 upstream DONE + exit_condition='' 时视为满足（兼容旧实例）。"""
    spec = WorkflowSpec(
        schema_version="3.0.0",
        workflow_id="test-confirmed",
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
            EdgeSpec(from_stage="s01", to_stage="s02", condition=EdgeCondition.CONFIRMED, choice="通过"),
            EdgeSpec(from_stage="s02", to_stage="s99", condition=EdgeCondition.SUCCESS),
        ],
    )
    adj = build_adjacency(spec)

    # ""(空) exit_condition 兼容旧实例 → s02 就绪
    instance = {
        "stages": [
            {"stage_id": "s00", "status": "DONE"},
            {"stage_id": "s01", "status": "DONE", "exit_condition": ""},
            {"stage_id": "s02", "status": "PENDING"},
            {"stage_id": "s99", "status": "PENDING"},
        ]
    }
    ready = compute_ready(adj, instance)
    assert ready == ["s02"], f"empty exit_condition is backward-compatible, got {ready}"

    # exit_condition=confirmed → s02 就绪
    instance["stages"][1]["exit_condition"] = "confirmed"
    ready = compute_ready(adj, instance)
    assert ready == ["s02"], f"explicit confirmed should also work, got {ready}"


def test_success_edge_with_choices_needs_routing_choice():
    """SUCCESS 边的 choice 必须匹配 upstream routing_choice 才能路由。"""
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

    # routing_choice 不匹配 → 无 stage 就绪
    instance = {
        "stages": [
            {"stage_id": "s00", "status": "DONE"},
            {"stage_id": "s01", "status": "DONE", "exit_condition": "success", "routing_choice": "wrong"},
            {"stage_id": "s02", "status": "PENDING"},
            {"stage_id": "s03", "status": "PENDING"},
            {"stage_id": "s99", "status": "PENDING"},
        ]
    }
    ready = compute_ready(adj, instance)
    assert ready == [], f"unmatched routing_choice should not unlock any stage, got {ready}"

    # routing_choice="path-a" → s02 就绪
    instance["stages"][1]["routing_choice"] = "path-a"
    ready = compute_ready(adj, instance)
    assert ready == ["s02"], f"routing_choice=path-a should unlock s02, got {ready}"


def test_downstream_collection_respects_all_branches():
    """collect_downstream 应收敛所有可达非虚拟 stage（含 failure 等分支）。"""
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
    """最简工作流：Start → A → End。"""
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

    instance = {
        "stages": [
            {"stage_id": "s00", "status": "DONE"},
            {"stage_id": "A", "status": "PENDING"},
            {"stage_id": "s99", "status": "PENDING"},
        ]
    }
    ready = compute_ready(adj, instance)
    assert ready == ["A"], f"expected only A, got {ready}"


def test_rejected_edge_not_in_ready_computation():
    """rejected 边不应在 compute_ready 中触发就绪。"""
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
            EdgeSpec(from_stage="s01", to_stage="s02", condition=EdgeCondition.CONFIRMED, choice="通过"),
            EdgeSpec(from_stage="s01", to_stage="s99", condition=EdgeCondition.REJECTED, choice="放弃"),
            EdgeSpec(from_stage="s02", to_stage="s99", condition=EdgeCondition.SUCCESS),
        ],
    )
    adj = build_adjacency(spec)

    # s01 DONE exit_condition=rejected → s99 不应通过 rejected 边触发就绪
    instance = {
        "stages": [
            {"stage_id": "s00", "status": "DONE"},
            {"stage_id": "s01", "status": "DONE", "exit_condition": "rejected"},
            {"stage_id": "s02", "status": "PENDING"},
            {"stage_id": "s99", "status": "PENDING"},
        ]
    }
    ready = compute_ready(adj, instance)
    assert ready == [], f"rejected edge should not trigger ready, got {ready}"
