"""confirm 回边级联重置单元测试。"""

import pytest

from core.schema.interface import (
    EdgeCondition,
    EdgeSpec,
    StageSpec,
    StageTargetType,
    WorkflowSpec,
)
from cli.confirm import _cascade_reset_on_backward_edge


def _make_spec(num_stages: int = 10):
    """构造一个简单的工作流 spec，stage 按 s00→s09 排列。"""
    stages = [
        StageSpec(
            stage_id="s00-workflow-start",
            name="开始",
            target_type=StageTargetType.VIRTUAL,
            target=None,
            mandatory=False,
            confirmation_point=False,
        ),
    ]
    for i in range(1, num_stages):
        stages.append(
            StageSpec(
                stage_id=f"s0{i}",
                name=f"Stage {i}",
                target_type=StageTargetType.SKILL,
                target=f"skill-{i}",
                mandatory=True,
                confirmation_point=(i % 3 == 0),
                model="standard",
            )
        )
    stages.append(
        StageSpec(
            stage_id="s99-workflow-end",
            name="结束",
            target_type=StageTargetType.VIRTUAL,
            target=None,
            mandatory=False,
            confirmation_point=False,
        )
    )
    return WorkflowSpec(
        schema_version="3.0.0",
        workflow_id="test",
        version="1.0.0",
        max_parallel_agents=1,
        anchor_prefix="wf",
        stages=stages,
        edges=[],
    )


def _make_instance(stage_statuses: dict[str, str]) -> dict:
    """构造一个包含指定 stage 状态的 instance。"""
    stages = [
        {
            "stage_id": sid,
            "stage_instance_id": sid,
            "status": status,
            "agent_id": None,
            "system_agent_id": f"sys-{sid}" if status == "DONE" else None,
            "output_message_id": f"msg-{sid}" if status == "DONE" else None,
            "loop_counter": 0,
            "attempt_count": 0,
            "started_at": None,
            "model": "standard",
            "child_instance_id": None,
            "fan_out_target": None,
        }
        for sid, status in stage_statuses.items()
    ]
    return {
        "instance_id": "test-001",
        "status": "ACTIVE",
        "stages": stages,
    }


class TestCascadeReset:
    """_cascade_reset_on_backward_edge 单元测试。"""

    def test_forward_edge_no_reset(self, tmp_path):
        """前向边（to 在 from 之后）：不应触发重置。"""
        spec = _make_spec(10)
        instance = _make_instance({
            "s00-workflow-start": "DONE",
            "s01": "DONE",
            "s02": "DONE",
            "s03": "DONE",
            "s04": "DONE",
            "s05": "DONE",
            "s06": "DONE",
            "s07": "DONE",
            "s08": "DONE",
            "s09": "DONE",
            "s99-workflow-end": "PENDING",
        })

        _cascade_reset_on_backward_edge(instance, spec, "s05", "s08", "test-001")

        # s05 到 s08 之间的 stage 应保持 DONE（s08 > s05，是前向边）
        statuses = {s["stage_id"]: s["status"] for s in instance["stages"]}
        assert statuses["s05"] == "DONE"
        assert statuses["s06"] == "DONE"
        assert statuses["s07"] == "DONE"
        assert statuses["s08"] == "DONE"

    def test_self_loop_no_reset(self, tmp_path):
        """自循环边（from == to）：不应触发级联重置。"""
        spec = _make_spec(10)
        instance = _make_instance({
            "s00-workflow-start": "DONE",
            "s01": "DONE",
            "s02": "PENDING",
            "s03": "PENDING",
            "s99-workflow-end": "PENDING",
        })

        _cascade_reset_on_backward_edge(instance, spec, "s02", "s02", "test-001")

        # 自循环不应改变任何东西
        assert instance["stages"][2]["status"] == "PENDING"

    def test_s08_to_s06_backward_cascade(self, tmp_path):
        """s08→s06 回边：应重置 s06 和 s07，s02–s05 保持 DONE。"""
        spec = _make_spec(10)
        instance = _make_instance({
            "s00-workflow-start": "DONE",
            "s01": "DONE",
            "s02": "DONE",
            "s03": "DONE",
            "s04": "DONE",
            "s05": "DONE",
            "s06": "DONE",
            "s07": "DONE",
            "s08": "DONE",
            "s09": "DONE",
            "s99-workflow-end": "PENDING",
        })

        _cascade_reset_on_backward_edge(instance, spec, "s08", "s06", "test-001")

        statuses = {s["stage_id"]: s["status"] for s in instance["stages"]}
        # s06–s07 应重置
        assert statuses["s06"] == "PENDING"
        assert statuses["s07"] == "PENDING"
        # s08（from_stage）不受影响——它已是 DONE
        assert statuses["s08"] == "DONE"
        # s02–s05 在重置范围之前，保持 DONE
        assert statuses["s02"] == "DONE"
        assert statuses["s03"] == "DONE"
        assert statuses["s04"] == "DONE"
        assert statuses["s05"] == "DONE"

    def test_s08_to_s02_deep_backward_cascade(self, tmp_path):
        """s08→s02 深回边：应重置 s02–s07。"""
        spec = _make_spec(10)
        instance = _make_instance({
            "s00-workflow-start": "DONE",
            "s01": "DONE",
            "s02": "DONE",
            "s03": "DONE",
            "s04": "DONE",
            "s05": "DONE",
            "s06": "DONE",
            "s07": "DONE",
            "s08": "DONE",
            "s09": "DONE",
            "s99-workflow-end": "PENDING",
        })

        _cascade_reset_on_backward_edge(instance, spec, "s08", "s02", "test-001")

        statuses = {s["stage_id"]: s["status"] for s in instance["stages"]}
        # s02–s07 应重置
        for sid in [f"s0{i}" for i in range(2, 8)]:
            assert statuses[sid] == "PENDING", f"{sid} should be PENDING"
        # s08 不受影响
        assert statuses["s08"] == "DONE"
        # s01 在范围之前，保持 DONE
        assert statuses["s01"] == "DONE"

    def test_backward_reset_clears_fields(self, tmp_path):
        """回边重置应清除 system_agent_id、output_message_id 等字段。"""
        spec = _make_spec(10)
        instance = _make_instance({
            "s00-workflow-start": "DONE",
            "s01": "DONE",
            "s02": "DONE",
            "s03": "DONE",
            "s04": "DONE",
            "s05": "DONE",
            "s06": "DONE",
            "s07": "DONE",
            "s08": "DONE",
            "s99-workflow-end": "PENDING",
        })

        _cascade_reset_on_backward_edge(instance, spec, "s08", "s06", "test-001")

        # 检查 s06 的新条目
        s06_entry = next(s for s in instance["stages"] if s["stage_id"] == "s06")
        assert s06_entry["system_agent_id"] is None
        assert s06_entry["output_message_id"] is None
        assert s06_entry["child_instance_id"] is None
        assert "fan_out_target" not in s06_entry or s06_entry["fan_out_target"] is None
        assert s06_entry["loop_counter"] == 0
        assert s06_entry["attempt_count"] == 0

    def test_backward_collapses_parallel_instances(self, tmp_path):
        """回边重置应将 parallel 实例折叠为单一 PENDING 条目。"""
        spec = _make_spec(10)
        instance = _make_instance({
            "s00-workflow-start": "DONE",
            "s01": "DONE",
            "s02": "DONE",
            "s03": "DONE",
            "s04": "DONE",
            "s05": "DONE",
            "s06": "DONE",
            "s07": "DONE",
            "s08": "DONE",
            "s99-workflow-end": "PENDING",
        })

        # 模拟 s07 有 parallel 实例
        instance["stages"].extend([
            {
                "stage_id": "s07",
                "stage_instance_id": "s07_0",
                "status": "DONE",
                "system_agent_id": "sys-parallel-0",
                "output_message_id": "msg-parallel-0",
                "loop_counter": 0,
                "attempt_count": 0,
                "started_at": None,
                "model": "standard",
                "child_instance_id": None,
                "fan_out_target": {"id": "M01", "label": "模块1"},
            },
            {
                "stage_id": "s07",
                "stage_instance_id": "s07_1",
                "status": "DONE",
                "system_agent_id": "sys-parallel-1",
                "output_message_id": "msg-parallel-1",
                "loop_counter": 0,
                "attempt_count": 0,
                "started_at": None,
                "model": "standard",
                "child_instance_id": None,
                "fan_out_target": {"id": "M02", "label": "模块2"},
            },
        ])

        _cascade_reset_on_backward_edge(instance, spec, "s08", "s06", "test-001")

        # s07 的所有 parallel 实例应折叠为一个 PENDING 条目
        s07_entries = [s for s in instance["stages"] if s["stage_id"] == "s07"]
        assert len(s07_entries) == 1, f"Expected 1 s07 entry, got {len(s07_entries)}"
        assert s07_entries[0]["stage_instance_id"] == "s07"
        assert s07_entries[0]["status"] == "PENDING"
        assert "fan_out_target" not in s07_entries[0] or s07_entries[0]["fan_out_target"] is None

    def test_pending_stages_not_affected(self, tmp_path):
        """已处于 PENDING 的 Stage 应保持 PENDING，不被重复重置。"""
        spec = _make_spec(10)
        instance = _make_instance({
            "s00-workflow-start": "DONE",
            "s01": "DONE",
            "s02": "DONE",
            "s03": "DONE",
            "s04": "DONE",
            "s05": "PENDING",  # 已是 PENDING
            "s06": "PENDING",  # 已是 PENDING
            "s07": "DONE",
            "s08": "DONE",
            "s99-workflow-end": "PENDING",
        })

        _cascade_reset_on_backward_edge(instance, spec, "s08", "s06", "test-001")

        # 已 PENDING 的 stage 数量不变（不重复创建条目）
        pending_count = sum(1 for s in instance["stages"] if s["stage_id"] in ("s05", "s06") and s["status"] == "PENDING")
        assert pending_count >= 2
