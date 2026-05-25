"""ExecutionContext：跨 Processor 共享的只读上下文。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from domain.dag.graph import AdjacencyList
from domain.workflow.spec import WorkflowSpec


@dataclass
class ExecutionContext:
    """调度执行上下文。

    在 _run_next_inner 入口处一次性构建，所有 Processor 共享。
    核心字段只读；extra 字典供 Processor 传递中间结果。
    """

    instance_id: str
    root: Path
    spec: WorkflowSpec
    adj: AdjacencyList
    worktree_map: dict[str, Path]
    extra: dict = field(default_factory=dict)
