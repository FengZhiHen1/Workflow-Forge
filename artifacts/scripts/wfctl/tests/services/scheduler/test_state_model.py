"""测试 state_model 的序列化/反序列化兼容性。"""

import pytest

from domain.workflow.spec import StageStatus, InstanceStatus
from state.model import InstanceState, StageState, StateDelta, CycleMeta


class TestStageState:
    def test_default_construction(self):
        s = StageState(stage_id="s01", stage_instance_id="s01")
        assert s.status == StageStatus.PENDING
        assert s.attempt_count == 0
        assert s.loop_counter == 0

    def test_replace(self):
        s = StageState(stage_id="s01", stage_instance_id="s01")
        s2 = s.replace(status=StageStatus.RUNNING, attempt_count=1)
        assert s2.status == StageStatus.RUNNING
        assert s2.attempt_count == 1
        assert s.status == StageStatus.PENDING  # 原对象不变

    def test_round_trip_dict(self):
        s = StageState(
            stage_id="s01",
            stage_instance_id="s01_0",
            status=StageStatus.DONE,
            exit_condition="success",
            output_message_id="msg-abc123",
            loop_counter=2,
            attempt_count=1,
            started_at="2026-05-24T12:00:00Z",
            model="claude-sonnet",
            fan_out_target={"id": "x", "label": "X"},
            valid_routing_choices=["a", "b"],
            conflict_files=["a.py"],
            confirm_questions=["确认继续？"],
        )
        d = s.to_dict()
        s2 = StageState.from_dict(d)
        assert s2 == s

    def test_from_dict_missing_fields(self):
        """旧 instance.json 可能缺少某些字段。"""
        d = {"stage_id": "s01", "stage_instance_id": "s01", "status": "PENDING"}
        s = StageState.from_dict(d)
        assert s.agent_id is None
        assert s.loop_counter == 0


class TestStateDelta:
    def test_merge(self):
        d1 = StateDelta(stage_updates={"s01": {"status": StageStatus.RUNNING}})
        d2 = StateDelta(stage_updates={"s01": {"attempt_count": 1}, "s02": {"status": StageStatus.DONE}})
        merged = d1.merge(d2)
        assert merged.stage_updates["s01"]["status"] == StageStatus.RUNNING
        assert merged.stage_updates["s01"]["attempt_count"] == 1
        assert merged.stage_updates["s02"]["status"] == StageStatus.DONE


class TestInstanceState:
    def test_default_construction(self):
        inst = InstanceState(instance_id="20260524-001", workflow_id="test")
        assert inst.status == InstanceStatus.ACTIVE
        assert inst.consumed_message_ids == frozenset()

    def test_with_stage(self):
        s = StageState(stage_id="s01", stage_instance_id="s01")
        inst = InstanceState(
            instance_id="20260524-001",
            workflow_id="test",
            stages=[s],
        )
        inst2 = inst.with_stage("s01", status=StageStatus.RUNNING)
        assert inst2.stage_by_id("s01").status == StageStatus.RUNNING
        assert inst.stage_by_id("s01").status == StageStatus.PENDING  # 原对象不变

    def test_apply_delta(self):
        s = StageState(stage_id="s01", stage_instance_id="s01")
        inst = InstanceState(
            instance_id="20260524-001",
            workflow_id="test",
            stages=[s],
        )
        delta = StateDelta(
            stage_updates={"s01": {"status": StageStatus.DONE, "exit_condition": "success"}},
            instance_updates={"status": InstanceStatus.COMPLETED},
        )
        inst2 = inst.apply_delta(delta)
        assert inst2.stage_by_id("s01").status == StageStatus.DONE
        assert inst2.stage_by_id("s01").exit_condition == "success"
        assert inst2.status == InstanceStatus.COMPLETED
        assert inst.status == InstanceStatus.ACTIVE  # 原对象不变

    def test_round_trip_dict(self):
        """完整 instance.json 格式的往返测试。"""
        inst = InstanceState(
            schema_version="3.0.0",
            instance_id="20260524-001",
            workflow_id="test-flow",
            version="1.0.0",
            goal="测试目标",
            status=InstanceStatus.ACTIVE,
            parent_instance_id=None,
            merge_confirmed=False,
            consumed_message_ids=frozenset(["msg-001"]),
            stages=[
                StageState(
                    stage_id="s00-workflow-start",
                    stage_instance_id="s00-workflow-start",
                    status=StageStatus.DONE,
                ),
                StageState(
                    stage_id="s01",
                    stage_instance_id="s01",
                    status=StageStatus.RUNNING,
                    started_at="2026-05-24T12:00:00Z",
                    agent_id="agent-1",
                    system_agent_id="sys-1",
                ),
                StageState(
                    stage_id="s02",
                    stage_instance_id="s02",
                    status=StageStatus.PENDING,
                    ),
            ],
        )
        d = inst.to_dict()
        inst2 = InstanceState.from_dict(d)
        assert inst2 == inst

    def test_from_dict_realistic(self):
        """从现有代码实际生成的 instance.json 结构解析。"""
        raw = {
            "schema_version": "3.0.0",
            "instance_id": "20260524-001",
            "workflow_id": "test-flow",
            "version": "1.0.0",
            "goal": "test",
            "status": "ACTIVE",
            "parent_instance_id": None,
            "consumed_message_ids": [],
            "stages": [
                {
                    "stage_id": "s01",
                    "stage_instance_id": "s01",
                    "status": "PENDING",
                    "agent_id": None,
                    "system_agent_id": None,
                    "output_message_id": None,
                    "loop_counter": 0,
                    "attempt_count": 0,
                    "confirmed": False,
                    "started_at": None,
                    "model": None,
                    "child_instance_id": None,
                    "fan_out_target": None,
                },
            ],
        }
        inst = InstanceState.from_dict(raw)
        assert inst.instance_id == "20260524-001"
        assert len(inst.stages) == 1
        assert inst.stage_by_id("s01").status == StageStatus.PENDING
        # 序列化回去应保持一致
        back = inst.to_dict()
        assert back["schema_version"] == "3.0.0"
        assert back["stages"][0]["stage_id"] == "s01"

    def test_apply_delta_append_remove_stages(self):
        """测试 parallel 拆分场景：移除旧 stage + 追加新 stages。"""
        inst = InstanceState(
            instance_id="20260524-001",
            workflow_id="test",
            stages=[
                StageState(stage_id="s01", stage_instance_id="s01"),
            ],
        )
        delta = StateDelta(
            remove_stage_instance_ids=["s01"],
            append_stages=[
                StageState(stage_id="s01", stage_instance_id="s01_0", fan_out_target={"id": "a"}),
                StageState(stage_id="s01", stage_instance_id="s01_1", fan_out_target={"id": "b"}),
            ],
        )
        inst2 = inst.apply_delta(delta)
        ids = {s.stage_instance_id for s in inst2.stages}
        assert ids == {"s01_0", "s01_1"}

    def test_parallel_stages_same_id(self):
        """验证同 stage_id 多个实例的场景（parallel 拆分）。"""
        raw = {
            "schema_version": "3.0.0",
            "instance_id": "20260524-001",
            "workflow_id": "test",
            "version": "1.0.0",
            "goal": "",
            "status": "ACTIVE",
            "consumed_message_ids": [],
            "stages": [
                {"stage_id": "s01", "stage_instance_id": "s01", "status": "PENDING"},
                {"stage_id": "s01", "stage_instance_id": "s01_0", "status": "PENDING", "fan_out_target": {"id": "a"}},
                {"stage_id": "s01", "stage_instance_id": "s01_1", "status": "PENDING", "fan_out_target": {"id": "b"}},
            ],
        }
        inst = InstanceState.from_dict(raw)
        assert len(inst.stages) == 3
        assert len(inst.stages_with_id("s01")) == 3
        # 序列化回去应保持三条
        back = inst.to_dict()
        assert len(back["stages"]) == 3


class TestCycleMeta:
    def test_default_construction(self):
        cm = CycleMeta()
        assert cm.newly_done_stage_instance_ids == frozenset()
        assert cm.newly_error_stage_instance_ids == frozenset()
        assert cm.newly_awaiting_confirm_ids == frozenset()
        assert cm.ready_candidates == []

    def test_with_done(self):
        cm = CycleMeta()
        cm2 = cm.with_done("s01")
        assert "s01" in cm2.newly_done_stage_instance_ids
        assert "s01" not in cm.newly_done_stage_instance_ids  # 原对象不变

    def test_with_error(self):
        cm = CycleMeta()
        cm2 = cm.with_error("s01_0")
        assert "s01_0" in cm2.newly_error_stage_instance_ids
        assert len(cm.newly_error_stage_instance_ids) == 0

    def test_with_awaiting_confirm(self):
        cm = CycleMeta()
        cm2 = cm.with_awaiting_confirm("s02")
        assert "s02" in cm2.newly_awaiting_confirm_ids

    def test_immutability(self):
        cm = CycleMeta()
        cm2 = cm.with_done("a").with_error("b").with_awaiting_confirm("c")
        assert cm2.newly_done_stage_instance_ids == frozenset(["a"])
        assert cm2.newly_error_stage_instance_ids == frozenset(["b"])
        assert cm2.newly_awaiting_confirm_ids == frozenset(["c"])
        # 原对象不受影响
        assert len(cm.newly_done_stage_instance_ids) == 0


class TestInstanceStateCycleMeta:
    def test_cycle_meta_default(self):
        """InstanceState 默认包含空 CycleMeta。"""
        inst = InstanceState(instance_id="test")
        assert isinstance(inst.cycle_meta, CycleMeta)
        assert inst.cycle_meta.newly_done_stage_instance_ids == frozenset()

    def test_cycle_meta_not_serialized(self):
        """to_dict() 不包含 cycle_meta 字段。"""
        inst = InstanceState(
            instance_id="test",
            cycle_meta=CycleMeta(
                newly_done_stage_instance_ids=frozenset(["a", "b"]),
            ),
        )
        d = inst.to_dict()
        assert "cycle_meta" not in d

    def test_cycle_meta_from_dict_initialized_empty(self):
        """from_dict() 始终初始化为空 CycleMeta。"""
        raw = {
            "schema_version": "3.0.0",
            "instance_id": "test",
            "workflow_id": "wf",
            "version": "1",
            "goal": "",
            "status": "ACTIVE",
            "consumed_message_ids": [],
            "stages": [],
            "cycle_meta": {  # 意外持久化——应被忽略
                "newly_done_stage_instance_ids": ["x"],
            },
        }
        inst = InstanceState.from_dict(raw)
        assert inst.cycle_meta == CycleMeta()

    def test_cycle_meta_preserved_in_apply_delta(self):
        """apply_delta 保留 cycle_meta（不重置）。"""
        inst = InstanceState(
            instance_id="test",
            cycle_meta=CycleMeta(newly_done_stage_instance_ids=frozenset(["s01"])),
        )
        inst2 = inst.apply_delta(StateDelta())
        assert "s01" in inst2.cycle_meta.newly_done_stage_instance_ids


class TestNewQueryMethods:
    def test_stages_by_id(self):
        inst = InstanceState(
            instance_id="test",
            stages=[
                StageState(stage_id="s01", stage_instance_id="s01"),
                StageState(stage_id="s01", stage_instance_id="s01_0", fan_out_target={"id": "a"}),
                StageState(stage_id="s02", stage_instance_id="s02"),
            ],
        )
        assert len(inst.stages_by_id("s01")) == 2
        assert len(inst.stages_by_id("s02")) == 1
        assert inst.stages_by_id("nonexistent") == []

    def test_first_stage_by_id(self):
        inst = InstanceState(
            instance_id="test",
            stages=[
                StageState(stage_id="s01", stage_instance_id="s01"),
                StageState(stage_id="s01", stage_instance_id="s01_0"),
            ],
        )
        found = inst.first_stage_by_id("s01")
        assert found is not None
        assert found.stage_instance_id == "s01"  # 第一条

    def test_first_stage_by_id_none(self):
        inst = InstanceState(instance_id="test")
        assert inst.first_stage_by_id("nonexistent") is None


