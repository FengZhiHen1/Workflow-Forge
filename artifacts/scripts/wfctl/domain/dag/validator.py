"""DAG 静态验证器：检测工作流定义中的结构问题（16 项检查）。

使用 domain/dag/topology.py 的 Tarjan SCC 引擎进行循环和回边分析。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from domain.dag.graph import AdjacencyList, build_adjacency
from domain.workflow.spec import (
    EdgeCondition,
    EdgeSpec,
    StageSpec,
    StageTargetType,
    WorkflowSpec,
)
from domain.dag.topology import TopologyResult, analyze_topology


@dataclass
class ValidationIssue:
    """验证问题描述。"""

    category: str
    message: str
    stage_id: str | None = None
    edge_from: str | None = None
    edge_to: str | None = None
    severity: str = "ERROR"  # ERROR | WARNING | INFO


@dataclass
class ValidationResult:
    """验证结果。"""

    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return any(i.severity == "ERROR" for i in self.issues)

    @property
    def error_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "ERROR")

    @property
    def warning_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "WARNING")

    def by_category(self, category: str) -> list[ValidationIssue]:
        return [i for i in self.issues if i.category == category]

    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "ERROR"]


def validate_workflow(spec: WorkflowSpec) -> ValidationResult:
    """验证 WorkflowSpec 的结构完整性（16 项检查）。

    使用 Tarjan SCC 拓扑分析引擎检测循环、回边等结构问题。
    """
    adj = build_adjacency(spec)
    topo = analyze_topology(adj)
    issues: list[ValidationIssue] = []

    issues.extend(_check_cycles(adj, topo))
    issues.extend(_check_back_edges(topo))
    issues.extend(_check_edge_completeness(spec, adj))
    issues.extend(_check_choice_consistency(adj))
    issues.extend(_check_failure_chain(adj))
    issues.extend(_check_parallel_consistency(spec, adj))
    issues.extend(_check_terminal_stages(spec, adj))
    issues.extend(_check_reachability(spec, adj))
    issues.extend(_check_duplicate_ids(spec))
    issues.extend(_check_dangling_edges(spec, adj))
    issues.extend(_check_parallel_sources(spec))

    return ValidationResult(issues=issues)


# ── 循环检测 ──

def _check_cycles(adj: AdjacencyList, topo: TopologyResult) -> list[ValidationIssue]:
    """检测自环无 max_loop 和多节点环误设 max_loop。"""
    issues: list[ValidationIssue] = []

    for cycle in topo.cycles:
        if len(cycle) == 1:
            node = cycle[0]
            for edge in adj.outgoing.get(node, []):
                if edge.to_stage == node:
                    if edge.max_loop is None or edge.max_loop <= 0:
                        issues.append(ValidationIssue(
                            "UNBOUNDED_LOOP",
                            f"Stage '{node}' 的自环 {edge.condition.value} 边缺少 max_loop 限制",
                            stage_id=node,
                            edge_from=node,
                            edge_to=node,
                        ))
        else:
            # max_loop 仅限于自环边；多节点环中的边不应设置 max_loop
            for n in cycle:
                for edge in adj.outgoing.get(n, []):
                    if edge.to_stage in cycle and edge.max_loop is not None:
                        issues.append(ValidationIssue(
                            "MULTI_NODE_CYCLE_MAX_LOOP",
                            f"多节点环中的边 '{edge.from_stage}'→'{edge.to_stage}' "
                            f"设置了 max_loop={edge.max_loop}，max_loop 仅限于自环边",
                            stage_id=n,
                            edge_from=edge.from_stage,
                            edge_to=edge.to_stage,
                        ))

    return issues


# ── 回边验证 ──

def _check_back_edges(topo: TopologyResult) -> list[ValidationIssue]:
    """检测回边是否误设了 max_loop（回边的 max_loop 应由目标 stage 的自环边上设置）。"""
    issues: list[ValidationIssue] = []

    for edge in topo.back_edges:
        # 自环边的 max_loop 是合法的，无需检查
        if edge.from_stage == edge.to_stage:
            continue
        if edge.max_loop is not None and edge.max_loop > 0:
            issues.append(ValidationIssue(
                "BACK_EDGE_MAX_LOOP",
                f"回边 '{edge.from_stage}'→'{edge.to_stage}' 设置了 max_loop={edge.max_loop}，"
                f"回边的循环控制应在目标 stage 的自环边上设置",
                edge_from=edge.from_stage,
                edge_to=edge.to_stage,
                severity="WARNING",
            ))

    return issues


# ── 边完备性 ──

def _check_edge_completeness(
    spec: WorkflowSpec, adj: AdjacencyList
) -> list[ValidationIssue]:
    """检测边目标有效性 + cascade_reset_until 合法性。"""
    issues: list[ValidationIssue] = []
    stage_ids = {s.stage_id for s in spec.stages}

    for edge in spec.edges:
        if edge.cascade_reset_until:
            target = edge.cascade_reset_until
            if target not in stage_ids:
                issues.append(ValidationIssue(
                    "INVALID_CASCADE_TARGET",
                    f"边 '{edge.from_stage}'→'{edge.to_stage}' 的 cascade_reset_until='{target}' "
                    f"指向不存在的 stage",
                    edge_from=edge.from_stage,
                    edge_to=edge.to_stage,
                ))
            elif not _is_ancestor(adj, target, edge.from_stage):
                issues.append(ValidationIssue(
                    "INVALID_CASCADE_TARGET",
                    f"边 '{edge.from_stage}'→'{edge.to_stage}' 的 cascade_reset_until='{target}' "
                    f"不是 '{edge.from_stage}' 的祖先节点",
                    edge_from=edge.from_stage,
                    edge_to=edge.to_stage,
                ))

    return issues


# ── choice 一致性 ──

def _check_choice_consistency(adj: AdjacencyList) -> list[ValidationIssue]:
    """检测 SUCCESS 边的 choice 一致性和歧义路由。"""
    issues: list[ValidationIssue] = []

    for stage_id, edges in adj.outgoing.items():
        by_cond: dict[EdgeCondition, list[EdgeSpec]] = {}
        for e in edges:
            by_cond.setdefault(e.condition, []).append(e)

        for cond in (EdgeCondition.SUCCESS,):
            cond_edges = by_cond.get(cond, [])
            if len(cond_edges) <= 1:
                continue

            with_choice = [e for e in cond_edges if e.choice]
            without_choice = [e for e in cond_edges if not e.choice]

            # 混合 choice（部分有、部分无）导致歧义
            if with_choice and without_choice:
                issues.append(ValidationIssue(
                    "AMBIGUOUS_ROUTING",
                    f"Stage '{stage_id}' 的 {cond.value} 边中部分有 choice、部分无 choice，"
                    f"匹配存在歧义",
                    stage_id=stage_id,
                ))

            # 重复 choice
            seen: set[str] = set()
            for e in with_choice:
                choice = e.choice or ""
                if choice in seen:
                    issues.append(ValidationIssue(
                        "CHOICE_INCONSISTENCY",
                        f"Stage '{stage_id}' 的 {cond.value} 边中 choice='{choice}' 重复",
                        stage_id=stage_id,
                        edge_from=e.from_stage,
                        edge_to=e.to_stage,
                    ))
                seen.add(choice)

    return issues


# ── 错误链 ──

def _check_failure_chain(adj: AdjacencyList) -> list[ValidationIssue]:
    """检测 failure_edge + retry=0（死 failure_edge）和孤儿 loop_exceeded_edge。"""
    issues: list[ValidationIssue] = []

    for stage_id, edges in adj.outgoing.items():
        spec = adj.stages.get(stage_id)
        if spec is None:
            continue

        has_failure = any(e.condition == EdgeCondition.FAILURE for e in edges)
        has_loop_exceeded = any(e.condition == EdgeCondition.LOOP_EXCEEDED for e in edges)

        # failure_edge 但 retry=0 → 永远无法触发
        if has_failure and spec.retry <= 0:
            issues.append(ValidationIssue(
                "DEAD_FAILURE_EDGE",
                f"Stage '{stage_id}' 有 failure_edge 但 retry={spec.retry}，error 后直接终止，"
                f"failure_edge 永远不会触发",
                stage_id=stage_id,
                severity="WARNING",
            ))

        # loop_exceeded_edge 但无 failure_edge
        if has_loop_exceeded and not has_failure:
            issues.append(ValidationIssue(
                "ORPHAN_LOOP_EXCEEDED",
                f"Stage '{stage_id}' 有 loop_exceeded_edge 但无 failure_edge，"
                f"loop_exceeded 锚定缺失",
                stage_id=stage_id,
                severity="WARNING",
            ))

    return issues


# ── parallel 一致性 ──

def _check_parallel_consistency(
    spec: WorkflowSpec, adj: AdjacencyList
) -> list[ValidationIssue]:
    """检测 parallel fan-in 一致性。"""
    issues: list[ValidationIssue] = []

    for stage in spec.stages:
        if not stage.parallel:
            continue

        incoming = adj.incoming.get(stage.stage_id, [])

        # parallel stage 应至少有一个入边
        if not incoming:
            issues.append(ValidationIssue(
                "PARALLEL_FANIN",
                f"Parallel stage '{stage.stage_id}' 没有入边",
                stage_id=stage.stage_id,
            ))

        # 所有入边应该是 ALWAYS 条件（parallel fan-in 不涉及条件路由）
        for edge in incoming:
            if edge.condition not in (EdgeCondition.ALWAYS,):
                issues.append(ValidationIssue(
                    "PARALLEL_FANIN",
                    f"Parallel stage '{stage.stage_id}' 的入边 '{edge.from_stage}'"
                    f" 条件为 {edge.condition.value}，建议使用 ALWAYS",
                    stage_id=stage.stage_id,
                    edge_from=edge.from_stage,
                    edge_to=stage.stage_id,
                    severity="WARNING",
                ))

    return issues


# ── 终态 stage ──

def _check_terminal_stages(
    spec: WorkflowSpec, adj: AdjacencyList
) -> list[ValidationIssue]:
    """检测终态 stage 是否有非 ALWAYS 出边。"""
    issues: list[ValidationIssue] = []

    for stage in spec.stages:
        if stage.target_type != StageTargetType.VIRTUAL:
            continue
        if "workflow-end" not in stage.stage_id and "终结" not in stage.name and "终止" not in stage.name:
            continue

        out_edges = adj.outgoing.get(stage.stage_id, [])
        non_always = [e for e in out_edges if e.condition != EdgeCondition.ALWAYS]
        if non_always:
            conditions = ", ".join(e.condition.value for e in non_always)
            issues.append(ValidationIssue(
                "TERMINAL_LEAK",
                f"终态 stage '{stage.stage_id}' 有非 ALWAYS 出边: {conditions}",
                stage_id=stage.stage_id,
            ))

    return issues


# ── 可达性 ──

def _check_reachability(
    spec: WorkflowSpec, adj: AdjacencyList
) -> list[ValidationIssue]:
    """BFS 检测从起始虚拟 stage 不可达的 stage。"""
    issues: list[ValidationIssue] = []

    # 找起始节点
    start_stages = [
        s.stage_id for s in spec.stages
        if s.target_type == StageTargetType.VIRTUAL and "start" in s.stage_id
    ]
    if not start_stages:
        start_stages = [s.stage_id for s in spec.stages if s.target_type == StageTargetType.VIRTUAL]
    if not start_stages:
        start_stages = [sid for sid in adj.stages if not adj.incoming.get(sid)]

    if not start_stages:
        return [ValidationIssue("MISSING_ENTRY", "未找到工作流入口 stage")]

    # BFS
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

    for stage in spec.stages:
        if stage.stage_id not in reachable and stage.target_type != StageTargetType.VIRTUAL:
            issues.append(ValidationIssue(
                "UNREACHABLE_STAGE",
                f"Stage '{stage.stage_id}' 从入口不可达",
                stage_id=stage.stage_id,
            ))

    return issues


# ── 重复 ID ──

def _check_duplicate_ids(spec: WorkflowSpec) -> list[ValidationIssue]:
    """检测重复的 stage_id。"""
    issues: list[ValidationIssue] = []
    seen: dict[str, int] = {}

    for i, stage in enumerate(spec.stages):
        sid = stage.stage_id
        if sid in seen:
            issues.append(ValidationIssue(
                "DUPLICATE_STAGE_ID",
                f"Stage ID '{sid}' 重复（位置 {seen[sid] + 1} 和 {i + 1}）",
                stage_id=sid,
            ))
        else:
            seen[sid] = i

    return issues


# ── 悬空边 ──

def _check_dangling_edges(
    spec: WorkflowSpec, adj: AdjacencyList
) -> list[ValidationIssue]:
    """检测边引用不存在的 source/target stage。"""
    issues: list[ValidationIssue] = []
    stage_ids = {s.stage_id for s in spec.stages}

    for edge in spec.edges:
        if edge.from_stage not in stage_ids:
            issues.append(ValidationIssue(
                "DANGLING_EDGE",
                f"边 from='{edge.from_stage}' 指向不存在的 source stage",
                edge_from=edge.from_stage,
                edge_to=edge.to_stage,
            ))
        if edge.to_stage not in stage_ids:
            issues.append(ValidationIssue(
                "DANGLING_EDGE",
                f"边 to='{edge.to_stage}' 指向不存在的 target stage",
                edge_from=edge.from_stage,
                edge_to=edge.to_stage,
            ))
        # INFO: 检测 YAML 死字段 loop_counter_stage
        if hasattr(edge, 'loop_counter_stage') and edge.loop_counter_stage:
            pass  # EdgeSpec 无此字段，在 parser 层静默丢弃

    return issues


# ── parallel source ──

def _check_parallel_sources(spec: WorkflowSpec) -> list[ValidationIssue]:
    """检测 parallel.source 指向不存在的 stage。"""
    issues: list[ValidationIssue] = []
    stage_ids = {s.stage_id for s in spec.stages}

    for stage in spec.stages:
        if stage.parallel and stage.parallel.source not in stage_ids:
            issues.append(ValidationIssue(
                "PARALLEL_SOURCE_MISSING",
                f"Stage '{stage.stage_id}' 的 parallel.source '{stage.parallel.source}' 不存在",
                stage_id=stage.stage_id,
            ))

    return issues


# ── 辅助 ──

def _is_ancestor(adj: AdjacencyList, ancestor: str, descendant: str) -> bool:
    """检查 ancestor 是否为 descendant 的祖先（BFS 反向可达）。"""
    if ancestor == descendant:
        return True
    visited: set[str] = set()
    queue = [descendant]
    while queue:
        current = queue.pop(0)
        if current in visited:
            continue
        visited.add(current)
        if current == ancestor:
            return True
        for edge in adj.incoming.get(current, []):
            if edge.from_stage not in visited:
                queue.append(edge.from_stage)
    return False
