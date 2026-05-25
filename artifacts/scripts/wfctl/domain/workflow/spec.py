"""WorkflowSpec / StageSpec / EdgeSpec 内部规范表示。"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class StageStatus(Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    AWAITING_CONFIRM = "AWAITING_CONFIRM"
    DONE = "DONE"
    ERROR = "ERROR"
    CONFLICT = "CONFLICT"


class InstanceStatus(Enum):
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class EdgeCondition(Enum):
    ALWAYS = "always"
    SUCCESS = "success"
    FAILURE = "failure"
    LOOP_EXCEEDED = "loop_exceeded"


class StageTargetType(Enum):
    SKILL = "skill"
    WORKFLOW = "workflow"
    VIRTUAL = "virtual"


@dataclass
class ParallelSpec:
    source: str              # 上游 stage_id
    max_instances: Optional[int] = None


@dataclass
class StageSpec:
    stage_id: str
    name: str
    target_type: StageTargetType
    target: Optional[str] = None       # skill_id 或 workflow 引用
    mandatory: bool = True
    retry: int = 0                     # 默认 0
    timeout_seconds: Optional[int] = None
    model: Optional[str] = None
    exclusive: bool = False
    parallel: Optional[ParallelSpec] = None


@dataclass
class EdgeSpec:
    from_stage: str
    to_stage: str
    condition: EdgeCondition
    max_loop: Optional[int] = None
    cascade_reset_until: Optional[str] = None
    choice: Optional[str] = None
    aggregation: str = "all"           # "all" | "any"


@dataclass
class WorkflowSpec:
    schema_version: str
    workflow_id: str
    version: str
    max_parallel_agents: int
    anchor_prefix: str = "wf"
    stages: list[StageSpec] = field(default_factory=list)
    edges: list[EdgeSpec] = field(default_factory=list)
