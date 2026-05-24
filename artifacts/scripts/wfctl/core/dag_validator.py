"""DAG 静态分析器：检测工作流定义中的结构问题。"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.dag import AdjacencyList, build_adjacency
from core.schema.interface import EdgeCondition, StageTargetType, WorkflowSpec


@dataclass
class ValidationIssue:
    """验证问题描述。"""

    category: str
    message: str
    stage_id: str | None = None
    edge_from: str | None = None
    edge_to: str | None = None


@dataclass
class ValidationResult:
    """验证结果。"""

    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return len(self.issues) > 0

    def by_category(self, category: str) -> list[ValidationIssue]:
        return [i for i in self.issues if i.category == category]


def validate_workflow(spec: WorkflowSpec) -> ValidationResult:
    """验证 WorkflowSpec 的结构完整性。

    检测：
    - 死锁（不可达 stage）
    - 边指向不存在的 stage
    - confirmation_point 无 confirmed 边
    - 自环无 max_loop
    - parallel.source 不存在
    - 无入口边
    """
    adj = build_adjacency(spec)
    issues: list[ValidationIssue] = []

    issues.extend(_check_unreachable_stages(spec, adj))
    issues.extend(_check_dangling_edges(spec, adj))
    issues.extend(_check_confirmation_gaps(spec, adj))
    issues.extend(_check_unbounded_loops(spec, adj))
    issues.extend(_check_parallel_sources(spec))
    issues.extend(_check_missing_entry(spec, adj))

    return ValidationResult(issues=issues)


def _check_unreachable_stages(spec: WorkflowSpec, adj: AdjacencyList) -> list[ValidationIssue]:
    """检测从起始虚拟 stage 不可达的 stage。"""
    issues: list[ValidationIssue] = []

    # 找到起始虚拟 stage
    start_stages = [s.stage_id for s in spec.stages if s.target_type == StageTargetType.VIRTUAL and "start" in s.stage_id]
    if not start_stages:
        start_stages = [s.stage_id for s in spec.stages if s.target_type == StageTargetType.VIRTUAL]
    if not start_stages:
        # 无虚拟 stage 时，找所有没有入边的 stage
        start_stages = [sid for sid in adj.stages if not adj.incoming.get(sid)]

    if not start_stages:
        return [ValidationIssue("MISSING_ENTRY", "未找到工作流入口 stage")]

    # BFS 遍历所有可达 stage
    reachable: set[str] = set()
    queue = list(start_stages)
    while queue:
        current = queue.pop(0)
        if current in reachable:
            continue
        reachable.add(current)
        for edge in adj.outgoing.get(current, []):
            if edge.to_stage not in reachable:
                queue.append(edge.to_stage)

    # 检查不可达 stage
    for stage in spec.stages:
        if stage.stage_id not in reachable and stage.target_type != StageTargetType.VIRTUAL:
            issues.append(ValidationIssue(
                "UNREACHABLE_STAGE",
                f"Stage '{stage.stage_id}' 从入口不可达",
                stage_id=stage.stage_id,
            ))

    return issues


def _check_dangling_edges(spec: WorkflowSpec, adj: AdjacencyList) -> list[ValidationIssue]:
    """检测边指向不存在的 stage。"""
    issues: list[ValidationIssue] = []
    stage_ids = {s.stage_id for s in spec.stages}

    for edge in spec.edges:
        if edge.from_stage not in stage_ids:
            issues.append(ValidationIssue(
                "DANGLING_EDGE",
                f"Edge from '{edge.from_stage}' 指向不存在的 source stage",
                edge_from=edge.from_stage,
                edge_to=edge.to_stage,
            ))
        if edge.to_stage not in stage_ids:
            issues.append(ValidationIssue(
                "DANGLING_EDGE",
                f"Edge to '{edge.to_stage}' 指向不存在的 target stage",
                edge_from=edge.from_stage,
                edge_to=edge.to_stage,
            ))

    return issues


def _check_confirmation_gaps(spec: WorkflowSpec, adj: AdjacencyList) -> list[ValidationIssue]:
    """检测 confirmation_point=true 但无 confirmed 出边的 stage。"""
    issues: list[ValidationIssue] = []
    for stage in spec.stages:
        if not stage.confirmation_point:
            continue
        confirmed_edges = [e for e in adj.outgoing.get(stage.stage_id, []) if e.condition == EdgeCondition.CONFIRMED]
        if not confirmed_edges:
            issues.append(ValidationIssue(
                "CONFIRMATION_GAP",
                f"Stage '{stage.stage_id}' 设置了 confirmation_point 但无 confirmed 出边",
                stage_id=stage.stage_id,
            ))
    return issues


def _check_unbounded_loops(spec: WorkflowSpec, adj: AdjacencyList) -> list[ValidationIssue]:
    """检测自环 edge 无 max_loop 限制。"""
    issues: list[ValidationIssue] = []
    for edge in spec.edges:
        if edge.from_stage == edge.to_stage and edge.condition in (EdgeCondition.FAILURE, EdgeCondition.CONFIRMED):
            if edge.max_loop is None or edge.max_loop <= 0:
                issues.append(ValidationIssue(
                    "UNBOUNDED_LOOP",
                    f"Stage '{edge.from_stage}' 的自环 {edge.condition.value} 边缺少 max_loop 限制",
                    stage_id=edge.from_stage,
                    edge_from=edge.from_stage,
                    edge_to=edge.to_stage,
                ))
    return issues


def _check_parallel_sources(spec: WorkflowSpec) -> list[ValidationIssue]:
    """检测 parallel.source 指向不存在的 stage。"""
    issues: list[ValidationIssue] = []
    stage_ids = {s.stage_id for s in spec.stages}
    for stage in spec.stages:
        if stage.parallel:
            if stage.parallel.source not in stage_ids:
                issues.append(ValidationIssue(
                    "PARALLEL_SOURCE_MISSING",
                    f"Stage '{stage.stage_id}' 的 parallel.source '{stage.parallel.source}' 不存在",
                    stage_id=stage.stage_id,
                ))
    return issues


def _check_missing_entry(spec: WorkflowSpec, adj: AdjacencyList) -> list[ValidationIssue]:
    """检测是否有 ALWAYS 边连接到起始虚拟 stage。"""
    issues: list[ValidationIssue] = []
    start_stages = [s.stage_id for s in spec.stages if s.target_type == StageTargetType.VIRTUAL and "start" in s.stage_id]
    if not start_stages:
        start_stages = [s.stage_id for s in spec.stages if s.target_type == StageTargetType.VIRTUAL]

    for start_id in start_stages:
        incoming_always = [
            e for e in adj.incoming.get(start_id, [])
            if e.condition == EdgeCondition.ALWAYS
        ]
        if not incoming_always and not any(
            s.target_type == StageTargetType.VIRTUAL and "start" in s.stage_id
            for s in spec.stages
        ):
            # 如果 start stage 没有 ALWAYS 入边，但有其他入边，也算问题
            if adj.incoming.get(start_id):
                issues.append(ValidationIssue(
                    "MISSING_ENTRY",
                    f"起始 stage '{start_id}' 没有 ALWAYS 条件的入口边",
                    stage_id=start_id,
                ))

    # 如果没有虚拟起始 stage，检查是否有 stage 没有入边
    if not start_stages:
        for stage in spec.stages:
            if not adj.incoming.get(stage.stage_id):
                issues.append(ValidationIssue(
                    "MISSING_ENTRY",
                    f"Stage '{stage.stage_id}' 没有入边，可能缺少入口",
                    stage_id=stage.stage_id,
                ))

    return issues
