"""schema_version "3.0.0" 适配器。"""

from typing import Any

from core.errors import SchemaError
from core.schema.interface import (
    EdgeCondition,
    EdgeSpec,
    ParallelSpec,
    StageSpec,
    StageTargetType,
    WorkflowSpec,
)


class V3Adapter:
    """schema_version "3.0.0" 适配器"""

    def parse(self, raw: dict[str, Any]) -> WorkflowSpec:
        # 校验必填字段
        required_top = ["schema_version", "workflow_id", "version", "max_parallel_agents", "stages", "edges"]
        for key in required_top:
            if key not in raw:
                raise SchemaError(f"Missing required top-level field: {key}", code="SCHEMA_VALIDATION_ERROR")

        stages: list[StageSpec] = []
        for idx, s in enumerate(raw["stages"]):
            stages.append(self._parse_stage(s, idx))

        edges: list[EdgeSpec] = []
        for idx, e in enumerate(raw["edges"]):
            edges.append(self._parse_edge(e, idx))

        return WorkflowSpec(
            schema_version=str(raw["schema_version"]),
            workflow_id=str(raw["workflow_id"]),
            version=str(raw["version"]),
            max_parallel_agents=int(raw["max_parallel_agents"]),
            anchor_prefix=str(raw.get("anchor_prefix", "wf")),
            stages=stages,
            edges=edges,
        )

    def _parse_stage(self, raw: dict[str, Any], idx: int) -> StageSpec:
        stage_id = str(raw.get("stage_id", ""))
        if not stage_id:
            raise SchemaError(f"Stage {idx}: missing stage_id", code="SCHEMA_VALIDATION_ERROR")

        name = str(raw.get("name", ""))

        # 虚拟 stage
        if stage_id in ("s00-workflow-start", "s99-workflow-end"):
            return StageSpec(
                stage_id=stage_id,
                name=name or stage_id,
                target_type=StageTargetType.VIRTUAL,
                target=None,
                mandatory=False,
                confirmation_point=False,
            )

        target_type: StageTargetType
        target: str | None = None
        if "skill_id" in raw:
            target_type = StageTargetType.SKILL
            target = str(raw["skill_id"])
        elif "workflow" in raw:
            target_type = StageTargetType.WORKFLOW
            target = str(raw["workflow"])
        else:
            raise SchemaError(f"Stage {stage_id}: must specify one of skill_id or workflow", code="SCHEMA_VALIDATION_ERROR")

        parallel = None
        if "parallel" in raw and raw["parallel"]:
            p = raw["parallel"]
            if "source" not in p:
                raise SchemaError(f"Stage {stage_id}: parallel.source is required", code="SCHEMA_VALIDATION_ERROR")
            parallel = ParallelSpec(
                source=str(p["source"]),
                max_instances=int(p["max_instances"]) if "max_instances" in p else None,
            )

        return StageSpec(
            stage_id=stage_id,
            name=name,
            target_type=target_type,
            target=target,
            mandatory=bool(raw.get("mandatory", True)),
            confirmation_point=bool(raw.get("confirmation_point", False)),
            retry=int(raw.get("retry", 0)),
            timeout_seconds=int(raw["timeout_seconds"]) if "timeout_seconds" in raw else None,
            model=str(raw["model"]) if "model" in raw else None,
            exclusive=bool(raw.get("exclusive", False)),
            parallel=parallel,
        )

    def _parse_edge(self, raw: dict[str, Any], idx: int) -> EdgeSpec:
        required = ["from", "to", "condition"]
        for key in required:
            if key not in raw:
                raise SchemaError(f"Edge {idx}: missing required field '{key}'", code="SCHEMA_VALIDATION_ERROR")

        condition_str = str(raw["condition"]).lower()
        try:
            condition = EdgeCondition(condition_str)
        except ValueError:
            raise SchemaError(f"Edge {idx}: unknown condition '{condition_str}'", code="SCHEMA_VALIDATION_ERROR")

        from_stage = str(raw["from"])
        to_stage = str(raw["to"])
        is_self_loop = from_stage == to_stage

        if condition in (EdgeCondition.FAILURE, EdgeCondition.LOOP_EXCEEDED):
            max_loop = int(raw["max_loop"]) if "max_loop" in raw else None
        elif condition == EdgeCondition.CONFIRMED and is_self_loop:
            max_loop = int(raw["max_loop"]) if "max_loop" in raw else None
        else:
            max_loop = None

        loop_counter_stage = str(raw["loop_counter_stage"]) if "loop_counter_stage" in raw else None

        return EdgeSpec(
            from_stage=from_stage,
            to_stage=to_stage,
            condition=condition,
            max_loop=max_loop,
            loop_counter_stage=loop_counter_stage,
            choice=str(raw["choice"]) if "choice" in raw else None,
            aggregation=str(raw.get("aggregation", "all")),
        )
