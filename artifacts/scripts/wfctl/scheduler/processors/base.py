"""Processor 协议与基础类型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from state.model import StateDelta
from scheduler.context import ExecutionContext
from state.model import InstanceState


@dataclass
class ProcessorResult:
    """Processor 执行结果。

    - state_delta: 对 InstanceState 的显式变更
    - actions: 生成的调度动作（spawn / continue / confirm / conflict / error 等）
    - side_effects: 副作用描述（Phase 2 迁移时逐步引入）
    """

    state_delta: StateDelta = field(default_factory=StateDelta)
    actions: list[dict] = field(default_factory=list)

    def merge(self, other: ProcessorResult) -> ProcessorResult:
        """合并两个结果（用于累积）。"""
        return ProcessorResult(
            state_delta=self.state_delta.merge(other.state_delta),
            actions=self.actions + other.actions,
        )


class Processor(Protocol):
    """Processor 协议。

    每个 Processor 是纯函数：接收 (ctx, state) → 返回 ProcessorResult。
    禁止直接修改 state（通过 StateDelta 描述变更）。
    """

    def process(self, ctx: ExecutionContext, state: InstanceState) -> ProcessorResult:
        ...
