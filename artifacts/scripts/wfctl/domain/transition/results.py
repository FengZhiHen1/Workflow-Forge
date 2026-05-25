"""状态转换结果类型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

from domain.workflow.spec import StageStatus

if TYPE_CHECKING:
    from state.model import StateDelta


@dataclass(frozen=True)
class TransitionResult:
    """单次状态转换结果（on_error 用）。

    next_status: 转换后的目标状态
    target_stage_id: 若需路由到其他 stage，目标 stage_id
    updates: 附加字段变更 (e.g., attempt_count increment)
    action: 触发动作 "retry" | "spawn" | "terminate" | ""
    """

    next_status: StageStatus
    target_stage_id: str | None = None
    updates: dict[str, Any] = field(default_factory=dict)
    action: str = ""


@dataclass(frozen=True)
class ConfirmResult:
    """确认/拒绝操作的纯决策结果。

    由 TransitionPolicy.on_confirm() 返回，不包含任何副作用。
    """

    next_status: StageStatus
    exit_condition: str = ""
    target_stage_id: str | None = None
    updates: dict[str, Any] = field(default_factory=dict)
    requires_feedback: bool = False
    cascade_reset_target: str | None = None
    action: str = ""
    reason: str = ""
    is_relay: bool = False
    is_rejected: bool = False
    instance_failed: bool = False

    @property
    def timeline_event_label(self) -> str:
        """纯决策：根据确认结果选择 timeline 事件名称。"""
        if self.exit_condition == "loop_exceeded":
            return "loop_exceeded"
        if self.is_rejected:
            return "awaiting_confirm→done"
        if self.exit_condition == "confirmed":
            return "awaiting_confirm→done"
        return "awaiting_confirm→pending"


@dataclass(frozen=True)
class CascadeResetResult:
    """回边级联重置结果。

    reset_stage_instance_ids: 需重置为 PENDING 的 stage_instance_id 列表
    removed_stage_instance_ids: 需移除的 stage_instance_id（折叠为单条）
    cleanup_running_agent_stage_ids: 需从 running_agents.json 清理的 stage_id 列表
    """

    reset_stage_instance_ids: list[str] = field(default_factory=list)
    removed_stage_instance_ids: list[str] = field(default_factory=list)
    cleanup_running_agent_stage_ids: list[str] = field(default_factory=list)

    def to_state_delta(self, spec_stages: list[Any]) -> Any:
        """纯决策：将级联重置结果转换为 StateDelta。"""
        from state.model import StageState, StageStatus, StateDelta
        spec_stage_map = {s.stage_id: s for s in spec_stages}
        append_stages: list[StageState] = []
        for sid in self.reset_stage_instance_ids:
            stage_spec = spec_stage_map.get(sid)
            append_stages.append(StageState(
                stage_id=sid,
                stage_instance_id=sid,
                status=StageStatus.PENDING,
                model=stage_spec.model if stage_spec else None,
            ))
        return StateDelta(
            remove_stage_instance_ids=self.removed_stage_instance_ids,
            append_stages=append_stages,
        )


@dataclass(frozen=True)
class RollbackResult:
    """回退操作的纯决策结果。"""

    reset_stage_ids: list[str] = field(default_factory=list)
    delta: Any = None  # StateDelta (lazy import)

    @property
    def state_delta(self) -> StateDelta:
        from state.model import StateDelta
        return self.delta if isinstance(self.delta, StateDelta) else StateDelta()


@dataclass(frozen=True)
class SkipResult:
    """跳过操作的纯决策结果。"""

    stage_instance_ids: list[str] = field(default_factory=list)
    force_applied: bool = False
    blocked_message_ids: list[str] = field(default_factory=list)
    delta: Any = None  # StateDelta (lazy import)

    @property
    def state_delta(self) -> StateDelta:
        from state.model import StateDelta
        return self.delta if isinstance(self.delta, StateDelta) else StateDelta()


@dataclass(frozen=True)
class MergeConfirmResult:
    """__merge__ 伪 stage 确认结果。"""

    merge_confirmed: bool = False
    remove_merge_stage: bool = True


@dataclass(frozen=True)
class SyncResult:
    """worktree 同步结果。

    success: 同步是否成功（无冲突）
    conflict_files: 冲突文件路径列表（success=False 时填充）
    """

    success: bool
    conflict_files: list[str] = field(default_factory=list)

    @property
    def message(self) -> str:
        """人类可读的冲突描述。"""
        if self.success:
            return ""
        return f"conflict files: {self.conflict_files}"
