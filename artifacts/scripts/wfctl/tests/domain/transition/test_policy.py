"""测试 domain.transition.policy — TransitionPolicy。"""

import pytest

from domain.dag.graph import AdjacencyList, build_adjacency
from domain.workflow.spec import (
    EdgeCondition,
    EdgeSpec,
    InstanceStatus,
    StageSpec,
    StageStatus,
    StageTargetType,
    WorkflowSpec,
)
from domain.transition.policy import TransitionPolicy
from domain.transition.results import (
    ConfirmResult,
    MergeConfirmResult,
    RollbackResult,
    SkipResult,
    TransitionResult,
)
from state.model import InstanceState, StageState


def _make_simple_adj() -> AdjacencyList:
    """线性工作流: s00→s01→s02→s99。"""
    spec = WorkflowSpec(
        schema_version="3.0.0",
        workflow_id="test",
        version="1.0.0",
        max_parallel_agents=4,
        stages=[
            StageSpec(stage_id="s00", name="start", target_type=StageTargetType.VIRTUAL),
            StageSpec(stage_id="s01", name="a", target_type=StageTargetType.SKILL, target="skill-a"),
            StageSpec(stage_id="s02", name="b", target_type=StageTargetType.SKILL, target="skill-b", retry=2),
            StageSpec(stage_id="s99", name="end", target_type=StageTargetType.VIRTUAL),
        ],
        edges=[
            EdgeSpec(from_stage="s00", to_stage="s01", condition=EdgeCondition.ALWAYS),
            EdgeSpec(from_stage="s01", to_stage="s02", condition=EdgeCondition.SUCCESS),
            EdgeSpec(from_stage="s02", to_stage="s99", condition=EdgeCondition.SUCCESS),
        ],
    )
    return build_adjacency(spec)


def _make_full_adj() -> AdjacencyList:
    """包含所有边类型的工作流。"""
    spec = WorkflowSpec(
        schema_version="3.0.0",
        workflow_id="full",
        version="1.0.0",
        max_parallel_agents=4,
        stages=[
            StageSpec(stage_id="s00", name="start", target_type=StageTargetType.VIRTUAL),
            StageSpec(stage_id="s01", name="main", target_type=StageTargetType.SKILL, target="skill-a", retry=1),
            StageSpec(stage_id="s02", name="confirmed-path", target_type=StageTargetType.SKILL, target="skill-b"),
            StageSpec(stage_id="s03", name="rejected-path", target_type=StageTargetType.SKILL, target="skill-c"),
            StageSpec(stage_id="s04", name="error-recovery", target_type=StageTargetType.SKILL, target="skill-d"),
            StageSpec(stage_id="s05", name="loop-exceeded-recovery", target_type=StageTargetType.SKILL, target="skill-e"),
            StageSpec(stage_id="s99", name="end", target_type=StageTargetType.VIRTUAL),
        ],
        edges=[
            EdgeSpec(from_stage="s00", to_stage="s01", condition=EdgeCondition.ALWAYS),
            EdgeSpec(from_stage="s01", to_stage="s02", condition=EdgeCondition.CONFIRMED, choice="通过"),
            EdgeSpec(from_stage="s01", to_stage="s03", condition=EdgeCondition.REJECTED, choice="放弃"),
            EdgeSpec(from_stage="s01", to_stage="s04", condition=EdgeCondition.FAILURE),
            EdgeSpec(from_stage="s01", to_stage="s05", condition=EdgeCondition.LOOP_EXCEEDED, max_loop=2),
            EdgeSpec(from_stage="s01", to_stage="s99", condition=EdgeCondition.SUCCESS),
            EdgeSpec(from_stage="s02", to_stage="s99", condition=EdgeCondition.SUCCESS),
            EdgeSpec(from_stage="s03", to_stage="s99", condition=EdgeCondition.SUCCESS),
            EdgeSpec(from_stage="s04", to_stage="s99", condition=EdgeCondition.SUCCESS),
            EdgeSpec(from_stage="s05", to_stage="s99", condition=EdgeCondition.SUCCESS),
        ],
    )
    return build_adjacency(spec)


class TestTransitionResult:
    def test_default_construction(self):
        r = TransitionResult(next_status=StageStatus.DONE)
        assert r.next_status == StageStatus.DONE
        assert r.target_stage_id is None
        assert r.updates == {}
        assert r.action == ""

    def test_with_action(self):
        r = TransitionResult(
            next_status=StageStatus.PENDING,
            action="retry",
            updates={"attempt_count": 2},
        )
        assert r.action == "retry"
        assert r.updates["attempt_count"] == 2


class TestTransitionPolicyFromAdjacency:
    def test_linear_graph(self):
        """线性图：验证边分类。"""
        adj = _make_simple_adj()
        policy = TransitionPolicy.from_adjacency(adj, "s01")

        assert policy.stage_id == "s01"
        assert len(policy.ready_edges) == 1
        assert policy.ready_edges[0].to_stage == "s02"
        assert policy.failure_edge is None
        assert policy.loop_exceeded_edge is None
        assert policy.confirmed_edges == []
        assert policy.rejected_edges == []

    def test_full_graph_edge_categorization(self):
        """包含所有边类型的图：验证全分类正确。"""
        adj = _make_full_adj()
        policy = TransitionPolicy.from_adjacency(adj, "s01")

        assert len(policy.ready_edges) == 1
        assert policy.ready_edges[0].condition == EdgeCondition.SUCCESS
        assert len(policy.confirmed_edges) == 1
        assert policy.confirmed_edges[0].choice == "通过"
        assert len(policy.rejected_edges) == 1
        assert policy.rejected_edges[0].choice == "放弃"
        assert policy.failure_edge is not None
        assert policy.failure_edge.to_stage == "s04"
        assert policy.loop_exceeded_edge is not None
        assert policy.loop_exceeded_edge.max_loop == 2

    def test_stage_not_found(self):
        """不存在的 stage_id 抛出 KeyError。"""
        adj = _make_simple_adj()
        with pytest.raises(KeyError, match="Stage 'nonexistent' not found"):
            TransitionPolicy.from_adjacency(adj, "nonexistent")


class TestIsUpstreamSatisfied:
    def test_always_edge_with_done_upstream(self):
        """s00 DONE + edge(s00→s01, ALWAYS) → s01 的上游已满足。"""
        adj = _make_simple_adj()
        s00_policy = TransitionPolicy.from_adjacency(adj, "s00")
        upstream = StageState(stage_id="s00", stage_instance_id="s00", status=StageStatus.DONE)
        edge = s00_policy.ready_edges[0]  # s00→s01, ALWAYS
        assert s00_policy.is_upstream_satisfied(upstream, edge) is True

    def test_always_edge_with_done(self):
        """ALWAYS 边：上游 DONE → True。"""
        adj = _make_simple_adj()
        policy = TransitionPolicy.from_adjacency(adj, "s00")
        edge = EdgeSpec(from_stage="s00", to_stage="s01", condition=EdgeCondition.ALWAYS)
        upstream = StageState(stage_id="s00", stage_instance_id="s00", status=StageStatus.DONE)
        assert policy.is_upstream_satisfied(upstream, edge) is True

    def test_pending_upstream_not_satisfied(self):
        """上游 PENDING → False（不管边条件）。"""
        adj = _make_simple_adj()
        policy = TransitionPolicy.from_adjacency(adj, "s00")
        edge = EdgeSpec(from_stage="s00", to_stage="s01", condition=EdgeCondition.ALWAYS)
        upstream = StageState(stage_id="s00", stage_instance_id="s00", status=StageStatus.PENDING)
        assert policy.is_upstream_satisfied(upstream, edge) is False

    def test_success_with_matching_choice(self):
        """SUCCESS 边 + routing_choice 匹配 → True。"""
        adj = _make_full_adj()
        policy = TransitionPolicy.from_adjacency(adj, "s01")
        edge = EdgeSpec(from_stage="s01", to_stage="s99", condition=EdgeCondition.SUCCESS)
        upstream = StageState(
            stage_id="s01", stage_instance_id="s01",
            status=StageStatus.DONE, exit_condition="success",
        )
        assert policy.is_upstream_satisfied(upstream, edge) is True

    def test_success_with_mismatched_choice(self):
        """SUCCESS 边 + routing_choice 不匹配 → False。"""
        adj = _make_full_adj()
        policy = TransitionPolicy.from_adjacency(adj, "s01")
        # Create an edge with a choice that doesn't match
        edge = EdgeSpec(from_stage="s01", to_stage="s02", condition=EdgeCondition.SUCCESS, choice="path-A")
        upstream = StageState(
            stage_id="s01", stage_instance_id="s01",
            status=StageStatus.DONE, exit_condition="success", routing_choice="path-B",
        )
        assert policy.is_upstream_satisfied(upstream, edge) is False

    def test_confirmed_with_matching_choice(self):
        """CONFIRMED 边 + confirmed_choice 匹配 → True。"""
        adj = _make_full_adj()
        policy = TransitionPolicy.from_adjacency(adj, "s01")
        edge = EdgeSpec(from_stage="s01", to_stage="s02", condition=EdgeCondition.CONFIRMED, choice="通过")
        upstream = StageState(
            stage_id="s01", stage_instance_id="s01",
            status=StageStatus.DONE, exit_condition="confirmed", confirmed_choice="通过",
        )
        assert policy.is_upstream_satisfied(upstream, edge) is True

    def test_confirmed_with_mismatched_choice(self):
        """CONFIRMED 边 + confirmed_choice 不匹配 → False。"""
        adj = _make_full_adj()
        policy = TransitionPolicy.from_adjacency(adj, "s01")
        edge = EdgeSpec(from_stage="s01", to_stage="s02", condition=EdgeCondition.CONFIRMED, choice="通过")
        upstream = StageState(
            stage_id="s01", stage_instance_id="s01",
            status=StageStatus.DONE, exit_condition="confirmed", confirmed_choice="放弃",
        )
        assert policy.is_upstream_satisfied(upstream, edge) is False

    def test_empty_exit_condition_compat(self):
        """空 exit_condition 兼容旧实例 → True。"""
        adj = _make_simple_adj()
        policy = TransitionPolicy.from_adjacency(adj, "s01")
        edge = EdgeSpec(from_stage="s01", to_stage="s02", condition=EdgeCondition.SUCCESS)
        upstream = StageState(
            stage_id="s01", stage_instance_id="s01",
            status=StageStatus.DONE, exit_condition="",
        )
        assert policy.is_upstream_satisfied(upstream, edge) is True

    def test_loop_exceeded_not_success(self):
        """exit_condition="loop_exceeded" 不满足 SUCCESS 边 → False。"""
        adj = _make_simple_adj()
        policy = TransitionPolicy.from_adjacency(adj, "s01")
        edge = EdgeSpec(from_stage="s01", to_stage="s02", condition=EdgeCondition.SUCCESS)
        upstream = StageState(
            stage_id="s01", stage_instance_id="s01",
            status=StageStatus.DONE, exit_condition="loop_exceeded",
        )
        assert policy.is_upstream_satisfied(upstream, edge) is False

    def test_confirmed_empty_exit_compat(self):
        """CONFIRMED 边 + 空 exit_condition → True（兼容旧实例）。"""
        adj = _make_full_adj()
        policy = TransitionPolicy.from_adjacency(adj, "s01")
        edge = EdgeSpec(from_stage="s01", to_stage="s02", condition=EdgeCondition.CONFIRMED)
        upstream = StageState(
            stage_id="s01", stage_instance_id="s01",
            status=StageStatus.DONE, exit_condition="",
        )
        assert policy.is_upstream_satisfied(upstream, edge) is True

    def test_confirmed_wrong_exit_condition(self):
        """CONFIRMED 边 + exit_condition="success" → False。"""
        adj = _make_full_adj()
        policy = TransitionPolicy.from_adjacency(adj, "s01")
        edge = EdgeSpec(from_stage="s01", to_stage="s02", condition=EdgeCondition.CONFIRMED)
        upstream = StageState(
            stage_id="s01", stage_instance_id="s01",
            status=StageStatus.DONE, exit_condition="success",
        )
        assert policy.is_upstream_satisfied(upstream, edge) is False


class TestOnError:
    def test_retry_within_limit(self):
        """attempt_count < retry → TransitionResult(action="retry")。"""
        adj = _make_simple_adj()
        policy = TransitionPolicy.from_adjacency(adj, "s02")  # retry=2
        state = StageState(stage_id="s02", stage_instance_id="s02", status=StageStatus.ERROR, attempt_count=0)
        result = policy.on_error(state)
        assert result.action == "retry"
        assert result.next_status == StageStatus.PENDING
        assert result.updates["attempt_count"] == 1

    def test_retry_exhausted_with_failure_edge(self):
        """attempt_count >= retry，有 failure_edge → spawn。"""
        adj = _make_full_adj()
        policy = TransitionPolicy.from_adjacency(adj, "s01")  # retry=1
        state = StageState(stage_id="s01", stage_instance_id="s01", status=StageStatus.ERROR, attempt_count=1)
        result = policy.on_error(state)
        assert result.action == "spawn"
        assert result.target_stage_id == "s04"

    def test_retry_exhausted_no_recovery(self):
        """无 retry、无 failure_edge → terminate。"""
        adj = _make_simple_adj()
        policy = TransitionPolicy.from_adjacency(adj, "s01")  # retry=0, no failure edge
        state = StageState(stage_id="s01", stage_instance_id="s01", status=StageStatus.ERROR, attempt_count=0)
        result = policy.on_error(state)
        assert result.action == "terminate"
        assert result.next_status == StageStatus.ERROR

    def test_loop_exceeded_activates(self):
        """loop_counter >= loop_exceeded_edge.max_loop → LOOP_EXCEEDED 路径。"""
        adj = _make_full_adj()
        policy = TransitionPolicy.from_adjacency(adj, "s01")  # has loop_exceeded_edge max_loop=2
        state = StageState(
            stage_id="s01", stage_instance_id="s01",
            status=StageStatus.ERROR, attempt_count=1, loop_counter=2,
        )
        result = policy.on_error(state)
        # loop_counter 2 >= max_loop 2 → loop_exceeded path
        assert result.action == "spawn"
        assert result.target_stage_id == "s05"

    def test_loop_not_yet_exceeded(self):
        """loop_counter < loop_exceeded_edge.max_loop → 走 failure_edge。"""
        adj = _make_full_adj()
        policy = TransitionPolicy.from_adjacency(adj, "s01")
        state = StageState(
            stage_id="s01", stage_instance_id="s01",
            status=StageStatus.ERROR, attempt_count=1, loop_counter=1,
        )
        result = policy.on_error(state)
        # loop_counter 1 < max_loop 2 → failure_edge path
        assert result.action == "spawn"
        assert result.target_stage_id == "s04"


class TestValidChoices:
    def test_routing_choices(self):
        """SUCCESS 边带 choice → 收录。"""
        spec = WorkflowSpec(
            schema_version="3.0.0",
            workflow_id="choices",
            version="1.0.0",
            max_parallel_agents=4,
            stages=[
                StageSpec(stage_id="A", name="A", target_type=StageTargetType.SKILL, target="a"),
                StageSpec(stage_id="B", name="B", target_type=StageTargetType.SKILL, target="b"),
                StageSpec(stage_id="C", name="C", target_type=StageTargetType.SKILL, target="c"),
            ],
            edges=[
                EdgeSpec(from_stage="A", to_stage="B", condition=EdgeCondition.SUCCESS, choice="path-b"),
                EdgeSpec(from_stage="A", to_stage="C", condition=EdgeCondition.SUCCESS, choice="path-c"),
            ],
        )
        adj = build_adjacency(spec)
        policy = TransitionPolicy.from_adjacency(adj, "A")
        assert set(policy.valid_routing_choices()) == {"path-b", "path-c"}

    def test_routing_choices_empty(self):
        """无 choice 的 SUCCESS 边 → 空列表。"""
        adj = _make_simple_adj()
        policy = TransitionPolicy.from_adjacency(adj, "s01")
        assert policy.valid_routing_choices() == []

    def test_confirm_choices(self):
        """CONFIRMED 边带 choice → 收录。"""
        adj = _make_full_adj()
        policy = TransitionPolicy.from_adjacency(adj, "s01")
        assert policy.valid_confirm_choices() == ["通过"]

    def test_confirm_choices_empty(self):
        """无 CONFIRMED 边 → 空列表。"""
        adj = _make_simple_adj()
        policy = TransitionPolicy.from_adjacency(adj, "s01")
        assert policy.valid_confirm_choices() == []


# ── 辅助函数（确认/回退/跳过测试用）──

def _make_state(stage_statuses: dict[str, str]) -> InstanceState:
    """从 {stage_id: status_str} 构造 InstanceState。"""
    stages = [
        StageState(
            stage_id=sid,
            stage_instance_id=sid,
            status=StageStatus(status),
            system_agent_id=f"sys-{sid}" if status == "DONE" else None,
            output_message_id=f"msg-{sid}" if status == "DONE" else None,
        )
        for sid, status in stage_statuses.items()
    ]
    return InstanceState(
        instance_id="test-001",
        workflow_id="test",
        stages=stages,
    )


def _make_confirm_adj() -> AdjacencyList:
    """包含自环 relay 边的工作流，用于 on_confirm 测试。

    拓扑:
        s00 → s01 (ALWAYS)
        s01 → s01 (CONFIRMED, choice="retry", max_loop=2)   [relay]
        s01 → s02 (CONFIRMED, choice="done")                  [final forward]
        s01 → s03 (REJECTED, choice="abort")                  [rejected forward]
        s01 → s01 (REJECTED, choice="retry_reject", max_loop=2) [rejected self-loop]
        s01 → s05 (LOOP_EXCEEDED, max_loop=2)
        s02 → s99 (SUCCESS)
        s03 → s99 (SUCCESS)
        s05 → s99 (SUCCESS)
    """
    spec = WorkflowSpec(
        schema_version="3.0.0",
        workflow_id="confirm",
        version="1.0.0",
        max_parallel_agents=4,
        stages=[
            StageSpec(stage_id="s00", name="start", target_type=StageTargetType.VIRTUAL),
            StageSpec(stage_id="s01", name="confirmable", target_type=StageTargetType.SKILL, target="skill-a"),
            StageSpec(stage_id="s02", name="confirmed-path", target_type=StageTargetType.SKILL, target="skill-b"),
            StageSpec(stage_id="s03", name="rejected-path", target_type=StageTargetType.SKILL, target="skill-c"),
            StageSpec(stage_id="s05", name="loop-exceeded-recovery", target_type=StageTargetType.SKILL, target="skill-e"),
            StageSpec(stage_id="s99", name="end", target_type=StageTargetType.VIRTUAL),
        ],
        edges=[
            EdgeSpec(from_stage="s00", to_stage="s01", condition=EdgeCondition.ALWAYS),
            EdgeSpec(from_stage="s01", to_stage="s01", condition=EdgeCondition.CONFIRMED, choice="retry", max_loop=2),
            EdgeSpec(from_stage="s01", to_stage="s02", condition=EdgeCondition.CONFIRMED, choice="done"),
            EdgeSpec(from_stage="s01", to_stage="s03", condition=EdgeCondition.REJECTED, choice="abort"),
            EdgeSpec(from_stage="s01", to_stage="s01", condition=EdgeCondition.REJECTED, choice="retry_reject", max_loop=2),
            EdgeSpec(from_stage="s01", to_stage="s05", condition=EdgeCondition.LOOP_EXCEEDED, max_loop=2),
            EdgeSpec(from_stage="s02", to_stage="s99", condition=EdgeCondition.SUCCESS),
            EdgeSpec(from_stage="s03", to_stage="s99", condition=EdgeCondition.SUCCESS),
            EdgeSpec(from_stage="s05", to_stage="s99", condition=EdgeCondition.SUCCESS),
        ],
    )
    return build_adjacency(spec)


def _make_cascade_adj() -> AdjacencyList:
    """包含回边 confirm 的工作流，用于 back-edge cascade 测试。

    拓扑:
        s00 → s01 (ALWAYS)
        s01 → s02 (SUCCESS)
        s02 → s03 (SUCCESS)
        s02 → s00 (CONFIRMED, choice="restart")  [back-edge: s02→s00]
        s03 → s99 (SUCCESS)
    """
    spec = WorkflowSpec(
        schema_version="3.0.0",
        workflow_id="cascade",
        version="1.0.0",
        max_parallel_agents=4,
        stages=[
            StageSpec(stage_id="s00", name="start", target_type=StageTargetType.VIRTUAL),
            StageSpec(stage_id="s01", name="step1", target_type=StageTargetType.SKILL, target="skill-a"),
            StageSpec(stage_id="s02", name="step2", target_type=StageTargetType.SKILL, target="skill-b"),
            StageSpec(stage_id="s03", name="step3", target_type=StageTargetType.SKILL, target="skill-c"),
            StageSpec(stage_id="s99", name="end", target_type=StageTargetType.VIRTUAL),
        ],
        edges=[
            EdgeSpec(from_stage="s00", to_stage="s01", condition=EdgeCondition.ALWAYS),
            EdgeSpec(from_stage="s01", to_stage="s02", condition=EdgeCondition.SUCCESS),
            EdgeSpec(from_stage="s02", to_stage="s03", condition=EdgeCondition.SUCCESS),
            EdgeSpec(from_stage="s02", to_stage="s00", condition=EdgeCondition.CONFIRMED, choice="restart"),
            EdgeSpec(from_stage="s03", to_stage="s99", condition=EdgeCondition.SUCCESS),
        ],
    )
    return build_adjacency(spec)


def _stage_order_from_spec(spec: WorkflowSpec) -> list[str]:
    return [s.stage_id for s in spec.stages]


# ═══════════════════════════════════════════════════════════════════
# TestEdgeMatching
# ═══════════════════════════════════════════════════════════════════

class TestEdgeMatching:
    """match_confirmed_edge / match_rejected_edge / match_success_edge。"""

    def test_match_confirmed_edge_exact_match(self):
        """精确 choice 匹配 → 返回对应 confirmed 边。"""
        adj = _make_confirm_adj()
        policy = TransitionPolicy.from_adjacency(adj, "s01")
        edge = policy.match_confirmed_edge("done")
        assert edge is not None
        assert edge.to_stage == "s02"
        assert edge.choice == "done"

    def test_match_confirmed_edge_fallback_no_choice(self):
        """无精确匹配时返回无 choice 的兜底边 → None（该 adj 无无 choice 边）。"""
        adj = _make_full_adj()
        policy = TransitionPolicy.from_adjacency(adj, "s01")
        edge = policy.match_confirmed_edge("unknown_choice")
        # _make_full_adj 中 confirmed 边有 choice="通过"，无 choice 的兜底边不存在
        assert edge is None

    def test_match_confirmed_edge_no_match(self):
        """所有边都带 choice 且无匹配 → None。"""
        adj = _make_confirm_adj()
        policy = TransitionPolicy.from_adjacency(adj, "s01")
        edge = policy.match_confirmed_edge("nonexistent")
        assert edge is None

    def test_match_rejected_edge_exact_match(self):
        """精确 choice 匹配 → 返回对应 rejected 边。"""
        adj = _make_confirm_adj()
        policy = TransitionPolicy.from_adjacency(adj, "s01")
        edge = policy.match_rejected_edge("abort")
        assert edge is not None
        assert edge.to_stage == "s03"
        assert edge.choice == "abort"

    def test_match_rejected_edge_fallback(self):
        """无精确匹配 → 兜底无 choice 边。"""
        adj = _make_full_adj()
        policy = TransitionPolicy.from_adjacency(adj, "s01")
        # "放弃" 精确匹配 → 应返回 to_stage="s03" 的 rejected 边
        edge = policy.match_rejected_edge("放弃")
        assert edge is not None
        assert edge.to_stage == "s03"
        # 不存在的 choice → 无 choice 兜底边也不存在 → None
        edge2 = policy.match_rejected_edge("nonexistent")
        assert edge2 is None

    def test_match_success_edge_exact_match(self):
        """routing_choice 精确匹配 SUCCESS 边。"""
        spec = WorkflowSpec(
            schema_version="3.0.0", workflow_id="test", version="1.0.0",
            max_parallel_agents=1,
            stages=[
                StageSpec(stage_id="A", name="A", target_type=StageTargetType.SKILL, target="a"),
                StageSpec(stage_id="B", name="B", target_type=StageTargetType.SKILL, target="b"),
                StageSpec(stage_id="C", name="C", target_type=StageTargetType.SKILL, target="c"),
            ],
            edges=[
                EdgeSpec(from_stage="A", to_stage="B", condition=EdgeCondition.SUCCESS, choice="go-b"),
                EdgeSpec(from_stage="A", to_stage="C", condition=EdgeCondition.SUCCESS, choice="go-c"),
            ],
        )
        adj = build_adjacency(spec)
        policy = TransitionPolicy.from_adjacency(adj, "A")
        edge = policy.match_success_edge("go-c")
        assert edge is not None
        assert edge.to_stage == "C"

    def test_match_success_edge_fallback(self):
        """无匹配 → 兜底无 choice 的 SUCCESS 边。"""
        adj = _make_full_adj()
        policy = TransitionPolicy.from_adjacency(adj, "s01")
        edge = policy.match_success_edge(None)
        # _make_full_adj 中 s01→s99 的 SUCCESS 边无 choice，应作为兜底
        assert edge is not None
        assert edge.to_stage == "s99"
        assert not edge.choice


# ═══════════════════════════════════════════════════════════════════
# TestValidation
# ═══════════════════════════════════════════════════════════════════

class TestValidation:
    """validate_routing_choice。"""

    def test_validate_routing_choice_valid(self):
        """合法 routing_choice → (True, '')。"""
        spec = WorkflowSpec(
            schema_version="3.0.0", workflow_id="test", version="1.0.0",
            max_parallel_agents=1,
            stages=[
                StageSpec(stage_id="A", name="A", target_type=StageTargetType.SKILL, target="a"),
                StageSpec(stage_id="B", name="B", target_type=StageTargetType.SKILL, target="b"),
            ],
            edges=[
                EdgeSpec(from_stage="A", to_stage="B", condition=EdgeCondition.SUCCESS, choice="go-b"),
            ],
        )
        adj = build_adjacency(spec)
        policy = TransitionPolicy.from_adjacency(adj, "A")
        is_valid, reason = policy.validate_routing_choice("go-b")
        assert is_valid is True
        assert reason == ""

    def test_validate_routing_choice_invalid(self):
        """非法 routing_choice → (False, reason)。"""
        spec = WorkflowSpec(
            schema_version="3.0.0", workflow_id="test", version="1.0.0",
            max_parallel_agents=1,
            stages=[
                StageSpec(stage_id="A", name="A", target_type=StageTargetType.SKILL, target="a"),
                StageSpec(stage_id="B", name="B", target_type=StageTargetType.SKILL, target="b"),
            ],
            edges=[
                EdgeSpec(from_stage="A", to_stage="B", condition=EdgeCondition.SUCCESS, choice="go-b"),
            ],
        )
        adj = build_adjacency(spec)
        policy = TransitionPolicy.from_adjacency(adj, "A")
        is_valid, reason = policy.validate_routing_choice("invalid")
        assert is_valid is False
        assert "go-b" in reason

    def test_validate_routing_choice_empty_ok(self):
        """空 routing_choice 且存在有效选项 → (True, '')。"""
        adj = _make_simple_adj()
        policy = TransitionPolicy.from_adjacency(adj, "s01")
        # s01 只有一条无 choice 的 SUCCESS 边，valid_routing_choices 为空
        is_valid, reason = policy.validate_routing_choice("")
        assert is_valid is True

    def test_validate_routing_choice_any_ok_when_no_valid(self):
        """无有效 routing_choices 时，任意值均合法 → (True, '')。"""
        adj = _make_simple_adj()
        policy = TransitionPolicy.from_adjacency(adj, "s01")
        is_valid, reason = policy.validate_routing_choice("anything")
        assert is_valid is True
        assert reason == ""


# ═══════════════════════════════════════════════════════════════════
# TestOnConfirm
# ═══════════════════════════════════════════════════════════════════

class TestOnConfirm:
    """TransitionPolicy.on_confirm 决策树。"""

    def test_confirm_matched_confirmed_edge(self):
        """confirmed 边匹配 → DONE + target_stage_id。"""
        adj = _make_confirm_adj()
        policy = TransitionPolicy.from_adjacency(adj, "s01")
        stage = StageState(
            stage_id="s01", stage_instance_id="s01",
            status=StageStatus.AWAITING_CONFIRM,
        )
        result = policy.on_confirm(stage, "done")
        assert result.next_status == StageStatus.DONE
        assert result.exit_condition == "confirmed"
        assert result.target_stage_id == "s02"
        assert result.action == "spawn"
        assert result.instance_failed is False

    def test_confirm_relay_self_loop(self):
        """自环 relay → PENDING + loop_counter++ + is_relay=True。"""
        adj = _make_confirm_adj()
        policy = TransitionPolicy.from_adjacency(adj, "s01")
        stage = StageState(
            stage_id="s01", stage_instance_id="s01",
            status=StageStatus.AWAITING_CONFIRM, loop_counter=0,
        )
        result = policy.on_confirm(stage, "retry")
        assert result.next_status == StageStatus.PENDING
        assert result.is_relay is True
        assert result.action == "retry"
        assert result.updates["loop_counter"] == 1

    def test_confirm_relay_loop_exceeded(self):
        """relay 超限 (loop_counter >= max_loop) → loop_exceeded。"""
        adj = _make_confirm_adj()
        policy = TransitionPolicy.from_adjacency(adj, "s01")
        stage = StageState(
            stage_id="s01", stage_instance_id="s01",
            status=StageStatus.AWAITING_CONFIRM, loop_counter=2,
        )
        result = policy.on_confirm(stage, "retry")
        assert result.exit_condition == "loop_exceeded"
        assert result.target_stage_id == "s05"
        assert result.action == "spawn"

    def test_confirm_relay_loop_exceeded_no_recovery(self):
        """relay 超限且无 loop_exceeded_edge → instance_failed。"""
        spec = WorkflowSpec(
            schema_version="3.0.0", workflow_id="no_recovery", version="1.0.0",
            max_parallel_agents=1,
            stages=[
                StageSpec(stage_id="s01", name="loop", target_type=StageTargetType.SKILL, target="a"),
            ],
            edges=[
                EdgeSpec(from_stage="s01", to_stage="s01", condition=EdgeCondition.CONFIRMED, choice="retry", max_loop=1),
            ],
        )
        adj = build_adjacency(spec)
        policy = TransitionPolicy.from_adjacency(adj, "s01")
        stage = StageState(
            stage_id="s01", stage_instance_id="s01",
            status=StageStatus.AWAITING_CONFIRM, loop_counter=1,
        )
        result = policy.on_confirm(stage, "retry")
        assert result.instance_failed is True
        assert result.next_status == StageStatus.ERROR

    def test_confirm_final_with_back_edge_cascade(self):
        """回边确认 → cascade_reset_target 设置。"""
        adj = _make_cascade_adj()
        policy = TransitionPolicy.from_adjacency(adj, "s02")
        stage_order = ["s00", "s01", "s02", "s03", "s99"]
        stage = StageState(
            stage_id="s02", stage_instance_id="s02",
            status=StageStatus.AWAITING_CONFIRM,
        )
        result = policy.on_confirm(stage, "restart", stage_order=stage_order)
        assert result.next_status == StageStatus.DONE
        assert result.cascade_reset_target == "s00"

    def test_confirm_final_with_confirmation_point(self):
        """confirmation_point=True → PENDING + action="continue"。"""
        spec = WorkflowSpec(
            schema_version="3.0.0", workflow_id="cp", version="1.0.0",
            max_parallel_agents=1,
            stages=[
                StageSpec(stage_id="s01", name="cp", target_type=StageTargetType.SKILL, target="a", confirmation_point=True),
                StageSpec(stage_id="s02", name="next", target_type=StageTargetType.SKILL, target="b"),
            ],
            edges=[
                EdgeSpec(from_stage="s01", to_stage="s02", condition=EdgeCondition.CONFIRMED, choice="ok"),
            ],
        )
        adj = build_adjacency(spec)
        policy = TransitionPolicy.from_adjacency(adj, "s01")
        stage = StageState(
            stage_id="s01", stage_instance_id="s01",
            status=StageStatus.AWAITING_CONFIRM,
        )
        result = policy.on_confirm(stage, "ok")
        assert result.next_status == StageStatus.PENDING
        assert result.action == "continue"

    def test_confirm_matched_rejected_edge(self):
        """rejected 边匹配 → DONE + rejected + target_stage_id。"""
        adj = _make_confirm_adj()
        policy = TransitionPolicy.from_adjacency(adj, "s01")
        stage = StageState(
            stage_id="s01", stage_instance_id="s01",
            status=StageStatus.AWAITING_CONFIRM,
        )
        result = policy.on_confirm(stage, "abort")
        assert result.next_status == StageStatus.DONE
        assert result.exit_condition == "rejected"
        assert result.is_rejected is True
        assert result.target_stage_id == "s03"

    def test_confirm_rejected_self_loop(self):
        """拒绝自环（非 relay, to != from）→ DONE + rejected。"""
        adj = _make_full_adj()
        policy = TransitionPolicy.from_adjacency(adj, "s01")
        stage = StageState(
            stage_id="s01", stage_instance_id="s01",
            status=StageStatus.AWAITING_CONFIRM,
        )
        result = policy.on_confirm(stage, "放弃")
        assert result.next_status == StageStatus.DONE
        assert result.exit_condition == "rejected"
        assert result.is_rejected is True

    def test_confirm_rejected_self_loop_loop_exceeded(self):
        """拒绝自环超限 → loop_exceeded edge。"""
        adj = _make_confirm_adj()
        policy = TransitionPolicy.from_adjacency(adj, "s01")
        stage = StageState(
            stage_id="s01", stage_instance_id="s01",
            status=StageStatus.AWAITING_CONFIRM, loop_counter=2,
        )
        result = policy.on_confirm(stage, "retry_reject")
        assert result.exit_condition == "loop_exceeded"
        assert result.target_stage_id == "s05"

    def test_confirm_no_match(self):
        """无匹配边 → instance_failed + terminate。"""
        adj = _make_confirm_adj()
        policy = TransitionPolicy.from_adjacency(adj, "s01")
        stage = StageState(
            stage_id="s01", stage_instance_id="s01",
            status=StageStatus.AWAITING_CONFIRM,
        )
        result = policy.on_confirm(stage, "nonexistent")
        assert result.instance_failed is True
        assert result.next_status == StageStatus.ERROR
        assert result.action == "terminate"
        assert "合法选项" in result.reason

    def test_confirm_no_match_no_choice_edges(self):
        """无边可选 → reason 提示无 choice 边。"""
        adj = _make_simple_adj()
        policy = TransitionPolicy.from_adjacency(adj, "s01")
        stage = StageState(
            stage_id="s01", stage_instance_id="s01",
            status=StageStatus.AWAITING_CONFIRM,
        )
        result = policy.on_confirm(stage, "something")
        assert result.instance_failed is True
        assert "未定义任何带 choice 的边" in result.reason

    def test_confirm_with_feedback_flag(self):
        """confirmation_point + has_feedback=True → requires_feedback=True。"""
        spec = WorkflowSpec(
            schema_version="3.0.0", workflow_id="fb", version="1.0.0",
            max_parallel_agents=1,
            stages=[
                StageSpec(stage_id="s01", name="cp", target_type=StageTargetType.SKILL, target="a", confirmation_point=True),
                StageSpec(stage_id="s02", name="next", target_type=StageTargetType.SKILL, target="b"),
            ],
            edges=[
                EdgeSpec(from_stage="s01", to_stage="s02", condition=EdgeCondition.CONFIRMED, choice="ok"),
            ],
        )
        adj = build_adjacency(spec)
        policy = TransitionPolicy.from_adjacency(adj, "s01")
        stage = StageState(
            stage_id="s01", stage_instance_id="s01",
            status=StageStatus.AWAITING_CONFIRM,
        )
        result = policy.on_confirm(stage, "ok", has_feedback=True)
        assert result.requires_feedback is True


# ═══════════════════════════════════════════════════════════════════
# TestRollbackAndSkip
# ═══════════════════════════════════════════════════════════════════

class TestRollbackAndSkip:
    """on_rollback / on_skip 决策。"""

    def test_on_rollback_collects_downstream(self):
        """回退收集下游 DONE stage。"""
        adj = _make_simple_adj()
        policy = TransitionPolicy.from_adjacency(adj, "s01")
        state = _make_state({"s00": "DONE", "s01": "DONE", "s02": "DONE", "s99": "PENDING"})
        result = policy.on_rollback(state, adj)
        assert "s01" in result.reset_stage_ids
        assert "s02" in result.reset_stage_ids
        assert "s00" not in result.reset_stage_ids  # upstream, not affected

    def test_on_rollback_delta_resets_fields(self):
        """StateDelta 重置字段为零值。"""
        adj = _make_simple_adj()
        policy = TransitionPolicy.from_adjacency(adj, "s02")
        state = _make_state({"s01": "DONE", "s02": "DONE", "s99": "PENDING"})
        result = policy.on_rollback(state, adj)
        delta = result.state_delta
        s02_updates = delta.stage_updates.get("s02", {})
        assert s02_updates.get("status") == StageStatus.PENDING
        assert s02_updates.get("attempt_count") == 0
        assert s02_updates.get("loop_counter") == 0

    def test_on_rollback_cleans_consumed_messages(self):
        """重置 stage 的 output_message_id 从 consumed 中移除。"""
        adj = _make_simple_adj()
        policy = TransitionPolicy.from_adjacency(adj, "s02")
        state = InstanceState(
            instance_id="test-001",
            workflow_id="test",
            consumed_message_ids=frozenset(["msg-s02", "msg-other"]),
            stages=[
                StageState(
                    stage_id="s01", stage_instance_id="s01",
                    status=StageStatus.DONE, output_message_id="msg-s01",
                ),
                StageState(
                    stage_id="s02", stage_instance_id="s02",
                    status=StageStatus.DONE, output_message_id="msg-s02",
                ),
            ],
        )
        result = policy.on_rollback(state, adj)
        delta = result.state_delta
        consumed = delta.instance_updates.get("consumed_message_ids", frozenset())
        assert "msg-s02" not in consumed  # s02 被重置，其产出消息移出
        assert "msg-other" in consumed    # 其他消息保留

    def test_on_skip_all_pending(self):
        """所有实例 PENDING → force=False 成功。"""
        adj = _make_simple_adj()
        policy = TransitionPolicy.from_adjacency(adj, "s01")
        state = _make_state({"s01": "PENDING"})
        result = policy.on_skip(state, force=False)
        assert len(result.stage_instance_ids) == 1
        assert result.force_applied is False
        assert result.state_delta.stage_updates["s01"]["status"] == StageStatus.DONE

    def test_on_skip_non_pending_without_force_raises(self):
        """非 PENDING 实例 + force=False → StateError。"""
        from infrastructure.errors import StateError
        adj = _make_simple_adj()
        policy = TransitionPolicy.from_adjacency(adj, "s01")
        state = _make_state({"s01": "RUNNING"})
        with pytest.raises(StateError, match="not PENDING"):
            policy.on_skip(state, force=False)

    def test_on_skip_non_pending_with_force(self):
        """非 PENDING 实例 + force=True → 成功。"""
        adj = _make_simple_adj()
        policy = TransitionPolicy.from_adjacency(adj, "s01")
        state = _make_state({"s01": "RUNNING"})
        result = policy.on_skip(state, force=True)
        assert result.force_applied is True
        assert result.state_delta.stage_updates["s01"]["status"] == StageStatus.DONE


# ═══════════════════════════════════════════════════════════════════
# TestStaticUtils
# ═══════════════════════════════════════════════════════════════════

class TestStaticUtils:
    """on_pause / on_resume / build_merge_stage / on_merge_confirm。"""

    def test_on_pause_resets_running_to_pending(self):
        """RUNNING stage → PENDING，实例 → PAUSED。"""
        state = _make_state({"s01": "RUNNING", "s02": "DONE"})
        delta = TransitionPolicy.on_pause(state)
        assert delta.stage_updates["s01"]["status"] == StageStatus.PENDING
        assert "s02" not in delta.stage_updates
        assert delta.instance_updates["status"] == InstanceStatus.PAUSED

    def test_on_resume_sets_active(self):
        """实例状态 → ACTIVE。"""
        state = _make_state({"s01": "PENDING"})
        delta = TransitionPolicy.on_resume(state)
        assert delta.instance_updates["status"] == InstanceStatus.ACTIVE

    def test_build_merge_stage(self):
        """build_merge_stage 构造正确的伪 stage。"""
        stage = TransitionPolicy.build_merge_stage("inst-1", "merge all changes")
        assert stage.stage_id == "__merge__"
        assert stage.stage_instance_id == "inst-1__merge__"
        assert stage.status == StageStatus.AWAITING_CONFIRM
        assert stage.confirmation_point is False

    def test_on_merge_confirm_positive(self):
        """positive 选择 → merge_confirmed=True。"""
        result = TransitionPolicy.on_merge_confirm("yes")
        assert result.merge_confirmed is True
        assert result.remove_merge_stage is True

    def test_on_merge_confirm_negative(self):
        """negative 选择 → merge_confirmed=False。"""
        result = TransitionPolicy.on_merge_confirm("no")
        assert result.merge_confirmed is False
        assert result.remove_merge_stage is True

    def test_is_terminal_stage_true(self):
        """VIRTUAL + workflow-end → 终态。"""
        spec_stages = [
            StageSpec(stage_id="s99-workflow-end", name="结束", target_type=StageTargetType.VIRTUAL),
        ]
        assert TransitionPolicy._is_terminal_stage("s99-workflow-end", spec_stages) is True

    def test_is_terminal_stage_false(self):
        """普通 SKILL stage → 非终态。"""
        spec_stages = [
            StageSpec(stage_id="s01", name="step1", target_type=StageTargetType.SKILL, target="a"),
        ]
        assert TransitionPolicy._is_terminal_stage("s01", spec_stages) is False
