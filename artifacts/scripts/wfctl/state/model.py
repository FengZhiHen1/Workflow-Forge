"""不可变状态模型：InstanceState / StageState / StateDelta。

严格兼容现有 instance.json 的序列化格式。
关键设计：stages 用 list[StageState] 存储，以兼容 parallel 拆分产生的
同 stage_id、不同 stage_instance_id 的多条记录。
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field, asdict, replace
from typing import Any, ClassVar

from domain.workflow.spec import StageStatus, InstanceStatus


@dataclass(frozen=True)
class StageState:
    """单个 stage 的不可变状态。"""

    stage_id: str
    stage_instance_id: str
    status: StageStatus = StageStatus.PENDING
    agent_id: str | None = None
    system_agent_id: str | None = None
    output_message_id: str | None = None
    loop_counter: int = 0
    attempt_count: int = 0
    started_at: str | None = None
    model: str | None = None
    child_instance_id: str | None = None
    fan_out_target: dict | None = None
    exit_condition: str = ""
    routing_choice: str = ""
    pending_choice: str = ""  # 最近一次 confirm 的用户选择，供 continue prompt 注入
    valid_routing_choices: list[str] = field(default_factory=list)
    requires_parallel_targets: bool = False
    conflict_files: list[str] = field(default_factory=list)
    continued_to: str | None = None
    parallel_retry_count: int = 0
    confirm_questions: list[str] = field(default_factory=list)

    def replace(self, **changes: Any) -> StageState:
        """返回应用变更后的新 StageState。"""
        return replace(self, **changes)

    def to_dict(self) -> dict[str, Any]:
        """序列化为兼容 instance.json stage 条目的 dict。"""
        d = asdict(self)
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StageState:
        """从 instance.json stage 条目解析。

        静默跳过旧数据中的幽灵字段（如 confirmed）和未知字段。
        """
        import dataclasses as dc

        raw = dict(data)
        # 跳过已废弃的字段
        raw.pop("confirmed", None)
        raw.pop("confirmed_choice", None)
        raw.pop("confirmation_point", None)

        # 过滤未知字段，防止传入 cls(**raw) 时触发 TypeError
        valid_fields = set(cls.__dataclass_fields__.keys())
        raw = {k: v for k, v in raw.items() if k in valid_fields}

        status_str = raw.pop("status", "PENDING")
        raw["status"] = StageStatus(status_str) if isinstance(status_str, str) else status_str
        missing: list[str] = []
        for f_name, f_def in cls.__dataclass_fields__.items():
            if f_name not in raw:
                if f_def.default is not dc.MISSING:
                    raw[f_name] = f_def.default
                elif f_def.default_factory is not dc.MISSING:
                    raw[f_name] = f_def.default_factory()
                else:
                    missing.append(f_name)
        if missing:
            from infrastructure.errors import StateError
            raise StateError(
                f"StageState.from_dict missing required fields: {missing}",
                code="STATE_CORRUPTED",
            )
        return cls(**raw)


@dataclass(frozen=True)
class CycleMeta:
    """单次调度循环的差分追踪（不持久化到 instance.json）。

    由 Processor 流水线中的各个 Processor 填充，在循环结束时
    由 StateTransitionProcessor 消费并应用到 stages。

    字段:
        newly_done_stage_instance_ids: 本轮新完成的 stage
        newly_error_stage_instance_ids: 本轮新出错的 stage
        newly_awaiting_confirm_ids: 本轮新进入等待确认的 stage
        ready_candidates: (stage_id, stage_instance_id) 就绪候选列表
        child_confirm_pending: 子工作流中的确认挂起项
    """

    newly_done_stage_instance_ids: frozenset[str] = field(default_factory=frozenset)
    newly_error_stage_instance_ids: frozenset[str] = field(default_factory=frozenset)
    newly_awaiting_confirm_ids: frozenset[str] = field(default_factory=frozenset)
    ready_candidates: list[tuple[str, str]] = field(default_factory=list)
    child_confirm_pending: list[dict] = field(default_factory=list)

    def with_done(self, stage_instance_id: str) -> "CycleMeta":
        """记录新增完成 stage，返回新 CycleMeta。"""
        return replace(
            self,
            newly_done_stage_instance_ids=self.newly_done_stage_instance_ids | {stage_instance_id},
        )

    def with_error(self, stage_instance_id: str) -> "CycleMeta":
        """记录新增错误 stage，返回新 CycleMeta。"""
        return replace(
            self,
            newly_error_stage_instance_ids=self.newly_error_stage_instance_ids | {stage_instance_id},
        )

    def with_awaiting_confirm(self, stage_instance_id: str) -> "CycleMeta":
        """记录新增等待确认 stage，返回新 CycleMeta。"""
        return replace(
            self,
            newly_awaiting_confirm_ids=self.newly_awaiting_confirm_ids | {stage_instance_id},
        )


@dataclass(frozen=True)
class StateDelta:
    """显式状态变更描述。

    - stage_updates: stage_instance_id → {字段: 新值}
    - instance_updates: 顶层 instance 字段变更
    - append_stages: 新增 stage（parallel 拆分用）
    - remove_stage_instance_ids: 移除 stage（按 stage_instance_id）
    """

    stage_updates: dict[str, dict[str, Any]] = field(default_factory=dict)
    instance_updates: dict[str, Any] = field(default_factory=dict)
    append_stages: list[StageState] = field(default_factory=list)
    remove_stage_instance_ids: list[str] = field(default_factory=list)
    cycle_meta: CycleMeta | None = None

    def is_empty(self) -> bool:
        return (
            not self.stage_updates
            and not self.instance_updates
            and not self.append_stages
            and not self.remove_stage_instance_ids
        )

    def merge(self, other: StateDelta) -> StateDelta:
        """合并两个 Delta（other 覆盖 self 的同名字段）。"""
        merged_stage = dict(self.stage_updates)
        for sid, updates in other.stage_updates.items():
            if sid in merged_stage:
                merged_stage[sid] = {**merged_stage[sid], **updates}
            else:
                merged_stage[sid] = dict(updates)
        return StateDelta(
            stage_updates=merged_stage,
            instance_updates={**self.instance_updates, **other.instance_updates},
            append_stages=self.append_stages + other.append_stages,
            remove_stage_instance_ids=self.remove_stage_instance_ids + other.remove_stage_instance_ids,
            cycle_meta=other.cycle_meta if other.cycle_meta is not None else self.cycle_meta,
        )


@dataclass(frozen=True)
class InstanceState:
    """实例的不可变状态。

    stages 用 list[StageState] 存储，以兼容 parallel 拆分产生的多条同 stage_id 记录。
    """

    _TRANSIENT_FIELDS: ClassVar[frozenset[str]] = frozenset({"cycle_meta"})

    schema_version: str = "3.0.0"
    instance_id: str = ""
    workflow_id: str = ""
    version: str = ""
    goal: str = ""
    status: InstanceStatus = InstanceStatus.ACTIVE
    parent_instance_id: str | None = None
    merge_confirmed: bool = False
    consumed_message_ids: frozenset[str] = field(default_factory=frozenset)
    stages: list[StageState] = field(default_factory=list)
    cycle_meta: CycleMeta = field(default_factory=lambda: CycleMeta())

    # ── 查询辅助 ──

    def stage_by_id(self, stage_id: str) -> StageState | None:
        """按 stage_id 查找（同 id 取最后一条）。"""
        result: StageState | None = None
        for s in self.stages:
            if s.stage_id == stage_id:
                result = s
        return result

    def stage_by_instance_id(self, stage_instance_id: str) -> StageState | None:
        """按 stage_instance_id 精确查找。"""
        for s in self.stages:
            if s.stage_instance_id == stage_instance_id:
                return s
        return None

    def stages_with_id(self, stage_id: str) -> list[StageState]:
        """返回所有匹配 stage_id 的 stage（用于 parallel 场景）。"""
        return [s for s in self.stages if s.stage_id == stage_id]

    def stages_by_id(self, stage_id: str) -> list[StageState]:
        """返回所有匹配 stage_id 的 stage（用于 parallel 场景）。

        这是 stages_with_id() 的推荐名称。
        """
        return self.stages_with_id(stage_id)

    def first_stage_by_id(self, stage_id: str) -> StageState | None:
        """返回第一个匹配 stage_id 的 stage（保持 stages 插入顺序）。"""
        for s in self.stages:
            if s.stage_id == stage_id:
                return s
        return None

    # ── 变更操作 ──

    def with_stage(self, stage_instance_id: str, **changes: Any) -> InstanceState:
        """返回更新单个 stage（按 stage_instance_id）后的新 InstanceState。"""
        new_stages = []
        found = False
        for s in self.stages:
            if s.stage_instance_id == stage_instance_id:
                new_stages.append(s.replace(**changes))
                found = True
            else:
                new_stages.append(s)
        if not found:
            raise KeyError(f"Stage with stage_instance_id={stage_instance_id} not found")
        return replace(self, stages=new_stages)

    def apply_delta(self, delta: StateDelta) -> InstanceState:
        """应用 StateDelta，返回新 InstanceState。"""
        new_stages = list(self.stages)

        # 移除 stage（按 stage_instance_id）
        remove_set = set(delta.remove_stage_instance_ids)
        new_stages = [s for s in new_stages if s.stage_instance_id not in remove_set]

        # 新增 stage
        new_stages.extend(delta.append_stages)

        # 更新 stage 字段（按 stage_instance_id）
        updated_stages = []
        for s in new_stages:
            if s.stage_instance_id in delta.stage_updates:
                updated_stages.append(s.replace(**delta.stage_updates[s.stage_instance_id]))
            else:
                updated_stages.append(s)
        new_stages = updated_stages

        # 顶层 instance 字段更新
        instance_changes = dict(delta.instance_updates)
        # 确保 frozenset 类型正确
        if "consumed_message_ids" in instance_changes and isinstance(instance_changes["consumed_message_ids"], (list, set)):
            instance_changes["consumed_message_ids"] = frozenset(instance_changes["consumed_message_ids"])

        if delta.cycle_meta is not None:
            instance_changes["cycle_meta"] = delta.cycle_meta

        return replace(self, stages=new_stages, **instance_changes)

    def replace(self, **changes: Any) -> InstanceState:
        """返回应用顶层变更后的新 InstanceState。"""
        return replace(self, **changes)

    # ── 序列化 ──

    def to_dict(self) -> dict[str, Any]:
        """序列化为兼容 instance.json 的 dict。"""
        return {
            "schema_version": self.schema_version,
            "instance_id": self.instance_id,
            "workflow_id": self.workflow_id,
            "version": self.version,
            "goal": self.goal,
            "status": self.status.value,
            "parent_instance_id": self.parent_instance_id,
            "merge_confirmed": self.merge_confirmed,
            "consumed_message_ids": sorted(self.consumed_message_ids),
            "stages": [s.to_dict() for s in self.stages],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> InstanceState:
        """从 instance.json dict 解析。"""
        raw = dict(data)
        status_str = raw.pop("status", "ACTIVE")
        status = InstanceStatus(status_str) if isinstance(status_str, str) else status_str

        consumed = raw.pop("consumed_message_ids", [])
        if isinstance(consumed, (list, set)):
            consumed = frozenset(consumed)

        stages_list = raw.pop("stages", [])
        stages = [StageState.from_dict(s) for s in stages_list]

        # 安全防护：忽略任何意外持久化的 cycle_meta
        raw.pop("cycle_meta", None)

        return cls(
            schema_version=raw.get("schema_version", "3.0.0"),
            instance_id=raw.get("instance_id", ""),
            workflow_id=raw.get("workflow_id", ""),
            version=raw.get("version", ""),
            goal=raw.get("goal", ""),
            status=status,
            parent_instance_id=raw.get("parent_instance_id"),
            merge_confirmed=raw.get("merge_confirmed", False),
            consumed_message_ids=consumed,
            stages=stages,
        )
