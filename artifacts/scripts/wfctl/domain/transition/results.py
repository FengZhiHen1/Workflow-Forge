"""状态转换结果类型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.schema.interface import StageStatus


@dataclass(frozen=True)
class TransitionResult:
    """单次状态转换结果。

    next_status: 转换后的目标状态
    target_stage_id: 若需路由到其他 stage，目标 stage_id
    updates: 附加字段变更 (e.g., attempt_count increment)
    action: 触发动作 "retry" | "spawn" | "terminate" | ""
    """

    next_status: StageStatus
    target_stage_id: str | None = None
    updates: dict[str, Any] = field(default_factory=dict)
    action: str = ""
