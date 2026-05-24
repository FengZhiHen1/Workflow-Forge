"""调度器重构包。

Phase 1：建立新包结构 + 状态模型，保持外部接口兼容。
原 scheduler.py 已迁移为 scheduler_legacy.py，本 __init__.py 负责向后兼容导出。
"""

# 向后兼容：外部 import services.scheduler 时，透明代理到 scheduler_legacy
from services.scheduler_legacy import run_next, run_sync

# 新包导出
from services.scheduler.state_model import InstanceState, StageState, StateDelta
from services.scheduler.context import ExecutionContext

__all__ = [
    "run_next",
    "run_sync",
    "InstanceState",
    "StageState",
    "StateDelta",
    "ExecutionContext",
]
