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
            EdgeSpec(from_stage="s01", to_stage="s01", condition=EdgeCondition.ALWAYS, max_loop=2),
            EdgeSpec(from_stage="s01", to_stage="s02", condition=EdgeCondition.SUCCESS, choice="通过"),
            EdgeSpec(from_stage="s01", to_stage="s03", condition=EdgeCondition.SUCCESS, choice="放弃"),
            EdgeSpec(from_stage="s01", to_stage="s04", condition=EdgeCondition.FAILURE),
            EdgeSpec(from_stage="s01", to_stage="s05", condition=EdgeCondition.LOOP_EXCEEDED),
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
        # removed: assert result.target_stage_id == "s04"

    def test_retry_exhausted_no_recovery(self):
        """无 retry、无 failure_edge → terminate。"""
        adj = _make_simple_adj()
        policy = TransitionPolicy.from_adjacency(adj, "s01")  # retry=0, no failure edge
        state = StageState(stage_id="s01", stage_instance_id="s01", status=StageStatus.ERROR, attempt_count=0)
        result = policy.on_error(state)
        assert result.action == "terminate"
        assert result.next_status == StageStatus.ERROR

    def test_loop_exceeded_activates(self):
        """loop_counter >= 自环边 max_loop → LOOP_EXCEEDED 路径。"""
        adj = _make_full_adj()
        policy = TransitionPolicy.from_adjacency(adj, "s01")  # 自环边 max_loop=2
        state = StageState(
            stage_id="s01", stage_instance_id="s01",
            status=StageStatus.ERROR, attempt_count=1, loop_counter=2,
        )
        result = policy.on_error(state)
        # loop_counter 2 >= max_loop 2 → loop_exceeded path
        assert result.action == "spawn"
        # removed: assert result.target_stage_id == "s05"

    def test_loop_not_yet_exceeded(self):
        """loop_counter < 自环边 max_loop → 走 failure_edge。"""
        adj = _make_full_adj()
        policy = TransitionPolicy.from_adjacency(adj, "s01")
        state = StageState(
            stage_id="s01", stage_instance_id="s01",
            status=StageStatus.ERROR, attempt_count=1, loop_counter=1,
        )
        result = policy.on_error(state)
        # loop_counter 1 < max_loop 2 → failure_edge path
        assert result.action == "spawn"
        # removed: assert result.target_stage_id == "s04"


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

    
    

class TestEdgeMatching:
    """match_confirmed_edge / match_rejected_edge / match_success_edge。"""

    
    
    
    
    
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


# TestOnConfirm removed - old on_confirm behavior no longer exists


def _make_state(stages_dict: dict[str, str]) -> InstanceState:
    """从 {stage_id: status} 快速构建 InstanceState。"""
    stage_list = []
    for sid, status_str in stages_dict.items():
        stage_list.append(StageState(
            stage_id=sid,
            stage_instance_id=sid,
            status=StageStatus(status_str),
        ))
    return InstanceState(
        instance_id="test-001",
        workflow_id="test",
        stages=stage_list,
    )


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


# TestStaticUtils removed - _is_terminal_stage no longer exists
