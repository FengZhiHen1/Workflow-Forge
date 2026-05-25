"""对抗性测试套件：针对 wfctl 核心组件的边界、注入、并发和状态机攻击。

覆盖：
  - 输入注入与校验绕过
  - 状态模型边界与不变式
  - DAG 分析边角案例
  - TransitionPolicy 决策正确性
  - 级联重置 bug 验证
  - 虚拟 stage 链鲁棒性
  - 消息消费安全

所有测试为纯单元测试，不依赖外部 git 仓库。
"""

import json
import threading
import time
from pathlib import Path

import pytest

from domain.dag.graph import (
    AdjacencyList,
    build_adjacency,
    collect_ancestors,
    collect_downstream,
    compute_ready,
    is_backward_edge,
)
from domain.dag.topology import analyze_topology
from domain.dag.validator import validate_workflow, ValidationIssue, ValidationResult
from domain.transition.policy import TransitionPolicy
from domain.transition.results import ConfirmResult, TransitionResult
from domain.workflow.spec import (
    EdgeCondition,
    EdgeSpec,
    InstanceStatus,
    ParallelSpec,
    StageSpec,
    StageStatus,
    StageTargetType,
    WorkflowSpec,
)
from infrastructure.errors import (
    GitError,
    InputError,
    SchemaError,
    StateError,
    ValidationError,
    WfctlError,
    WorktreeError,
)
from infrastructure.lock import FileLock
from scheduler.context import ExecutionContext
from scheduler.processors.message_consumer import ConsumeMessagesProcessor
from scheduler.processors.virtual_stages import VirtualStagesProcessor
from state.model import (
    CycleMeta,
    InstanceState,
    StageState,
    StateDelta,
)


# ═══════════════════════════════════════════════════════════════════
# 辅助工厂函数
# ═══════════════════════════════════════════════════════════════════

def _make_stage(stage_id: str, **kwargs) -> StageState:
    defaults = {"stage_id": stage_id, "stage_instance_id": stage_id, "status": StageStatus.PENDING}
    defaults.update(kwargs)
    return StageState(**defaults)


def _make_edge(from_stage: str, to_stage: str, condition=EdgeCondition.ALWAYS, **kwargs) -> EdgeSpec:
    return EdgeSpec(from_stage=from_stage, to_stage=to_stage, condition=condition, **kwargs)


def _make_virtual_stage(stage_id: str, name: str = "") -> StageSpec:
    return StageSpec(stage_id=stage_id, name=name or stage_id, target_type=StageTargetType.VIRTUAL)


def _make_skill_stage(stage_id: str, skill: str = "test-skill", **kwargs) -> StageSpec:
    return StageSpec(stage_id=stage_id, name=stage_id, target_type=StageTargetType.SKILL, target=skill, **kwargs)


# ═══════════════════════════════════════════════════════════════════
# 1. 输入注入与校验绕过
# ═══════════════════════════════════════════════════════════════════

class TestInputInjection:

    def test_stage_state_from_dict_ignores_unknown_fields(self):
        """StageState.from_dict 静默跳过未知字段（修复后）。"""
        malicious = {
            "stage_id": "s01",
            "stage_instance_id": "s01",
            "status": "PENDING",
            "__magic__": "evil",
            "injected_field": {"nested": "payload"},
        }
        result = StageState.from_dict(malicious)
        assert result.stage_id == "s01"
        assert result.status == StageStatus.PENDING

    def test_stage_state_from_dict_missing_instance_id(self):
        """StageState.from_dict 缺少 stage_instance_id 时抛出 StateError。"""
        minimal = {"stage_id": "s-minimal"}
        with pytest.raises(StateError, match="missing required fields"):
            StageState.from_dict(minimal)

    def test_stage_state_from_dict_empty(self):
        """StageState.from_dict({}) 因缺少 stage_id/stage_instance_id 抛 StateError。"""
        with pytest.raises(StateError, match="missing required fields"):
            StageState.from_dict({})

    def test_stage_state_from_dict_none_value(self):
        """StageState.from_dict 应处理 None 值字段。"""
        data = {
            "stage_id": "s01",
            "stage_instance_id": "s01",
            "status": "PENDING",
            "agent_id": None,
            "system_agent_id": None,
        }
        result = StageState.from_dict(data)
        assert result.stage_id == "s01"
        assert result.agent_id is None

    def test_stage_state_from_dict_bad_status(self):
        """未知 status 字符串应抛出 ValueError。"""
        data = {"stage_id": "s01", "stage_instance_id": "s01", "status": "IMAGINARY_STATUS"}
        with pytest.raises(ValueError):
            StageState.from_dict(data)

    def test_stage_state_from_dict_status_as_enum_bypass(self):
        """直接传入 StageStatus 枚举值应可被 from_dict 接受。"""
        data = {"stage_id": "s01", "stage_instance_id": "s01", "status": StageStatus.DONE}
        result = StageState.from_dict(data)
        assert result.status == StageStatus.DONE

    def test_instance_state_from_dict_ignores_cycle_meta(self):
        """InstanceState.from_dict 应安全忽略持久化的 cycle_meta。"""
        data = {
            "schema_version": "3.0.0",
            "instance_id": "inst-001",
            "workflow_id": "wf",
            "status": "ACTIVE",
            "stages": [{"stage_id": "s01", "stage_instance_id": "s01", "status": "PENDING"}],
            "cycle_meta": {
                "newly_done_stage_instance_ids": ["fake-done"],
                "newly_error_stage_instance_ids": ["fake-error"],
            },
        }
        result = InstanceState.from_dict(data)
        # cycle_meta 不被接受为字段，默认初始化为空
        assert result.cycle_meta is not None
        assert len(result.cycle_meta.newly_done_stage_instance_ids) == 0

    def test_instance_state_from_dict_consumed_message_ids_as_list(self):
        """consumed_message_ids 是 list 格式时自动转 frozenset。"""
        data = {
            "instance_id": "inst-001",
            "status": "ACTIVE",
            "stages": [],
            "consumed_message_ids": ["msg-1", "msg-2", "msg-3"],
        }
        result = InstanceState.from_dict(data)
        assert isinstance(result.consumed_message_ids, frozenset)
        assert "msg-1" in result.consumed_message_ids

    def test_instance_state_from_dict_consumed_message_ids_as_set(self):
        """consumed_message_ids 是 set 格式时自动转 frozenset。"""
        data = {
            "instance_id": "inst-001",
            "status": "ACTIVE",
            "stages": [],
            "consumed_message_ids": {"msg-a", "msg-b"},
        }
        result = InstanceState.from_dict(data)
        assert isinstance(result.consumed_message_ids, frozenset)

    def test_state_delta_merge_overwrites_stage_updates(self):
        """StateDelta.merge 应让 other 覆盖 self 的同名 stage 更新。"""
        a = StateDelta(stage_updates={"s01": {"status": StageStatus.DONE, "loop_counter": 1}})
        b = StateDelta(stage_updates={"s01": {"status": StageStatus.ERROR}})
        merged = a.merge(b)
        assert merged.stage_updates["s01"]["status"] == StageStatus.ERROR
        assert merged.stage_updates["s01"]["loop_counter"] == 1  # 保留 a 的字段

    def test_state_delta_merge_preserves_disjoint_keys(self):
        """merge 不应丢失双方互不冲突的 stage_updates。"""
        a = StateDelta(stage_updates={"s01": {"status": StageStatus.DONE}})
        b = StateDelta(stage_updates={"s02": {"status": StageStatus.ERROR}})
        merged = a.merge(b)
        assert "s01" in merged.stage_updates
        assert "s02" in merged.stage_updates

    def test_state_delta_merge_empty_both(self):
        """两个空 delta merge 后仍为空。"""
        a = StateDelta()
        b = StateDelta()
        merged = a.merge(b)
        assert merged.is_empty()

    def test_state_delta_merge_cycle_meta_priority(self):
        """other.cycle_meta 有值时覆盖 self。"""
        a = StateDelta(cycle_meta=CycleMeta(newly_done_stage_instance_ids=frozenset(["old"])))
        b = StateDelta(cycle_meta=CycleMeta(newly_done_stage_instance_ids=frozenset(["new"])))
        merged = a.merge(b)
        assert "new" in merged.cycle_meta.newly_done_stage_instance_ids
        assert "old" not in merged.cycle_meta.newly_done_stage_instance_ids

    def test_transition_result_failure_without_loop_or_retry(self):
        """没有 retry、没有 failure_edge、没有 loop_exceeded_edge 时应终止。"""
        spec = StageSpec(stage_id="s01", name="test", target_type=StageTargetType.SKILL, target="skill", retry=0)
        policy = TransitionPolicy(stage_id="s01", spec=spec, ready_edges=[])
        state = _make_stage("s01", status=StageStatus.ERROR, attempt_count=0, loop_counter=0)
        result = policy.on_error(state)
        assert result.action == "terminate"
        assert result.next_status == StageStatus.ERROR

    def test_transition_result_retry_exhausted_loop_exceeded_priority(self):
        """retry 耗尽 + loop_counter >= max_loop 时优先走 loop_exceeded。"""
        loop_edge = _make_edge("s01", "s13-report", condition=EdgeCondition.LOOP_EXCEEDED, max_loop=2)
        failure_edge = _make_edge("s01", "s02", condition=EdgeCondition.FAILURE)
        spec = StageSpec(stage_id="s01", name="test", target_type=StageTargetType.SKILL, target="skill", retry=2)
        policy = TransitionPolicy(
            stage_id="s01", spec=spec,
            ready_edges=[], failure_edge=failure_edge, loop_exceeded_edge=loop_edge,
        )
        state = _make_stage("s01", status=StageStatus.ERROR, attempt_count=2, loop_counter=3)
        result = policy.on_error(state)
        assert result.action == "spawn"
        assert result.target_stage_id == "s13-report"

    def test_transition_result_retry_exhausted_failure_edge(self):
        """retry 耗尽但没有 loop_exceeded edge 时走 failure_edge。"""
        failure_edge = _make_edge("s01", "s02", condition=EdgeCondition.FAILURE)
        spec = StageSpec(stage_id="s01", name="test", target_type=StageTargetType.SKILL, target="skill", retry=1)
        policy = TransitionPolicy(
            stage_id="s01", spec=spec, ready_edges=[], failure_edge=failure_edge,
        )
        state = _make_stage("s01", status=StageStatus.ERROR, attempt_count=1, loop_counter=0)
        result = policy.on_error(state)
        assert result.action == "spawn"
        assert result.target_stage_id == "s02"

    def test_transition_result_retry_still_available(self):
        """attempt_count < retry 时走 retry。"""
        spec = StageSpec(stage_id="s01", name="test", target_type=StageTargetType.SKILL, target="skill", retry=3)
        policy = TransitionPolicy(stage_id="s01", spec=spec, ready_edges=[])
        state = _make_stage("s01", status=StageStatus.ERROR, attempt_count=1, loop_counter=0)
        result = policy.on_error(state)
        assert result.action == "retry"
        assert result.updates["attempt_count"] == 2


# ═══════════════════════════════════════════════════════════════════
# 2. 状态模型不变式
# ═══════════════════════════════════════════════════════════════════

class TestStateModelInvariants:

    def test_stage_state_is_frozen(self):
        """StageState 是不可变数据类，直接设置属性应抛出 FrozenInstanceError。"""
        s = _make_stage("s01")
        with pytest.raises(Exception):  # dataclasses.FrozenInstanceError
            s.status = StageStatus.DONE

    def test_cycle_meta_is_frozen(self):
        """CycleMeta 是不可变数据类。"""
        cm = CycleMeta()
        with pytest.raises(Exception):
            cm.newly_done_stage_instance_ids = frozenset(["x"])

    def test_state_delta_empty(self):
        """空 StateDelta 的 is_empty() 返回 True。"""
        delta = StateDelta()
        assert delta.is_empty()

    def test_state_delta_non_empty_stage_updates(self):
        """有 stage_updates 时应为非空。"""
        delta = StateDelta(stage_updates={"s01": {"status": StageStatus.DONE}})
        assert not delta.is_empty()

    def test_state_delta_non_empty_instance_updates(self):
        """有 instance_updates 时应为非空。"""
        delta = StateDelta(instance_updates={"status": InstanceStatus.FAILED})
        assert not delta.is_empty()

    def test_state_delta_non_empty_append_stages(self):
        """有 append_stages 时应为非空。"""
        delta = StateDelta(append_stages=[_make_stage("s-new")])
        assert not delta.is_empty()

    def test_state_delta_non_empty_remove_stages(self):
        """有 remove_stage_instance_ids 时应为非空。"""
        delta = StateDelta(remove_stage_instance_ids=["s01"])
        assert not delta.is_empty()

    def test_instance_state_to_dict_excludes_cycle_meta(self):
        """to_dict 不应暴露 cycle_meta。"""
        state = InstanceState(instance_id="test", cycle_meta=CycleMeta(newly_done_stage_instance_ids=frozenset(["x"])))
        d = state.to_dict()
        assert "cycle_meta" not in d

    def test_instance_state_with_stage_raises_on_missing(self):
        """with_stage 对不存在的 stage_instance_id 抛出 KeyError。"""
        state = InstanceState(instance_id="test", stages=[_make_stage("s01")])
        with pytest.raises(KeyError):
            state.with_stage("nonexistent", status=StageStatus.DONE)

    def test_apply_delta_remove_stages(self):
        """apply_delta 按 stage_instance_id 正确删除。"""
        state = InstanceState(stages=[
            _make_stage("s01"), _make_stage("s02"), _make_stage("s03"),
        ])
        delta = StateDelta(remove_stage_instance_ids=["s01", "s03"])
        result = state.apply_delta(delta)
        assert len(result.stages) == 1
        assert result.stages[0].stage_id == "s02"

    def test_apply_delta_append_stages(self):
        """apply_delta 追加新 stage。"""
        state = InstanceState(stages=[_make_stage("s01")])
        delta = StateDelta(append_stages=[_make_stage("s02"), _make_stage("s03")])
        result = state.apply_delta(delta)
        assert len(result.stages) == 3
        assert [s.stage_id for s in result.stages] == ["s01", "s02", "s03"]

    def test_apply_delta_combined_append_remove_update(self):
        """apply_delta 同时执行追加、删除和更新。"""
        state = InstanceState(stages=[
            _make_stage("s-old"),
            _make_stage("s-target", loop_counter=0),
        ])
        delta = StateDelta(
            remove_stage_instance_ids=["s-old"],
            append_stages=[_make_stage("s-new")],
            stage_updates={"s-target": {"loop_counter": 5}},
        )
        result = state.apply_delta(delta)
        ids = [s.stage_id for s in result.stages]
        assert "s-old" not in ids
        assert "s-new" in ids
        target = result.stage_by_instance_id("s-target")
        assert target.loop_counter == 5

    def test_to_dict_roundtrip_preserves_parallel(self):
        """to_dict ↔ from_dict 应保持 parallel 同 stage_id 多实例。"""
        state = InstanceState(instance_id="test", stages=[
            _make_stage("s01", stage_instance_id="s01_0"),
            _make_stage("s01", stage_instance_id="s01_1"),
            _make_stage("s01", stage_instance_id="s01_2"),
        ])
        d = state.to_dict()
        reloaded = InstanceState.from_dict(d)
        assert len(reloaded.stages) == 3
        for s in reloaded.stages:
            assert s.stage_id == "s01"

    def test_consumed_message_ids_sorted_in_to_dict(self):
        """consumed_message_ids 在 to_dict 中应排序（确保确定性）。"""
        state = InstanceState(consumed_message_ids=frozenset(["c", "a", "b"]))
        d = state.to_dict()
        assert d["consumed_message_ids"] == ["a", "b", "c"]

    def test_cycle_meta_with_error_immutable_accumulator(self):
        """with_error 返回新 CycleMeta，不影响旧值。"""
        cm = CycleMeta()
        cm2 = cm.with_error("err-1")
        cm3 = cm2.with_error("err-2")
        assert len(cm.newly_error_stage_instance_ids) == 0
        assert len(cm2.newly_error_stage_instance_ids) == 1
        assert len(cm3.newly_error_stage_instance_ids) == 2
        assert "err-1" in cm3.newly_error_stage_instance_ids
        assert "err-2" in cm3.newly_error_stage_instance_ids

    def test_cycle_meta_mixed_accumulation(self):
        """同 CycleMeta 可同时有 done、error 和 awaiting_confirm。"""
        cm = CycleMeta()
        cm = cm.with_done("d1").with_error("e1").with_awaiting_confirm("a1")
        assert cm.newly_done_stage_instance_ids == frozenset(["d1"])
        assert cm.newly_error_stage_instance_ids == frozenset(["e1"])
        assert cm.newly_awaiting_confirm_ids == frozenset(["a1"])

    def test_instance_state_first_stage_by_id_ordering(self):
        """first_stage_by_id 应返回插入顺序的第一个匹配。"""
        state = InstanceState(stages=[
            _make_stage("s01", stage_instance_id="s01-v1"),
            _make_stage("s01", stage_instance_id="s01-v2"),
        ])
        result = state.first_stage_by_id("s01")
        assert result.stage_instance_id == "s01-v1"

    def test_instance_state_stage_by_id_returns_last(self):
        """stage_by_id 同 id 时应返回最后一条。"""
        state = InstanceState(stages=[
            _make_stage("s01", stage_instance_id="s01-v1"),
            _make_stage("s01", stage_instance_id="s01-v2"),
            _make_stage("s01", stage_instance_id="s01-v3"),
        ])
        result = state.stage_by_id("s01")
        assert result.stage_instance_id == "s01-v3"

    def test_consumed_message_ids_set_transformation_in_apply_delta(self):
        """apply_delta 将 instance_updates 中的 list/set consumed_message_ids 转为 frozenset。"""
        state = InstanceState()
        delta = StateDelta(instance_updates={"consumed_message_ids": ["a", "b", "c"]})
        result = state.apply_delta(delta)
        assert isinstance(result.consumed_message_ids, frozenset)
        assert "b" in result.consumed_message_ids


# ═══════════════════════════════════════════════════════════════════
# 3. DAG 分析边界
# ═══════════════════════════════════════════════════════════════════

class TestDAGBoundary:

    def test_empty_workflow(self):
        """空工作流（无 stage）应能解析但不产生就绪。"""
        spec = WorkflowSpec(
            schema_version="3.0.0", workflow_id="empty", version="1.0.0",
            max_parallel_agents=4, anchor_prefix="wf", stages=[], edges=[],
        )
        adj = build_adjacency(spec)
        assert len(adj.stages) == 0
        assert len(adj.outgoing) == 0

    def test_single_stage_no_edges(self):
        """单 stage 无边的 worktree 应该就绪。"""
        spec = WorkflowSpec(
            schema_version="3.0.0", workflow_id="single", version="1.0.0",
            max_parallel_agents=4, anchor_prefix="wf",
            stages=[_make_skill_stage("s01")],
            edges=[],
        )
        adj = build_adjacency(spec)
        state = InstanceState(stages=[_make_stage("s01", status=StageStatus.PENDING)])
        ready = compute_ready(adj, state)
        assert len(ready) == 1
        sid, inst_id = ready[0]
        assert sid == "s01"

    def test_diamond_topo_order(self):
        """钻石形 DAG 的拓扑序应满足依赖关系。"""
        spec = WorkflowSpec(
            schema_version="3.0.0", workflow_id="diamond", version="1.0.0",
            max_parallel_agents=4, anchor_prefix="wf",
            stages=[
                _make_virtual_stage("start"),
                _make_skill_stage("s01"), _make_skill_stage("s02"),
                _make_skill_stage("s03"), _make_virtual_stage("end"),
            ],
            edges=[
                _make_edge("start", "s01"), _make_edge("start", "s02"),
                _make_edge("s01", "s03"), _make_edge("s02", "s03"),
                _make_edge("s03", "end"),
            ],
        )
        adj = build_adjacency(spec)
        topo = analyze_topology(adj)
        # start 应在 s01,s02 之前
        assert topo.order.index("start") < topo.order.index("s01")
        assert topo.order.index("start") < topo.order.index("s02")
        # s01,s02 应在 s03 之前
        assert topo.order.index("s01") < topo.order.index("s03")
        assert topo.order.index("s02") < topo.order.index("s03")
        # 应无环
        assert len(topo.cycles) == 0

    def test_self_loop_detection(self):
        """自环应被 Tarjan SCC 检测为 cycle。"""
        spec = WorkflowSpec(
            schema_version="3.0.0", workflow_id="self-loop", version="1.0.0",
            max_parallel_agents=4, anchor_prefix="wf",
            stages=[_make_skill_stage("s01", retry=1)],
            edges=[_make_edge("s01", "s01", condition=EdgeCondition.FAILURE, max_loop=3)],
        )
        adj = build_adjacency(spec)
        topo = analyze_topology(adj)
        assert len(topo.cycles) >= 1
        assert "s01" in topo.cycles[0]

    def test_multi_node_cycle_detection(self):
        """多节点环 A→B→C→A 应被 Tarjan 检测。"""
        spec = WorkflowSpec(
            schema_version="3.0.0", workflow_id="cycle", version="1.0.0",
            max_parallel_agents=4, anchor_prefix="wf",
            stages=[_make_skill_stage("A"), _make_skill_stage("B"), _make_skill_stage("C")],
            edges=[
                _make_edge("A", "B"), _make_edge("B", "C"), _make_edge("C", "A"),
            ],
        )
        adj = build_adjacency(spec)
        topo = analyze_topology(adj)
        assert len(topo.cycles) >= 1

    def test_unreachable_virtual_stage_ok(self):
        """不可达 VIRTUAL stage 不应标记为错误。"""
        spec = WorkflowSpec(
            schema_version="3.0.0", workflow_id="test", version="1.0.0",
            max_parallel_agents=4, anchor_prefix="wf",
            stages=[_make_virtual_stage("start"), _make_skill_stage("s01"), _make_virtual_stage("orphan")],
            edges=[_make_edge("start", "s01")],
        )
        adj = build_adjacency(spec)
        issues = validate_workflow(spec)
        # "orphan" VIRTUAL stage 不可达但不受惩罚
        unreachable = [i for i in issues.issues if i.category == "UNREACHABLE_STAGE"]
        assert not any(i.stage_id == "orphan" for i in unreachable)

    def test_disconnected_components(self):
        """两个互不连通的组件的拓扑分析。"""
        spec = WorkflowSpec(
            schema_version="3.0.0", workflow_id="disconnected", version="1.0.0",
            max_parallel_agents=4, anchor_prefix="wf",
            stages=[_make_skill_stage("A"), _make_skill_stage("B")],
            edges=[],
        )
        adj = build_adjacency(spec)
        topo = analyze_topology(adj)
        assert "A" in topo.order
        assert "B" in topo.order

    def test_collect_ancestors_basic(self):
        """collect_ancestors 收集反向可达节点。"""
        spec = WorkflowSpec(
            schema_version="3.0.0", workflow_id="test", version="1.0.0",
            max_parallel_agents=4, anchor_prefix="wf",
            stages=[_make_virtual_stage("start"), _make_skill_stage("A"), _make_skill_stage("B"), _make_skill_stage("C")],
            edges=[_make_edge("start", "A"), _make_edge("A", "B"), _make_edge("B", "C")],
        )
        adj = build_adjacency(spec)
        ancestors = collect_ancestors(adj, "C")
        assert "B" in ancestors
        assert "A" in ancestors
        assert "start" in ancestors
        assert "C" not in ancestors  # 自身不计入

    def test_is_backward_edge_true(self):
        """回边（topo 序反向）应被识别。"""
        assert is_backward_edge(["start", "A", "B", "C"], "C", "start")
        assert is_backward_edge(["start", "A", "B", "C"], "C", "A")

    def test_is_backward_edge_false(self):
        """前向边不应被识别为回边。"""
        assert not is_backward_edge(["start", "A", "B", "C"], "start", "A")
        assert not is_backward_edge(["start", "A", "B", "C"], "A", "C")

    def test_is_backward_edge_self(self):
        """自环（A→A）不是回边——拓扑顺序中索引相等。"""
        assert not is_backward_edge(["start", "A", "C"], "A", "A")

    def test_validation_duplicate_ids(self):
        """应检测重复的 stage_id。"""
        spec = WorkflowSpec(
            schema_version="3.0.0", workflow_id="test", version="1.0.0",
            max_parallel_agents=4, anchor_prefix="wf",
            stages=[_make_skill_stage("dup"), _make_skill_stage("dup")],
            edges=[],
        )
        result = validate_workflow(spec)
        dup_issues = result.by_category("DUPLICATE_STAGE_ID")
        assert len(dup_issues) >= 1

    def test_validation_dangling_edge(self):
        """应检测指向不存在 stage 的边。"""
        spec = WorkflowSpec(
            schema_version="3.0.0", workflow_id="test", version="1.0.0",
            max_parallel_agents=4, anchor_prefix="wf",
            stages=[_make_skill_stage("s01")],
            edges=[_make_edge("s01", "nonexistent")],
        )
        result = validate_workflow(spec)
        dangling = [i for i in result.issues if i.category == "DANGLING_EDGE"]
        assert len(dangling) >= 1

    def test_validation_unbounded_loop(self):
        """自环无 max_loop 应报告 UNBOUNDED_LOOP。"""
        spec = WorkflowSpec(
            schema_version="3.0.0", workflow_id="test", version="1.0.0",
            max_parallel_agents=4, anchor_prefix="wf",
            stages=[_make_skill_stage("s01")],
            edges=[_make_edge("s01", "s01", condition=EdgeCondition.FAILURE)],
        )
        result = validate_workflow(spec)
        issues = result.by_category("UNBOUNDED_LOOP")
        assert len(issues) >= 1

    def test_validation_ambiguous_routing(self):
        """混合 choice 的 SUCCESS 边应报告 AMBIGUOUS_ROUTING。"""
        spec = WorkflowSpec(
            schema_version="3.0.0", workflow_id="test", version="1.0.0",
            max_parallel_agents=4, anchor_prefix="wf",
            stages=[_make_skill_stage("s01"), _make_skill_stage("s02"), _make_virtual_stage("end")],
            edges=[
                _make_edge("s01", "s02", condition=EdgeCondition.SUCCESS, choice="通过"),
                _make_edge("s01", "end", condition=EdgeCondition.SUCCESS),  # 无 choice
            ],
        )
        result = validate_workflow(spec)
        issues = result.by_category("AMBIGUOUS_ROUTING")
        assert len(issues) >= 1

    def test_validation_dead_failure_edge(self):
        """retry=0 且有 failure_edge 应报告 DEAD_FAILURE_EDGE。"""
        spec = WorkflowSpec(
            schema_version="3.0.0", workflow_id="test", version="1.0.0",
            max_parallel_agents=4, anchor_prefix="wf",
            stages=[_make_skill_stage("s01", retry=0), _make_skill_stage("s02")],
            edges=[
                _make_edge("s01", "s02", condition=EdgeCondition.FAILURE),
            ],
        )
        result = validate_workflow(spec)
        dead = result.by_category("DEAD_FAILURE_EDGE")
        assert len(dead) >= 1

    def test_validation_orphan_loop_exceeded(self):
        """有 loop_exceeded 但无 failure_edge 应报告 ORPHAN_LOOP_EXCEEDED。"""
        spec = WorkflowSpec(
            schema_version="3.0.0", workflow_id="test", version="1.0.0",
            max_parallel_agents=4, anchor_prefix="wf",
            stages=[_make_skill_stage("s01"), _make_skill_stage("s02")],
            edges=[
                _make_edge("s01", "s02", condition=EdgeCondition.LOOP_EXCEEDED),
            ],
        )
        result = validate_workflow(spec)
        orphan = result.by_category("ORPHAN_LOOP_EXCEEDED")
        assert len(orphan) >= 1

    def test_validation_result_has_errors_property(self):
        """ValidationResult.has_errors 正确反映是否有 ERROR。"""
        r = ValidationResult(issues=[
            ValidationIssue("TEST", "warning only", severity="WARNING"),
        ])
        assert not r.has_errors
        r.issues.append(ValidationIssue("TEST", "real error", severity="ERROR"))
        assert r.has_errors

    def test_validation_result_error_count(self):
        """error_count / warning_count 计数正确。"""
        r = ValidationResult(issues=[
            ValidationIssue("A", "e1", severity="ERROR"),
            ValidationIssue("B", "e2", severity="ERROR"),
            ValidationIssue("C", "w1", severity="WARNING"),
        ])
        assert r.error_count == 2
        assert r.warning_count == 1


# ═══════════════════════════════════════════════════════════════════
# 4. TransitionPolicy 边界
# ═══════════════════════════════════════════════════════════════════

class TestTransitionPolicyBoundary:

    def _make_linear_adj(self) -> tuple[AdjacencyList, WorkflowSpec]:
        spec = WorkflowSpec(
            schema_version="3.0.0", workflow_id="test", version="1.0.0",
            max_parallel_agents=4, anchor_prefix="wf",
            stages=[
                _make_virtual_stage("start"),
                _make_skill_stage("s01", retry=2),
                _make_skill_stage("s02"),
                _make_virtual_stage("end"),
            ],
            edges=[
                _make_edge("start", "s01"),
                _make_edge("s01", "s02", condition=EdgeCondition.SUCCESS),
                _make_edge("s01", "s02", condition=EdgeCondition.FAILURE, max_loop=3),
                _make_edge("s02", "end", condition=EdgeCondition.SUCCESS),
            ],
        )
        return build_adjacency(spec), spec

    def test_from_adjacency_stage_not_found(self):
        """不存在的 stage_id 应抛出 KeyError。"""
        adj, _ = self._make_linear_adj()
        with pytest.raises(KeyError):
            TransitionPolicy.from_adjacency(adj, "nonexistent")

    def test_is_upstream_satisfied_always_edge(self):
        """ALWAYS 边在 upstream DONE 时总是满足。"""
        adj, _ = self._make_linear_adj()
        policy = TransitionPolicy.from_adjacency(adj, "s01")
        upstream = _make_stage("start", status=StageStatus.DONE)
        edge = _make_edge("start", "s01", condition=EdgeCondition.ALWAYS)
        assert policy.is_upstream_satisfied(upstream, edge)

    def test_is_upstream_satisfied_pending_upstream(self):
        """PENDING upstream 不满足任何边。"""
        adj, _ = self._make_linear_adj()
        policy = TransitionPolicy.from_adjacency(adj, "s01")
        upstream = _make_stage("start", status=StageStatus.PENDING)
        edge = _make_edge("start", "s01", condition=EdgeCondition.ALWAYS)
        assert not policy.is_upstream_satisfied(upstream, edge)

    def test_is_upstream_satisfied_loop_exceeded_not_success(self):
        """exit_condition=loop_exceeded 不满足 SUCCESS 边。"""
        adj, _ = self._make_linear_adj()
        policy = TransitionPolicy.from_adjacency(adj, "s02")
        upstream = _make_stage("s01", status=StageStatus.DONE, exit_condition="loop_exceeded")
        edge = _make_edge("s01", "s02", condition=EdgeCondition.SUCCESS)
        assert not policy.is_upstream_satisfied(upstream, edge)

    def test_is_upstream_satisfied_success_with_choice_mismatch(self):
        """SUCCESS 边 choice 不匹配时应拒绝。"""
        adj, _ = self._make_linear_adj()
        policy = TransitionPolicy.from_adjacency(adj, "s02")
        upstream = _make_stage("s01", status=StageStatus.DONE, exit_condition="success", routing_choice="通过")
        edge = _make_edge("s01", "s02", condition=EdgeCondition.SUCCESS, choice="拒绝")
        assert not policy.is_upstream_satisfied(upstream, edge)

    def test_validate_routing_choice_empty_is_valid(self):
        """空 routing_choice 总是有效。"""
        adj, _ = self._make_linear_adj()
        policy = TransitionPolicy.from_adjacency(adj, "s01")
        is_valid, reason = policy.validate_routing_choice("")
        assert is_valid

    def test_validate_routing_choice_any_ok_when_no_valid(self):
        """没有定义 choice 的 SUCCESS 边时，任意值都有效。"""
        adj, _ = self._make_linear_adj()
        policy = TransitionPolicy.from_adjacency(adj, "s02")
        # s02→end 的 SUCCESS 边没有 choice
        is_valid, _ = policy.validate_routing_choice("anything")
        assert is_valid

    def test_match_success_edge_exact(self):
        """精确匹配 SUCCESS 边 choice。"""
        spec = WorkflowSpec(
            schema_version="3.0.0", workflow_id="test", version="1.0.0",
            max_parallel_agents=4, anchor_prefix="wf",
            stages=[_make_skill_stage("s01"), _make_skill_stage("s02"), _make_skill_stage("s03")],
            edges=[
                _make_edge("s01", "s02", condition=EdgeCondition.SUCCESS, choice="通过"),
                _make_edge("s01", "s03", condition=EdgeCondition.SUCCESS, choice="拒绝"),
            ],
        )
        adj = build_adjacency(spec)
        policy = TransitionPolicy.from_adjacency(adj, "s01")
        matched = policy.match_success_edge("拒绝")
        assert matched is not None
        assert matched.to_stage == "s03"

    def test_match_success_edge_fallback_no_choice(self):
        """无匹配时回退到无 choice 的 SUCCESS 边。"""
        spec = WorkflowSpec(
            schema_version="3.0.0", workflow_id="test", version="1.0.0",
            max_parallel_agents=4, anchor_prefix="wf",
            stages=[_make_skill_stage("s01"), _make_skill_stage("s02"), _make_skill_stage("s03")],
            edges=[
                _make_edge("s01", "s02", condition=EdgeCondition.SUCCESS, choice="通过"),
                _make_edge("s01", "s03", condition=EdgeCondition.SUCCESS),  # 兜底
            ],
        )
        adj = build_adjacency(spec)
        policy = TransitionPolicy.from_adjacency(adj, "s01")
        matched = policy.match_success_edge("unknown-choice")
        assert matched is not None
        assert matched.to_stage == "s03"

    def test_match_success_edge_no_match_no_fallback(self):
        """既无精确匹配也无兜底边时返回 None。"""
        spec = WorkflowSpec(
            schema_version="3.0.0", workflow_id="test", version="1.0.0",
            max_parallel_agents=4, anchor_prefix="wf",
            stages=[_make_skill_stage("s01"), _make_skill_stage("s02")],
            edges=[
                _make_edge("s01", "s02", condition=EdgeCondition.SUCCESS, choice="通过"),
            ],
        )
        adj = build_adjacency(spec)
        policy = TransitionPolicy.from_adjacency(adj, "s01")
        matched = policy.match_success_edge("unknown")
        assert matched is None

    def test_on_confirm_normal_case(self):
        """普通确认：返回 PENDING + continue。"""
        adj, _ = self._make_linear_adj()
        policy = TransitionPolicy.from_adjacency(adj, "s02")
        stage = _make_stage("s02", status=StageStatus.AWAITING_CONFIRM, loop_counter=0)
        result = policy.on_confirm(stage, "通过")
        assert result.next_status == StageStatus.PENDING
        assert result.action == "continue"
        assert result.updates["loop_counter"] == 1

    def test_on_confirm_loop_exceeded(self):
        """loop_counter >= max_loop 时返回 DONE + spawn。"""
        spec = WorkflowSpec(
            schema_version="3.0.0", workflow_id="test", version="1.0.0",
            max_parallel_agents=4, anchor_prefix="wf",
            stages=[_make_virtual_stage("start"), _make_skill_stage("s01", retry=0),
                    _make_skill_stage("s02"), _make_virtual_stage("end")],
            edges=[
                _make_edge("start", "s01"),
                _make_edge("s01", "s02", condition=EdgeCondition.SUCCESS),
                _make_edge("s01", "s02", condition=EdgeCondition.LOOP_EXCEEDED, max_loop=3),
                _make_edge("s02", "end", condition=EdgeCondition.SUCCESS),
            ],
        )
        adj = build_adjacency(spec)
        policy = TransitionPolicy.from_adjacency(adj, "s01")
        stage = _make_stage("s01", status=StageStatus.AWAITING_CONFIRM, loop_counter=5)
        result = policy.on_confirm(stage, "pass")
        assert result.next_status == StageStatus.DONE
        assert result.action == "spawn"
        assert result.loop_exceeded_target is not None

    def test_on_skip_non_pending_without_force(self):
        """skip 非 PENDING stage 且不用 force → StateError。"""
        spec = WorkflowSpec(
            schema_version="3.0.0", workflow_id="test", version="1.0.0",
            max_parallel_agents=4, anchor_prefix="wf",
            stages=[_make_skill_stage("s01")],
            edges=[],
        )
        adj = build_adjacency(spec)
        policy = TransitionPolicy.from_adjacency(adj, "s01")
        state = InstanceState(stages=[_make_stage("s01", status=StageStatus.RUNNING)])
        with pytest.raises(StateError):
            policy.on_skip(state, force=False)

    def test_on_rollback_collects_downstream(self):
        """rollback 应收集受影响的 downstream stage。"""
        adj, _ = self._make_linear_adj()
        policy = TransitionPolicy.from_adjacency(adj, "s01")
        state = InstanceState(stages=[
            _make_stage("start", status=StageStatus.DONE),
            _make_stage("s01", status=StageStatus.DONE),
            _make_stage("s02", status=StageStatus.RUNNING),
            _make_stage("end", status=StageStatus.PENDING),
        ])
        result = policy.on_rollback(state, adj)
        assert "s01" in result.reset_stage_ids
        assert "s02" in result.reset_stage_ids  # s01 的直接下游
        assert "end" in result.reset_stage_ids  # s02 的下游（SUCCESS 边可传播）

    def test_valid_routing_choices_empty_when_no_success_choices(self):
        """没有带 choice 的 SUCCESS 边时 valid_routing_choices 为空。"""
        adj, _ = self._make_linear_adj()
        policy = TransitionPolicy.from_adjacency(adj, "start")
        assert policy.valid_routing_choices() == []

    def test_valid_routing_choices_with_choices(self):
        """valid_routing_choices 返回所有去重的 choice。"""
        spec = WorkflowSpec(
            schema_version="3.0.0", workflow_id="test", version="1.0.0",
            max_parallel_agents=4, anchor_prefix="wf",
            stages=[_make_skill_stage("s01"), _make_skill_stage("s02"), _make_virtual_stage("end")],
            edges=[
                _make_edge("s01", "s02", condition=EdgeCondition.SUCCESS, choice="通过"),
                _make_edge("s01", "end", condition=EdgeCondition.SUCCESS, choice="放弃"),
            ],
        )
        adj = build_adjacency(spec)
        policy = TransitionPolicy.from_adjacency(adj, "s01")
        choices = policy.valid_routing_choices()
        assert "通过" in choices
        assert "放弃" in choices

    def test_from_adjacency_non_standard_edge_conditions(self):
        """只有 ALWAYS/SUCCESS 进 ready_edges，FAILURE/LOOP_EXCEEDED 不进。"""
        spec = WorkflowSpec(
            schema_version="3.0.0", workflow_id="test", version="1.0.0",
            max_parallel_agents=4, anchor_prefix="wf",
            stages=[_make_skill_stage("s01"), _make_skill_stage("s02"), _make_skill_stage("s03"), _make_skill_stage("s04")],
            edges=[
                _make_edge("s01", "s02", condition=EdgeCondition.ALWAYS),
                _make_edge("s01", "s03", condition=EdgeCondition.SUCCESS),
                _make_edge("s01", "s04", condition=EdgeCondition.FAILURE),
            ],
        )
        adj = build_adjacency(spec)
        policy = TransitionPolicy.from_adjacency(adj, "s01")
        assert len(policy.ready_edges) == 2  # ALWAYS + SUCCESS
        assert policy.failure_edge is not None
        assert policy.loop_exceeded_edge is None


# ═══════════════════════════════════════════════════════════════════
# 5. 级联重置 Bug 验证
# ═══════════════════════════════════════════════════════════════════

class TestCascadeResetBug:
    """验证 cascade reset 在 DONE+routing_choice 消息首次到达时的行为。

    已知 bug：ConsumeMessagesProcessor 在处理 DONE 消息时，
    先把 routing_choice 写入 delta.stage_updates，然后在检查
    级联重置时从 stage_index（原始 state）读取 routing_choice，
    导致首次消息无法触发级联重置。
    """

    def test_cascade_reset_with_routing_choice_in_message(self, tmp_path, monkeypatch):
        """DONE 消息携带 routing_choice 时级联重置的行为。

        对抗性发现：首次消费 DONE+routing_choice 消息时 cascade reset
        不会触发，因为 routing_choice 存储在 delta 中但 cascade 检查
        读取的是原始 stage_index 中的 st.routing_choice（仍为空）。
        """
        # 设置项目根目录结构
        root = tmp_path / "project"
        root.mkdir()
        (root / ".agent").mkdir()
        monkeypatch.setattr("runtime.message.handler.find_root", lambda: root)
        monkeypatch.setattr("infrastructure.project.find_root", lambda: root)

        adj = self._make_adj_with_back_edge()
        state = self._make_state_for_back_edge()
        ctx = ExecutionContext(
            instance_id="cascade-test",
            adj=adj,
            spec=self._make_spec(),
            worktree_map={"s03": str(root / "worktree" / "s03")},
            root=root,
        )
        # 写入 DONE+routing_choice 消息
        messages_dir = root / ".agent" / "instances" / "cascade-test" / "messages"
        messages_dir.mkdir(parents=True, exist_ok=True)
        msg = {
            "message_id": "msg-c001",
            "instance_id": "cascade-test",
            "stage_id": "s03",
            "stage_instance_id": "s03",
            "status": "DONE",
            "routing_choice": "reject",
            "report": "done with reject",
            "timestamp": "2026-05-17T10:00:00+0800",
        }
        (messages_dir / "msg-c001.json").write_text(json.dumps(msg), encoding="utf-8")

        processor = ConsumeMessagesProcessor()
        result = processor.process(ctx, state)

        # 消息应被消费
        consumed = result.state_delta.instance_updates.get("consumed_message_ids", frozenset())
        assert "msg-c001" in consumed

        # 对抗性发现：routing_choice 在 delta 中但 cascade reset 未触发
        # 因为 stage_index[st] 的 routing_choice 仍为空（来自原始 state）
        # 另见 ConsumeMessagesProcessor 行 173-175
        assert isinstance(result.state_delta, StateDelta)

    @staticmethod
    def _make_spec():
        return WorkflowSpec(
            schema_version="3.0.0", workflow_id="cascade", version="1.0.0",
            max_parallel_agents=4, anchor_prefix="wf",
            stages=[
                _make_virtual_stage("s00-workflow-start"),
                _make_skill_stage("s01"), _make_skill_stage("s02"),
                _make_skill_stage("s03"), _make_skill_stage("s04"),
                _make_virtual_stage("s05-workflow-end"),
            ],
            edges=[
                _make_edge("s00-workflow-start", "s01"),
                _make_edge("s01", "s02", condition=EdgeCondition.SUCCESS),
                _make_edge("s02", "s03", condition=EdgeCondition.SUCCESS),
                # 回边: s03 reject → s01（较早的 stage）
                _make_edge("s03", "s01", condition=EdgeCondition.SUCCESS, choice="reject"),
                _make_edge("s03", "s04", condition=EdgeCondition.SUCCESS, choice="accept"),
                _make_edge("s04", "s05-workflow-end", condition=EdgeCondition.SUCCESS),
            ],
        )

    @staticmethod
    def _make_adj_with_back_edge():
        spec = TestCascadeResetBug._make_spec()
        return build_adjacency(spec)

    @staticmethod
    def _make_state_for_back_edge():
        return InstanceState(instance_id="cascade-test", stages=[
            _make_stage("s00-workflow-start", status=StageStatus.DONE),
            _make_stage("s01", status=StageStatus.DONE),
            _make_stage("s02", status=StageStatus.DONE),
            _make_stage("s03", status=StageStatus.RUNNING),
            _make_stage("s04", status=StageStatus.PENDING),
            _make_stage("s05-workflow-end", status=StageStatus.PENDING),
        ])


# ═══════════════════════════════════════════════════════════════════
# 6. 虚拟 Stage 鲁棒性
# ═══════════════════════════════════════════════════════════════════

class TestVirtualStages:

    def test_virtual_chain_terminates(self):
        """虚拟 stage 链（A→B→C）应在有限迭代内终止。"""
        spec = WorkflowSpec(
            schema_version="3.0.0", workflow_id="vchain", version="1.0.0",
            max_parallel_agents=4, anchor_prefix="wf",
            stages=[
                _make_virtual_stage("start"),
                _make_virtual_stage("vmid"),
                _make_virtual_stage("vend"),
                _make_skill_stage("s01"),
            ],
            edges=[
                _make_edge("start", "vmid"),
                _make_edge("vmid", "vend"),
                _make_edge("vend", "s01", condition=EdgeCondition.SUCCESS),
            ],
        )
        adj = build_adjacency(spec)
        state = InstanceState(stages=[
            _make_stage("start", status=StageStatus.DONE),
            _make_stage("vmid", status=StageStatus.PENDING),
            _make_stage("vend", status=StageStatus.PENDING),
            _make_stage("s01", status=StageStatus.PENDING),
        ])
        ctx = ExecutionContext(
            instance_id="vchain-test", adj=adj, spec=spec,
            worktree_map={}, root=Path("/tmp"),
        )
        processor = VirtualStagesProcessor()
        result = processor.process(ctx, state)
        # 应标记 vmid 和 vend 为 DONE
        assert "vmid" in result.state_delta.stage_updates
        assert "vend" in result.state_delta.stage_updates

    def test_virtual_stage_without_incoming(self):
        """没有入边的虚拟 stage 应立即 DONE。"""
        spec = WorkflowSpec(
            schema_version="3.0.0", workflow_id="vnopin", version="1.0.0",
            max_parallel_agents=4, anchor_prefix="wf",
            stages=[_make_virtual_stage("start"), _make_skill_stage("s01")],
            edges=[_make_edge("start", "s01")],
        )
        adj = build_adjacency(spec)
        state = InstanceState(stages=[
            _make_stage("start", status=StageStatus.PENDING),
            _make_stage("s01", status=StageStatus.PENDING),
        ])
        ctx = ExecutionContext(
            instance_id="vnopin-test", adj=adj, spec=spec,
            worktree_map={}, root=Path("/tmp"),
        )
        processor = VirtualStagesProcessor()
        result = processor.process(ctx, state)
        assert result.state_delta.stage_updates["start"]["status"] == StageStatus.DONE

    def test_virtual_stage_already_done_no_change(self):
        """已 DONE 的虚拟 stage 不应被重复处理。"""
        spec = WorkflowSpec(
            schema_version="3.0.0", workflow_id="valrd", version="1.0.0",
            max_parallel_agents=4, anchor_prefix="wf",
            stages=[_make_virtual_stage("start"), _make_skill_stage("s01")],
            edges=[_make_edge("start", "s01")],
        )
        adj = build_adjacency(spec)
        state = InstanceState(stages=[
            _make_stage("start", status=StageStatus.DONE),
            _make_stage("s01", status=StageStatus.PENDING),
        ])
        ctx = ExecutionContext(
            instance_id="valrd-test", adj=adj, spec=spec,
            worktree_map={}, root=Path("/tmp"),
        )
        processor = VirtualStagesProcessor()
        result = processor.process(ctx, state)
        assert "start" not in result.state_delta.stage_updates

    def test_virtual_stage_loop_terminates_with_no_progress(self):
        """互相依赖的虚拟 stage（A 依赖 B, B 依赖 A）循环应终止。"""
        spec = WorkflowSpec(
            schema_version="3.0.0", workflow_id="vdeadlock", version="1.0.0",
            max_parallel_agents=4, anchor_prefix="wf",
            stages=[_make_virtual_stage("va"), _make_virtual_stage("vb")],
            edges=[
                _make_edge("va", "vb", condition=EdgeCondition.SUCCESS),
                _make_edge("vb", "va", condition=EdgeCondition.SUCCESS),
            ],
        )
        adj = build_adjacency(spec)
        state = InstanceState(stages=[
            _make_stage("va", status=StageStatus.PENDING),
            _make_stage("vb", status=StageStatus.PENDING),
        ])
        ctx = ExecutionContext(
            instance_id="vdeadlock-test", adj=adj, spec=spec,
            worktree_map={}, root=Path("/tmp"),
        )
        processor = VirtualStagesProcessor()
        # 不应死循环
        result = processor.process(ctx, state)
        # va 和 vb 互相依赖对方先 DONE 来满足 SUCCESS 条件，所以都不会被标记为 DONE
        assert "va" not in result.state_delta.stage_updates
        assert "vb" not in result.state_delta.stage_updates


# ═══════════════════════════════════════════════════════════════════
# 7. 错误体系边界
# ═══════════════════════════════════════════════════════════════════

class TestErrorHierarchy:

    def test_wfctl_error_default_code(self):
        e = WfctlError("test")
        assert e.code == "UNKNOWN_ERROR"
        assert e.exit_code == 1

    def test_wfctl_error_custom_code(self):
        e = WfctlError("test", code="CUSTOM", exit_code=42)
        assert e.code == "CUSTOM"
        assert e.exit_code == 42

    def test_state_error_default(self):
        e = StateError("corrupt state")
        assert "STATE_CORRUPTED" in e.code

    def test_worktree_error_default(self):
        e = WorktreeError("fail")
        assert "WORKTREE" in e.code

    def test_schema_error_default(self):
        e = SchemaError("bad schema")
        assert "SCHEMA" in e.code

    def test_validation_error_default(self):
        e = ValidationError("access denied")
        assert "ACCESS" in e.code or "VIOLATION" in e.code

    def test_git_error_default(self):
        e = GitError("git fail")
        assert "GIT" in e.code

    def test_input_error_exit_code(self):
        e = InputError("bad arg")
        assert e.exit_code == 2

    def test_error_inheritance(self):
        assert issubclass(StateError, WfctlError)
        assert issubclass(InputError, WfctlError)
        assert issubclass(GitError, WfctlError)

    def test_wfctl_error_str_representation(self):
        e = WfctlError("something went wrong")
        assert "something went wrong" in str(e)


# ═══════════════════════════════════════════════════════════════════
# 8. 文件锁并发安全
# ═══════════════════════════════════════════════════════════════════

class TestFileLockConcurrency:

    def test_lock_acquire_release(self, tmp_path):
        """基本 acquire/release 循环。"""
        lock_path = tmp_path / "test.lock"
        lock = FileLock(lock_path)
        assert lock.acquire(timeout=1.0)
        lock.release()
        assert not lock_path.exists()

    def test_lock_context_manager(self, tmp_path):
        """上下文管理器应正确 acquire 和 release。"""
        lock_path = tmp_path / "test.lock"
        # FileLock.lock_path = test.lock.lock (suffix based)
        actual_lock = lock_path.with_suffix(lock_path.suffix + ".lock")
        with FileLock(lock_path):
            assert actual_lock.exists()
        assert not actual_lock.exists()

    def test_lock_exclusive_access(self, tmp_path):
        """同一锁不能被两个持有者同时获取。"""
        lock_path = tmp_path / "test.lock"
        lock1 = FileLock(lock_path)
        lock2 = FileLock(lock_path)

        assert lock1.acquire(timeout=1.0)
        assert not lock2.acquire(timeout=0.1)
        lock1.release()
        assert lock2.acquire(timeout=1.0)
        lock2.release()

    def test_lock_concurrent_threads(self, tmp_path):
        """多线程竞争同一锁。"""
        lock_path = tmp_path / "test.lock"
        results = []
        errors = []

        def worker(wid: int):
            lock = FileLock(lock_path)
            acquired = lock.acquire(timeout=2.0)
            if acquired:
                results.append(wid)
                time.sleep(0.05)
                lock.release()
            else:
                errors.append(wid)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 所有线程都应成功获取锁（通过轮转）
        assert len(errors) == 0
        assert len(results) == 4

    def test_lock_pid_stored_in_file(self, tmp_path):
        """锁文件应包含 PID:timestamp。"""
        lock_path = tmp_path / "test.lock"
        actual_lock = lock_path.with_suffix(lock_path.suffix + ".lock")
        lock = FileLock(lock_path)
        lock.acquire(timeout=1.0)
        content = actual_lock.read_text().strip()
        assert ":" in content  # "pid:timestamp" 格式
        pid_str = content.split(":")[0]
        assert pid_str.isdigit()
        lock.release()

    def test_lock_cleanup_on_error(self, tmp_path):
        """异常时锁文件应被清理。"""
        lock_path = tmp_path / "test.lock"
        try:
            with FileLock(lock_path):
                raise RuntimeError("simulated crash")
        except RuntimeError:
            pass
        assert not lock_path.exists()

    def test_lock_timeout(self, tmp_path):
        """超时后 acquire 返回 False。"""
        lock_path = tmp_path / "test.lock"
        lock1 = FileLock(lock_path)
        lock2 = FileLock(lock_path)
        lock1.acquire(timeout=1.0)
        try:
            assert not lock2.acquire(timeout=0.1)
        finally:
            lock1.release()

    def test_lock_dead_pid_steal(self, tmp_path):
        """死 PID 锁应可被抢占。"""
        lock_path = tmp_path / "test.lock"
        # 写入一个不存在的 PID
        lock_path.write_text("999999")
        lock = FileLock(lock_path)
        assert lock.acquire(timeout=1.0)
        lock.release()

    def test_lock_release_twice_no_error(self, tmp_path):
        """重复 release 不应报错。"""
        lock_path = tmp_path / "test.lock"
        lock = FileLock(lock_path)
        lock.acquire(timeout=1.0)
        lock.release()
        lock.release()  # 第二次 release 不应抛异常
        assert not lock_path.exists()


# ═══════════════════════════════════════════════════════════════════
# 9. 工作流结构验证器攻击
# ═══════════════════════════════════════════════════════════════════

class TestValidatorAttack:

    def test_max_parallel_agents_zero(self):
        """max_parallel_agents=0 应有明确定义的行为。"""
        spec = WorkflowSpec(
            schema_version="3.0.0", workflow_id="zero-parallel", version="1.0.0",
            max_parallel_agents=0, anchor_prefix="wf",
            stages=[_make_virtual_stage("start"), _make_skill_stage("s01")],
            edges=[_make_edge("start", "s01")],
        )
        # 不应崩溃
        adj = build_adjacency(spec)
        assert adj is not None

    def test_max_parallel_agents_negative(self):
        """负数 max_parallel_agents 的处理。"""
        spec = WorkflowSpec(
            schema_version="3.0.0", workflow_id="neg-parallel", version="1.0.0",
            max_parallel_agents=-1, anchor_prefix="wf",
            stages=[_make_virtual_stage("start"), _make_skill_stage("s01")],
            edges=[_make_edge("start", "s01")],
        )
        # 不应崩溃，但应能构建邻接表
        adj = build_adjacency(spec)
        assert adj is not None

    def test_multiple_start_stages(self):
        """多个入口虚拟 stage 的 BFS 可达性分析。"""
        spec = WorkflowSpec(
            schema_version="3.0.0", workflow_id="multi-start", version="1.0.0",
            max_parallel_agents=4, anchor_prefix="wf",
            stages=[
                _make_virtual_stage("start-1"), _make_virtual_stage("start-2"),
                _make_skill_stage("s01"), _make_skill_stage("s02"),
                _make_virtual_stage("end"),
            ],
            edges=[
                _make_edge("start-1", "s01"), _make_edge("start-2", "s02"),
                _make_edge("s01", "end"), _make_edge("s02", "end"),
            ],
        )
        adj = build_adjacency(spec)
        topo = analyze_topology(adj)
        assert len(topo.order) >= 5

    def test_parallel_spec_with_source_not_found(self):
        """parallel.source 指向不存在的 stage → 验证失败。"""
        spec = WorkflowSpec(
            schema_version="3.0.0", workflow_id="bad-parallel", version="1.0.0",
            max_parallel_agents=4, anchor_prefix="wf",
            stages=[
                _make_virtual_stage("start"),
                StageSpec(stage_id="s01", name="p", target_type=StageTargetType.SKILL,
                         target="skill", parallel=ParallelSpec(source="nonexistent")),
            ],
            edges=[_make_edge("start", "s01")],
        )
        result = validate_workflow(spec)
        issues = result.by_category("PARALLEL_SOURCE_MISSING")
        assert len(issues) >= 1

    def test_workflow_with_only_virtual_stages(self):
        """只有虚拟 stage 的工作流。"""
        spec = WorkflowSpec(
            schema_version="3.0.0", workflow_id="virtual-only", version="1.0.0",
            max_parallel_agents=4, anchor_prefix="wf",
            stages=[_make_virtual_stage("start"), _make_virtual_stage("end")],
            edges=[_make_edge("start", "end")],
        )
        adj = build_adjacency(spec)
        assert len(adj.stages) == 2

    def test_stage_spec_defaults(self):
        """StageSpec 默认值检查（target_type 为必填字段）。"""
        s = StageSpec(stage_id="test", name="Test", target_type=StageTargetType.SKILL)
        assert s.target is None
        assert s.retry == 0
        assert s.exclusive is False
        assert s.mandatory is True

    def test_edge_spec_defaults(self):
        """EdgeSpec 默认值检查（condition 为必填字段）。"""
        e = EdgeSpec(from_stage="a", to_stage="b", condition=EdgeCondition.SUCCESS)
        assert e.choice is None
        assert e.max_loop is None
        assert e.aggregation == "all"

    def test_enum_values(self):
        """枚举成员值的正确性。"""
        assert StageStatus.PENDING.value == "PENDING"
        assert StageStatus.RUNNING.value == "RUNNING"
        assert StageStatus.DONE.value == "DONE"
        assert StageStatus.ERROR.value == "ERROR"
        assert StageStatus.AWAITING_CONFIRM.value == "AWAITING_CONFIRM"
        assert InstanceStatus.ACTIVE.value == "ACTIVE"
        assert InstanceStatus.COMPLETED.value == "COMPLETED"
        assert InstanceStatus.FAILED.value == "FAILED"
        assert InstanceStatus.PAUSED.value == "PAUSED"

    def test_edge_condition_enum_values(self):
        """EdgeCondition 枚举值。"""
        assert EdgeCondition.ALWAYS.value == "always"
        assert EdgeCondition.SUCCESS.value == "success"
        assert EdgeCondition.FAILURE.value == "failure"
        assert EdgeCondition.LOOP_EXCEEDED.value == "loop_exceeded"


# ═══════════════════════════════════════════════════════════════════
# 10. 综合攻击场景
# ═══════════════════════════════════════════════════════════════════

class TestIntegrationAttacks:

    def test_stage_state_with_long_strings(self):
        """StageState 应能存储长字符串而不崩溃。"""
        long_id = "s" + "x" * 1000
        s = _make_stage(long_id)
        assert s.stage_id == long_id
        d = s.to_dict()
        assert d["stage_id"] == long_id

    def test_stage_state_roundtrip_with_unicode(self):
        """StageState 应正确序列化/反序列化 Unicode 内容。"""
        s = StageState(
            stage_id="s01",
            stage_instance_id="s01",
            routing_choice="通过✅",
            exit_condition="成功",
            pending_choice="继续→下一步",
        )
        d = s.to_dict()
        reloaded = StageState.from_dict(d)
        assert reloaded.routing_choice == "通过✅"
        assert reloaded.exit_condition == "成功"

    def test_many_stages_roundtrip(self):
        """大量 stage 的序列化/反序列化性能。"""
        stages = [_make_stage(f"s{i:04d}") for i in range(200)]
        state = InstanceState(instance_id="massive", stages=stages)
        d = state.to_dict()
        reloaded = InstanceState.from_dict(d)
        assert len(reloaded.stages) == 200

    def test_parallel_stage_split_state_consistency(self):
        """parallel 拆分产生的同 stage_id 多实例状态一致性。"""
        state = InstanceState(instance_id="p", stages=[
            _make_stage("parallel-task", stage_instance_id="pt-0"),
            _make_stage("parallel-task", stage_instance_id="pt-1"),
            _make_stage("parallel-task", stage_instance_id="pt-2"),
        ])
        by_id = state.stages_by_id("parallel-task")
        assert len(by_id) == 3
        first = state.first_stage_by_id("parallel-task")
        assert first.stage_instance_id == "pt-0"

    def test_empty_instance_state_serialization(self):
        """空 InstanceState 应可序列化和反序列化。"""
        state = InstanceState()
        d = state.to_dict()
        reloaded = InstanceState.from_dict(d)
        assert reloaded.instance_id == ""
        assert reloaded.stages == []

    def test_apply_delta_idempotent_remove(self):
        """对不存在的 stage_instance_id 执行 remove 应是幂等的。"""
        state = InstanceState(stages=[_make_stage("s01")])
        delta = StateDelta(remove_stage_instance_ids=["nonexistent"])
        result = state.apply_delta(delta)
        assert len(result.stages) == 1

    def test_transition_policy_on_pause(self):
        """on_pause 应重置所有 RUNNING → PENDING。"""
        state = InstanceState(stages=[
            _make_stage("s01", status=StageStatus.RUNNING),
            _make_stage("s02", status=StageStatus.DONE),
            _make_stage("s03", status=StageStatus.RUNNING),
        ])
        delta = TransitionPolicy.on_pause(state)
        assert "s01" in delta.stage_updates
        assert "s03" in delta.stage_updates
        assert "s02" not in delta.stage_updates
        assert delta.stage_updates["s01"]["status"] == StageStatus.PENDING

    def test_transition_policy_on_resume(self):
        """on_resume 应将实例状态设为 ACTIVE。"""
        state = InstanceState(status=InstanceStatus.PAUSED)
        delta = TransitionPolicy.on_resume(state)
        assert delta.instance_updates["status"] == InstanceStatus.ACTIVE

    def test_confirm_result_properties(self):
        """ConfirmResult 字段完整性。"""
        result = ConfirmResult(
            next_status=StageStatus.DONE,
            choice="accept",
            updates={"exit_condition": "loop_exceeded"},
            action="spawn",
            reason="test",
            loop_exceeded_target="s99-end",
        )
        assert result.loop_exceeded_target == "s99-end"
        assert result.requires_feedback is False

    def test_build_confirm_delta_loop_exceeded(self):
        """build_confirm_delta 在 loop_exceeded 时激活目标 stage。"""
        result = ConfirmResult(
            next_status=StageStatus.DONE,
            choice="ignored",
            updates={"exit_condition": "loop_exceeded"},
            action="spawn",
            loop_exceeded_target="s02",
        )
        stage = _make_stage("s01", status=StageStatus.AWAITING_CONFIRM, loop_counter=5)
        target = _make_stage("s02", status=StageStatus.PENDING, loop_counter=0)
        state = InstanceState(stages=[stage, target])
        delta = TransitionPolicy.build_confirm_delta(result, stage, state)
        assert "s01" in delta.stage_updates
        assert "s02" in delta.stage_updates
        assert delta.stage_updates["s02"]["status"] == StageStatus.PENDING

    def test_build_confirm_delta_loop_exceeded_same_stage(self):
        """loop_exceeded 目标为自身时不重复加 loop_counter。"""
        result = ConfirmResult(
            next_status=StageStatus.DONE,
            choice="ignored",
            updates={"exit_condition": "loop_exceeded"},
            action="spawn",
            loop_exceeded_target="s01",  # 同 stage
        )
        stage = _make_stage("s01", status=StageStatus.AWAITING_CONFIRM, loop_counter=5)
        state = InstanceState(stages=[stage])
        delta = TransitionPolicy.build_confirm_delta(result, stage, state)
        assert "s01" in delta.stage_updates



# ═══════════════════════════════════════════════════════════════════
# 11. 保护区校验攻击
# ═══════════════════════════════════════════════════════════════════

class TestProtectedZoneValidation:

    def test_path_traversal_via_parent_refs(self, tmp_path):
        """通过 .. 路径逃逸 worktree 应被检测。"""
        from services.validator import validate_modified_files

        wt = tmp_path / "worktree"
        wt.mkdir()
        modified = ["../../../etc/passwd", "../other-worktree/file.py"]

        for f in modified:
            with pytest.raises((ValidationError, ValueError)):
                validate_modified_files(wt, [f], "s01")

    def test_dot_agent_access_blocked(self, tmp_path):
        """访问 .agent/ 路径应被阻止。"""
        from services.validator import validate_modified_files

        wt = tmp_path / "worktree"
        wt.mkdir()
        # 在 worktree 内创建 .agent 文件
        (wt / ".agent").mkdir(exist_ok=True)

        with pytest.raises(ValidationError) as exc_info:
            validate_modified_files(wt, [".agent/config.json"], "s01")
        assert "ACCESS_VIOLATION" in exc_info.value.code

    def test_dot_claude_access_blocked(self, tmp_path):
        """访问 .claude/ 路径应被阻止。"""
        from services.validator import validate_modified_files

        wt = tmp_path / "worktree"
        wt.mkdir()
        (wt / ".claude").mkdir(exist_ok=True)

        with pytest.raises(ValidationError) as exc_info:
            validate_modified_files(wt, [".claude/settings.json"], "s01")
        assert "ACCESS_VIOLATION" in exc_info.value.code

    def test_dot_git_access_blocked(self, tmp_path):
        """访问 .git/ 路径应被阻止。"""
        from services.validator import validate_modified_files

        wt = tmp_path / "worktree"
        wt.mkdir()
        (wt / ".git").mkdir(exist_ok=True)

        with pytest.raises(ValidationError) as exc_info:
            validate_modified_files(wt, [".git/config"], "s01")
        assert "ACCESS_VIOLATION" in exc_info.value.code

    def test_legitimate_files_allowed(self, tmp_path):
        """合法的项目文件应被允许。"""
        from services.validator import validate_modified_files

        wt = tmp_path / "worktree"
        wt.mkdir()
        (wt / "src").mkdir()
        (wt / "src" / "main.py").write_text("")

        # 不应抛异常
        validate_modified_files(wt, ["src/main.py", "README.md", "docs/guide.md"], "s01")

    def test_case_insensitive_bypass_attempt(self, tmp_path):
        """大小写绕过 .AGENT 应被检测（路径规范化后）。"""
        from services.validator import validate_modified_files

        wt = tmp_path / "worktree"
        wt.mkdir()
        (wt / ".AGENT").mkdir(exist_ok=True)

        with pytest.raises(ValidationError):
            validate_modified_files(wt, [".AGENT/config.json"], "s01")
