"""confirm 回边级联重置单元测试——TransitionPolicy.compute_cascade_reset。"""

import pytest

from domain.workflow.spec import (
    EdgeCondition,
    EdgeSpec,
    StageSpec,
    StageTargetType,
    WorkflowSpec,
)
from domain.transition.policy import TransitionPolicy
from state.model import InstanceState, StageState, StageStatus


def _make_spec(num_stages: int = 10):
    """构造一个简单的工作流 spec，stage 按 s00→s09 排列。"""
    stages = [
        StageSpec(
            stage_id="s00-workflow-start",
            name="开始",
            target_type=StageTargetType.VIRTUAL,
            target=None,
            mandatory=False,
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


def _make_state(stage_statuses: dict[str, str]) -> InstanceState:
    """构造 InstanceState。"""
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


def _stage_order(spec: WorkflowSpec) -> list[str]:
    return [s.stage_id for s in spec.stages]


class TestCascadeReset:
    """compute_cascade_reset 单元测试。"""

    def test_forward_edge_no_reset(self):
        """前向边（to 在 from 之后）：不应触发重置。"""
        spec = _make_spec(10)
        state = _make_state({f"s0{i}": "DONE" for i in range(1, 10)})

        result = TransitionPolicy.compute_cascade_reset(
            state, "s05", "s08", _stage_order(spec)
        )

        assert len(result.reset_stage_instance_ids) == 0
        assert len(result.removed_stage_instance_ids) == 0

    def test_self_loop_no_reset(self):
        """自循环边（from == to）：不应触发级联重置。"""
        spec = _make_spec(10)
        state = _make_state({"s01": "DONE", "s02": "PENDING", "s03": "PENDING"})

        result = TransitionPolicy.compute_cascade_reset(
            state, "s02", "s02", _stage_order(spec)
        )

        assert len(result.reset_stage_instance_ids) == 0

    def test_s08_to_s06_backward_cascade(self):
        """s08→s06 回边：应重置 s06、s07、s08（含 from_stage）。"""
        spec = _make_spec(10)
        state = _make_state({f"s0{i}": "DONE" for i in range(1, 10)})

        result = TransitionPolicy.compute_cascade_reset(
            state, "s08", "s06", _stage_order(spec)
        )

        assert set(result.reset_stage_instance_ids) == {"s06", "s07", "s08"}
        assert all(sid in result.cleanup_running_agent_stage_ids for sid in ["s06", "s07", "s08"])

    def test_s08_to_s02_deep_backward_cascade(self):
        """s08→s02 深回边：应重置 s02–s08（含 from_stage）。"""
        spec = _make_spec(10)
        state = _make_state({f"s0{i}": "DONE" for i in range(1, 10)})

        result = TransitionPolicy.compute_cascade_reset(
            state, "s08", "s02", _stage_order(spec)
        )

        expected = {f"s0{i}" for i in range(2, 9)}
        assert set(result.reset_stage_instance_ids) == expected

    def test_pending_stages_not_affected(self):
        """PENDING stage 不应出现在重置列表中。"""
        spec = _make_spec(10)
        statuses = {f"s0{i}": "DONE" for i in range(1, 10)}
        statuses["s05"] = "PENDING"
        statuses["s06"] = "PENDING"
        state = _make_state(statuses)

        result = TransitionPolicy.compute_cascade_reset(
            state, "s08", "s06", _stage_order(spec)
        )

        # s07 和 s08 是 DONE，应重置；s05 和 s06 是 PENDING，不应重置
        assert "s07" in result.reset_stage_instance_ids
        assert "s08" in result.reset_stage_instance_ids
        assert "s05" not in result.reset_stage_instance_ids
        assert "s06" not in result.reset_stage_instance_ids

    def test_backward_collapses_parallel_instances(self):
        """回边重置应列出 parallel 实例的 removal。"""
        spec = _make_spec(10)
        state = _make_state({f"s0{i}": "DONE" for i in range(1, 10)})

        # 添加 parallel s07 实例
        parallel_stages = list(state.stages) + [
            StageState(
                stage_id="s07",
                stage_instance_id="s07_0",
                status=StageStatus.DONE,
                system_agent_id="sys-parallel-0",
                output_message_id="msg-parallel-0",
                fan_out_target={"id": "M01", "label": "模块1"},
            ),
            StageState(
                stage_id="s07",
                stage_instance_id="s07_1",
                status=StageStatus.DONE,
                system_agent_id="sys-parallel-1",
                output_message_id="msg-parallel-1",
                fan_out_target={"id": "M02", "label": "模块2"},
            ),
        ]
        state = InstanceState(
            instance_id="test-001",
            workflow_id="test",
            stages=parallel_stages,
        )

        result = TransitionPolicy.compute_cascade_reset(
            state, "s08", "s06", _stage_order(spec)
        )

        # s07 的 3 个实例（原始 + 2 个 parallel）都应被移除
        s07_removed = [sid for sid in result.removed_stage_instance_ids if sid.startswith("s07")]
        assert len(s07_removed) == 3, f"Expected 3 s07 removals, got {len(s07_removed)}"
        assert "s07" in result.reset_stage_instance_ids
