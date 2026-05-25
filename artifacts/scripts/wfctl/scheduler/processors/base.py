"""Processor 协议与基础类型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Protocol

from state.model import StateDelta
from scheduler.context import ExecutionContext
from state.model import InstanceState

# ── 副作用类型 ──

SideEffectKind = Literal[
    "file_read", "file_write", "file_delete",
    "git_read", "git_commit", "git_tag", "git_merge",
    "json_write", "deviation_write", "worktree_create",
]


@dataclass(frozen=True)
class SideEffect:
    """副作用描述符。

    - kind: 副作用类别
    - description: 人类可读描述
    - execute: 延迟执行 callable。None 表示 processor 已自行执行（链式副作用）
    - metadata: 附加上下文
    """

    kind: SideEffectKind
    description: str
    execute: Callable[[], Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProcessorResult:
    """Processor 执行结果。

    - state_delta: 对 InstanceState 的显式变更
    - actions: 生成的调度动作（spawn / continue / confirm / conflict / error 等）
    - side_effects: 副作用描述符（独立副作用延迟执行，链式副作用仅声明）
    """

    state_delta: StateDelta = field(default_factory=StateDelta)
    actions: list[dict] = field(default_factory=list)
    side_effects: list[SideEffect] = field(default_factory=list)

    def merge(self, other: ProcessorResult) -> ProcessorResult:
        """合并两个结果（用于累积）。"""
        return ProcessorResult(
            state_delta=self.state_delta.merge(other.state_delta),
            actions=self.actions + other.actions,
            side_effects=self.side_effects + other.side_effects,
        )


class Processor(Protocol):
    """Processor 协议。

    每个 Processor 是纯函数：接收 (ctx, state) → 返回 ProcessorResult。
    禁止直接修改 state（通过 StateDelta 描述变更）。
    """

    def process(self, ctx: ExecutionContext, state: InstanceState) -> ProcessorResult:
        ...
