#!/usr/bin/env python3
"""
WORKFLOW.yaml v3.0.0 合法性校验脚本。

职责：
1. YAML 语法校验
2. Schema v3.0.0 结构校验（字段类型、必填项、枚举值）
3. 交叉引用一致性（stage_id、edges、skill_id、parallel.source）
4. 图结构合法性（可达性、死节点、s99 终态可达性）
5. 废弃字段检测（confirmation_point、confirmed/rejected 边）
6. 虚拟 Stage 约束（s00 只能 always 出边，s00 无入边，s99 无出边）
7. 循环出口完整性（loop_exceeded）
8. retry 耗尽降级路径完整性（retry>0 必须有 failure/loop_exceeded 出口）
9. 子工作流 failure 传播完整性（workflow stage 必须有 failure edge）
10. 回边可回复性（SUCCESS 回边目标能否返回源 stage）
11. 冗余 edge 检测（always + success/failure 指向同一目标）
12. parallel 与 exclusive 互斥检查
13. aggregation 仅用于 parallel 场景
14. 子工作流嵌套深度（≤3 层）与引用存在性（需 --workflows-dir）
15. parallel.max_instances ≤ max_parallel_agents
16. 可选：Skill 产物存在性校验（--skills-dir）
17. optimization 模式：retry 合理性、max_loop 非自环告警

调用方式：
    python validate_workflow.py \
        --workflow-yaml <path/to/WORKFLOW.yaml> \
        [--skills-dir <path/to/skills/>] \
        [--workflows-dir <path/to/workflows/>] \
        [--strict] \
        [--mode standard|optimization]

返回 JSON：
    {"valid": true}
    {"valid": false, "errors": ["..."]}
"""

import argparse
import json
import re
import sys
from pathlib import Path


try:
    import yaml
except ImportError:
    yaml = None

try:
    from dag_topology import build_adjacency, analyze_topology, _is_ancestor
    _HAS_DAG_TOPOLOGY = True
except ImportError:
    _HAS_DAG_TOPOLOGY = False


VALID_CONDITIONS = {"always", "success", "failure", "loop_exceeded"}
VALID_MODELS = {"light", "standard", "heavy"}

KEBAB_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
SEMVER_RE = re.compile(r"^\d+\.\d+(\.\d+)?([+-].+)?$")


def _err(msg: str, errors: list):
    errors.append(msg)


def validate_yaml_syntax(path: Path) -> tuple[dict | None, list[str]]:
    errors = []
    if yaml is None:
        errors.append("未安装 PyYAML，请执行: pip install pyyaml")
        return None, errors

    try:
        text = path.read_text(encoding="utf-8")
        data = yaml.safe_load(text)
    except Exception as e:
        errors.append(f"YAML 解析失败: {e}")
        return None, errors

    if data is None:
        errors.append("YAML 文件为空")
        return None, errors
    if not isinstance(data, dict):
        errors.append(f"YAML 根节点必须是对象，当前类型: {type(data).__name__}")
        return None, errors
    return data, errors


def validate_structure(data: dict, errors: list) -> None:
    sv = data.get("schema_version")
    if sv != "3.0.0":
        _err(f"schema_version 必须是 '3.0.0'，当前: {sv!r}", errors)

    wf_id = data.get("workflow_id")
    if not wf_id:
        _err("workflow_id 必填", errors)
    elif not isinstance(wf_id, str):
        _err(f"workflow_id 必须是字符串，当前类型: {type(wf_id).__name__}", errors)
    elif not KEBAB_RE.match(wf_id):
        _err(f"workflow_id 格式应为 kebab-case，当前: {wf_id!r}", errors)

    ver = data.get("version")
    if not ver:
        _err("version 必填", errors)
    elif not isinstance(ver, str):
        _err(f"version 必须是字符串，当前类型: {type(ver).__name__}", errors)
    elif not SEMVER_RE.match(ver):
        _err(f"version 应为语义化版本 (如 1.0.0)，当前: {ver!r}", errors)

    mpa = data.get("max_parallel_agents")
    if mpa is None:
        _err("max_parallel_agents 必填", errors)
    elif not isinstance(mpa, int) or isinstance(mpa, bool) or mpa < 1:
        _err(f"max_parallel_agents 必须是正整数", errors)

    ap = data.get("anchor_prefix")
    if ap is not None and not isinstance(ap, str):
        _err(f"anchor_prefix 必须是字符串，当前类型: {type(ap).__name__}", errors)

    stages = data.get("stages")
    if not isinstance(stages, list):
        _err(f"stages 必须是数组，当前类型: {type(stages).__name__}", errors)
        return
    if len(stages) == 0:
        _err("stages 不能为空数组", errors)
        return

    edges = data.get("edges")
    if not isinstance(edges, list):
        _err(f"edges 必须是数组，当前类型: {type(edges).__name__}", errors)

    # v2 遗留字段警告
    for legacy in ("concurrency_rules", "conflict_resolution", "git_anchors"):
        if legacy in data:
            _err(f"v3.0.0 已移除 '{legacy}' 顶层字段，请使用 v3 替代方案", errors)


def validate_stages(stages: list, errors: list) -> dict:
    """返回: {stage_ids, skill_ids, parallel_stages, exclusive_stages, parallel_sources, workflow_refs}"""
    stage_ids = {}
    skill_ids = set()
    parallel_stages = set()       # 声明了 parallel 的 stage_id
    exclusive_stages = set()      # 声明了 exclusive 的 stage_id
    parallel_sources = set()      # parallel.source 引用的 stage_id（临时收集，最后校验存在性）
    workflow_refs = {}            # stage_id → workflow 引用字符串
    has_start = False
    has_end = False

    for i, stage in enumerate(stages):
        prefix = f"stages[{i}]"
        if not isinstance(stage, dict):
            _err(f"{prefix} 必须是对象", errors)
            continue

        sid = stage.get("stage_id")
        if not sid:
            _err(f"{prefix}.stage_id 必填", errors)
        elif not isinstance(sid, str):
            _err(f"{prefix}.stage_id 必须是字符串", errors)
        elif not KEBAB_RE.match(sid):
            _err(f"{prefix}.stage_id 格式应为 kebab-case，当前: {sid!r}", errors)
        elif sid in stage_ids:
            _err(f"stage_id '{sid}' 重复定义（stages[{stage_ids[sid]}] 和 stages[{i}]）", errors)
        else:
            stage_ids[sid] = i

        # 虚拟 stage 检测
        is_virtual = sid in ("s00-workflow-start", "s99-workflow-end")
        if sid == "s00-workflow-start":
            has_start = True
        if sid == "s99-workflow-end":
            has_end = True

        name = stage.get("name")
        if not name:
            _err(f"{prefix}.name 必填", errors)
        elif not isinstance(name, str):
            _err(f"{prefix}.name 必须是字符串", errors)

        if is_virtual:
            # 虚拟 stage 不应有 skill_id / workflow
            if stage.get("skill_id") or stage.get("workflow"):
                _err(f"{prefix} 虚拟 stage 不应设置 skill_id 或 workflow", errors)
            continue

        # 非虚拟 stage: skill_id 与 workflow 互斥
        has_skill = "skill_id" in stage
        has_workflow = "workflow" in stage
        if not has_skill and not has_workflow:
            _err(f"{prefix} 必须设置 skill_id 或 workflow", errors)
        elif has_skill and has_workflow:
            _err(f"{prefix} skill_id 和 workflow 互斥，只能设置一个", errors)

        sk_id = stage.get("skill_id")
        if sk_id:
            if not isinstance(sk_id, str):
                _err(f"{prefix}.skill_id 必须是字符串", errors)
            else:
                skill_ids.add(sk_id)

        wf = stage.get("workflow")
        if wf and not isinstance(wf, str):
            _err(f"{prefix}.workflow 必须是字符串", errors)
        elif wf:
            workflow_refs[sid] = wf

        m = stage.get("mandatory")
        if m is not None and not isinstance(m, bool):
            _err(f"{prefix}.mandatory 必须是布尔值", errors)

        retry = stage.get("retry")
        if retry is not None:
            if not isinstance(retry, int) or isinstance(retry, bool) or retry < 0:
                _err(f"{prefix}.retry 必须是非负整数", errors)

        to = stage.get("timeout_seconds")
        if to is not None:
            if not isinstance(to, int) or isinstance(to, bool) or to < 1:
                _err(f"{prefix}.timeout_seconds 必须是正整数", errors)

        model = stage.get("model")
        if model is not None and model not in VALID_MODELS:
            _err(f"{prefix}.model 无效: {model!r}，只允许 {VALID_MODELS}", errors)

        exclusive = stage.get("exclusive")
        if exclusive is not None and not isinstance(exclusive, bool):
            _err(f"{prefix}.exclusive 必须是布尔值", errors)
        elif exclusive is True:
            exclusive_stages.add(sid)

        parallel = stage.get("parallel")
        if parallel is not None:
            if not isinstance(parallel, dict):
                _err(f"{prefix}.parallel 必须是对象", errors)
            else:
                psrc = parallel.get("source")
                if not psrc:
                    _err(f"{prefix}.parallel.source 必填", errors)
                elif not isinstance(psrc, str):
                    _err(f"{prefix}.parallel.source 必须是字符串", errors)
                else:
                    parallel_sources.add(psrc)
                pmi = parallel.get("max_instances")
                if pmi is not None:
                    if not isinstance(pmi, int) or isinstance(pmi, bool) or pmi < 1:
                        _err(f"{prefix}.parallel.max_instances 必须是正整数", errors)
                parallel_stages.add(sid)

            # parallel 与 exclusive 互斥
            if exclusive is True:
                _err(f"{prefix} parallel 和 exclusive 不能同时设置", errors)

        # v2 遗留字段检查
        if "retry_policy" in stage:
            _err(f"{prefix} 使用了 v2 的 retry_policy，v3 请改用 retry (整数)", errors)
        if "description" in stage:
            pass  # description 在 v3 是可选的，不报错

    if not has_start:
        _err("缺少虚拟起始 stage 's00-workflow-start'", errors)
    if not has_end:
        _err("缺少虚拟终止 stage 's99-workflow-end'", errors)

    return {
        "stage_ids": stage_ids,
        "skill_ids": skill_ids,
        "parallel_stages": parallel_stages,
        "exclusive_stages": exclusive_stages,
        "parallel_sources": parallel_sources,
        "workflow_refs": workflow_refs,
    }


def validate_edges(edges: list, stage_ids: dict, stage_info: dict, errors: list) -> None:
    if not isinstance(edges, list):
        return

    seen = set()
    edge_from_count = {sid: 0 for sid in stage_ids}
    edge_to_count = {sid: 0 for sid in stage_ids}
    parallel_stages = stage_info.get("parallel_stages", set())

    for i, edge in enumerate(edges):
        prefix = f"edges[{i}]"
        if not isinstance(edge, dict):
            _err(f"{prefix} 必须是对象", errors)
            continue

        for req in ("from", "to", "condition"):
            if req not in edge:
                _err(f"{prefix}.{req} 必填", errors)

        fr = edge.get("from")
        to = edge.get("to")
        cond = edge.get("condition")

        if fr and fr not in stage_ids:
            _err(f"{prefix}.from '{fr}' 不存在于 stages 中", errors)
        if to and to not in stage_ids:
            _err(f"{prefix}.to '{to}' 不存在于 stages 中", errors)

        if cond and cond not in VALID_CONDITIONS:
            _err(f"{prefix}.condition 无效: {cond!r}，只允许 {VALID_CONDITIONS}", errors)

        max_loop = edge.get("max_loop")
        if max_loop is not None:
            if not isinstance(max_loop, int) or isinstance(max_loop, bool) or max_loop < 1:
                _err(f"{prefix}.max_loop 必须是正整数", errors)
            if cond not in ("failure", "loop_exceeded"):
                _err(f"{prefix} 设置了 max_loop，但 condition={cond}（仅 failure/loop_exceeded 需要）", errors)

        lcs = edge.get("loop_counter_stage")
        if lcs and lcs not in stage_ids:
            _err(f"{prefix}.loop_counter_stage '{lcs}' 不存在于 stages 中", errors)

        choice = edge.get("choice")
        if choice is not None and not isinstance(choice, str):
            _err(f"{prefix}.choice 必须是字符串", errors)

        agg = edge.get("aggregation")
        if agg is not None and agg not in ("all", "any"):
            _err(f"{prefix}.aggregation 无效: {agg!r}，只允许 'all' 或 'any'", errors)
        elif agg is not None and fr not in parallel_stages:
            _err(f"{prefix} 设置了 aggregation={agg!r}，但 from stage '{fr}' 未声明 parallel——aggregation 仅用于并行扇出场景", errors)

        if fr and to and cond:
            key = (fr, to, cond, choice)
            if key in seen:
                _err(f"{prefix} 重复定义: from={fr}, to={to}, condition={cond}" +
                     (f", choice={choice!r}" if choice else ""), errors)
            seen.add(key)

        if fr in stage_ids:
            edge_from_count[fr] += 1
        if to in stage_ids:
            edge_to_count[to] += 1

    # 冗余 edge 检测: condition=always 与 condition=success/failure 指向同一目标
    # always 已覆盖所有转移条件，success/failure edge 为冗余
    redundancy_groups = {}
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        fr = edge.get("from")
        to = edge.get("to")
        cond = edge.get("condition")
        if not (fr and to and cond):
            continue
        key = (fr, to)
        if key not in redundancy_groups:
            redundancy_groups[key] = set()
        redundancy_groups[key].add(cond)

    for (fr, to), conditions in redundancy_groups.items():
        if "always" in conditions and ({"success", "failure"} & conditions):
            overlapping = conditions & {"success", "failure"}
            _err(f"冗余 edge: from='{fr}' to='{to}' 同时有 condition=always 和 "
                 f"condition={'/'.join(sorted(overlapping))}——"
                 f"always 已覆盖所有转移条件，后者为冗余定义", errors)

    for sid in stage_ids:
        if edge_from_count[sid] == 0 and edge_to_count[sid] == 0:
            if sid not in ("s00-workflow-start", "s99-workflow-end"):
                _err(f"stage '{sid}' 是孤立节点（没有任何 edge 引用）", errors)


def validate_confirmation_points(stages: list, stage_ids: dict, edges: list, errors: list) -> None:
    """检测是否残留已废弃的 confirmation_point 字段。"""
    for stage in stages:
        if not isinstance(stage, dict):
            continue
        if "confirmation_point" in stage:
            sid = stage.get("stage_id", "?")
            _err(f"stage '{sid}' 包含已废弃的 confirmation_point 字段——请移除。"
                 f"确认现在是 Skill 内部行为（AskUserQuestion），不再由工作流定义声明。", errors)


def validate_choice_uniqueness(stage_ids: dict, edges: list, errors: list) -> None:
    """校验同一 from stage 的 SUCCESS edge 的 choice 值不重复。

    如果两条 SUCCESS edge 设了相同的 choice，match_success_edge 会取第一条匹配，
    另一条永远不可达。
    """
    if not edges:
        return

    # 按 from stage 分组 SUCCESS edges 的 choice 值
    choice_map = {}
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        if edge.get("condition") != "success":
            continue
        fr = edge.get("from")
        choice = edge.get("choice")
        if not fr or not choice:
            continue
        if fr not in choice_map:
            choice_map[fr] = {}
        choice_map[fr][choice] = choice_map[fr].get(choice, 0) + 1

    for sid, counts in choice_map.items():
        for choice, count in counts.items():
            if count > 1:
                _err(f"stage '{sid}' 的 success edges 中 choice='{choice}' 重复 {count} 次——"
                     f"wfctl 只会匹配第一条，其余 edge 永远不可达", errors)


def validate_graph_structure(data: dict, stage_ids: dict, edges: list, errors: list) -> None:
    if not stage_ids or not edges:
        return

    adj = {sid: [] for sid in stage_ids}
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        fr = edge.get("from")
        to = edge.get("to")
        if fr in stage_ids and to in stage_ids:
            adj[fr].append(to)

    in_degree = {sid: 0 for sid in stage_ids}
    for edge in edges:
        if isinstance(edge, dict):
            to = edge.get("to")
            if to in stage_ids:
                in_degree[to] += 1

    start_nodes = [sid for sid, deg in in_degree.items() if deg == 0]
    if not start_nodes:
        _err("工作流图中没有起始 stage（所有 stage 都有入边）", errors)
        return

    visited = set()
    queue = list(start_nodes)
    while queue:
        curr = queue.pop(0)
        if curr in visited:
            continue
        visited.add(curr)
        for nxt in adj.get(curr, []):
            if nxt not in visited:
                queue.append(nxt)

    unreachable = set(stage_ids) - visited
    for sid in sorted(unreachable):
        _err(f"stage '{sid}' 不可达（无法从任何起始 stage 到达）", errors)


def validate_loop_exceeded(stages: list, stage_ids: dict, edges: list, errors: list) -> None:
    """校验所有带 max_loop 的 edge 都有对应的 loop_exceeded 出口"""
    loop_edges = {}
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        if edge.get("max_loop"):
            fr = edge.get("from")
            if fr not in loop_edges:
                loop_edges[fr] = True

    has_loop_exceeded = set()
    for edge in edges:
        if isinstance(edge, dict) and edge.get("condition") == "loop_exceeded":
            has_loop_exceeded.add(edge.get("from"))

    for sid in loop_edges:
        if sid not in has_loop_exceeded:
            _err(f"stage '{sid}' 有带 max_loop 的 edge，但缺少 loop_exceeded 出口", errors)


def validate_virtual_stage_edges(stage_ids: dict, edges: list, errors: list) -> None:
    """校验虚拟 stage 的 edge 约束：s00 只能有 always 出边，s99 不能有出边。"""
    if not edges:
        return

    for i, edge in enumerate(edges):
        if not isinstance(edge, dict):
            continue
        fr = edge.get("from")
        cond = edge.get("condition")
        to = edge.get("to")
        prefix = f"edges[{i}]"

        if fr == "s00-workflow-start" and cond != "always":
            _err(f"{prefix} 虚拟起始 stage 's00-workflow-start' 的出边 condition 必须为 'always'，当前: {cond!r}", errors)
        if fr == "s99-workflow-end":
            _err(f"{prefix} 虚拟终止 stage 's99-workflow-end' 不应有出边", errors)

    # s00 不应有入边——它是图的唯一入口
    for i, edge in enumerate(edges):
        if isinstance(edge, dict) and edge.get("to") == "s00-workflow-start":
            _err(f"edges[{i}] 虚拟起始 stage 's00-workflow-start' 不应有入边", errors)


def validate_cycles(adj, topo, errors: list) -> None:
    """检测自环无 max_loop 和多节点环（基于 Tarjan SCC 结果）。"""
    for cycle in topo.cycles:
        if len(cycle) == 1:
            node = cycle[0]
            for edge in adj.outgoing.get(node, []):
                if edge.get("to") == node:
                    ml = edge.get("max_loop")
                    if ml is None or ml <= 0:
                        cond = edge.get("condition", "?")
                        errors.append(
                            f"UNBOUNDED_LOOP: Stage '{node}' 的自环 {cond} 边缺少 max_loop 限制"
                        )
        else:
            names = " → ".join(cycle)
            has_max = any(
                e.get("max_loop") and e.get("max_loop") > 0
                for n in cycle
                for e in adj.outgoing.get(n, [])
                if e.get("to") in cycle
            )
            if not has_max:
                errors.append(
                    f"MULTI_NODE_CYCLE: 多节点环 [{names}] 中无任何边设置 max_loop 限制"
                )


def validate_back_edge_max_loop(topo, errors: list) -> None:
    """检测回边是否误设了 max_loop。"""
    for edge in topo.back_edges:
        ml = edge.get("max_loop")
        if ml is not None and ml > 0:
            fr = edge.get("from")
            to = edge.get("to")
            errors.append(
                f"BACK_EDGE_MAX_LOOP (WARNING): 回边 '{fr}'→'{to}' 设置了 max_loop={ml}，"
                f"回边的循环控制应在目标 stage 的自环边上设置"
            )


def validate_ambiguous_routing(edges: list, errors: list) -> None:
    """检测 SUCCESS 边的 choice 歧义路由（部分有、部分无 choice）。"""
    from collections import defaultdict
    success_edges: dict[str, list[dict]] = defaultdict(list)
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        if edge.get("condition") == "success":
            fr = edge.get("from")
            if fr:
                success_edges[fr].append(edge)

    for fr, edge_list in success_edges.items():
        if len(edge_list) <= 1:
            continue
        with_choice = [e for e in edge_list if e.get("choice")]
        without_choice = [e for e in edge_list if not e.get("choice")]
        if with_choice and without_choice:
            errors.append(
                f"AMBIGUOUS_ROUTING: Stage '{fr}' 的 success 边中部分有 choice、部分无 choice，"
                f"匹配存在歧义"
            )


def validate_terminal_leak(stages: list, edges: list, errors: list) -> None:
    """检测终态 virtual stage 是否有非 ALWAYS 出边。"""
    for stage in stages:
        if not isinstance(stage, dict):
            continue
        sid = stage.get("stage_id", "")
        name = stage.get("name", "")
        is_terminal = (
            sid == "s99-workflow-end"
            or "workflow-end" in sid
            or "终结" in name
            or "终止" in name
        )
        if not is_terminal:
            continue
        # virtual stage: 无 skill_id 且无 workflow
        if stage.get("skill_id") or stage.get("workflow"):
            continue
        for edge in edges:
            if not isinstance(edge, dict):
                continue
            if edge.get("from") == sid and edge.get("condition") != "always":
                cond = edge.get("condition", "?")
                errors.append(
                    f"TERMINAL_LEAK: 终态 stage '{sid}' 有非 ALWAYS 出边: {cond}"
                )


def validate_dead_failure_edge(stages: list, edges: list, errors: list) -> None:
    """检测 failure_edge 但 retry=0（死 failure_edge）。"""
    out_conditions: dict[str, set[str]] = {}
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        fr = edge.get("from")
        cond = edge.get("condition")
        if fr and cond:
            out_conditions.setdefault(fr, set()).add(cond)

    for stage in stages:
        if not isinstance(stage, dict):
            continue
        sid = stage.get("stage_id")
        retry = stage.get("retry", 0)
        if not isinstance(retry, int) or retry > 0:
            continue
        if sid and "failure" in out_conditions.get(sid, set()):
            errors.append(
                f"DEAD_FAILURE_EDGE (WARNING): Stage '{sid}' 有 failure_edge 但 retry={retry}，"
                f"error 后直接终止，failure_edge 永远不会触发"
            )


def validate_orphan_loop_exceeded(edges: list, errors: list) -> None:
    """检测 loop_exceeded_edge 但无 failure_edge（锚定缺失）。"""
    out_conditions: dict[str, set[str]] = {}
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        fr = edge.get("from")
        cond = edge.get("condition")
        if fr and cond:
            out_conditions.setdefault(fr, set()).add(cond)

    for fr, conds in out_conditions.items():
        if "loop_exceeded" in conds and "failure" not in conds:
            errors.append(
                f"ORPHAN_LOOP_EXCEEDED (WARNING): Stage '{fr}' 有 loop_exceeded_edge 但无 "
                f"failure_edge，loop_exceeded 锚定缺失"
            )


def validate_cascade_reset(edges: list, stage_ids: dict, adj, errors: list) -> None:
    """检测 cascade_reset_until 指向不存在的 stage 或非祖先。"""
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        target = edge.get("cascade_reset_until")
        if not target:
            continue
        fr = edge.get("from")
        to = edge.get("to")
        if target not in stage_ids:
            errors.append(
                f"INVALID_CASCADE_TARGET: 边 '{fr}'→'{to}' 的 cascade_reset_until='{target}' "
                f"指向不存在的 stage"
            )
        elif not _is_ancestor(adj, target, fr):
            errors.append(
                f"INVALID_CASCADE_TARGET: 边 '{fr}'→'{to}' 的 cascade_reset_until='{target}' "
                f"不是 '{fr}' 的祖先节点"
            )


def validate_parallel_fanin(stages: list, edges: list, errors: list) -> None:
    """检测 parallel stage 的 fan-in 一致性。"""
    parallel_stages = set()
    for stage in stages:
        if isinstance(stage, dict) and stage.get("parallel"):
            parallel_stages.add(stage.get("stage_id"))

    if not parallel_stages:
        return

    incoming: dict[str, list[dict]] = {}
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        to = edge.get("to")
        if to in parallel_stages:
            incoming.setdefault(to, []).append(edge)

    for sid in parallel_stages:
        edges_in = incoming.get(sid, [])
        if not edges_in:
            errors.append(
                f"PARALLEL_FANIN: Parallel stage '{sid}' 没有入边"
            )
        for edge in edges_in:
            if edge.get("condition") != "always":
                fr = edge.get("from")
                cond = edge.get("condition", "?")
                errors.append(
                    f"PARALLEL_FANIN (WARNING): Parallel stage '{sid}' 的入边 '{fr}' "
                    f"条件为 {cond}，建议使用 ALWAYS"
                )


def validate_s99_reachability(stage_ids: dict, edges: list, errors: list) -> None:
    """校验每个非虚拟 stage 是否存在到达 s99 的路径。

    对应 audit SM-2/UB-1：部分退出路径虽然 YAML 连接正确，但可能因
    条件匹配或环路导致永远无法到达终态。
    """
    if not edges or "s99-workflow-end" not in stage_ids:
        return

    # 构建正向邻接表（不区分 condition）
    adj = {sid: [] for sid in stage_ids}
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        fr = edge.get("from")
        to = edge.get("to")
        if fr in stage_ids and to in stage_ids:
            adj[fr].append(to)

    for sid in stage_ids:
        if sid in ("s00-workflow-start", "s99-workflow-end"):
            continue

        # BFS 从 sid 到 s99
        visited = set()
        queue = [sid]
        found = False
        while queue:
            curr = queue.pop(0)
            if curr == "s99-workflow-end":
                found = True
                break
            if curr in visited:
                continue
            visited.add(curr)
            for nxt in adj.get(curr, []):
                if nxt not in visited:
                    queue.append(nxt)

        if not found:
            _err(f"stage '{sid}' 无法到达 s99-workflow-end——从该 stage 出发的所有路径"
                 f"均不能到达工作流终态，设计上存在死路径", errors)


def validate_rejected_returnability(stage_ids: dict, edges: list, errors: list) -> None:
    """校验 SUCCESS 回边目标非 s99 时，从目标 stage 能否重新到达源 stage。

    对应回边路由检查：SUCCESS 边回指上游 stage 时，需确保从回跳目标可再次到达当前 stage。
    """
    if not edges:
        return

    # 构建正向邻接表
    adj = {sid: [] for sid in stage_ids}
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        fr = edge.get("from")
        to = edge.get("to")
        if fr in stage_ids and to in stage_ids:
            adj[fr].append(to)

    # 构建拓扑序
    topo_order = _build_topo_order(stage_ids, adj)

    for edge in edges:
        if not isinstance(edge, dict):
            continue
        cond = edge.get("condition")
        if cond != "success":
            continue
        fr = edge.get("from")
        to = edge.get("to")
        if to == "s99-workflow-end":
            continue
        # 仅检查回边（to 在拓扑序中先于 fr）
        if topo_order.get(to, 999) >= topo_order.get(fr, 0):
            continue

        # BFS 从 to 到 fr，检查是否可达
        visited = set()
        queue = [to]
        can_return = False
        while queue:
            curr = queue.pop(0)
            if curr == fr:
                can_return = True
                break
            if curr in visited:
                continue
            visited.add(curr)
            for nxt in adj.get(curr, []):
                if nxt not in visited:
                    queue.append(nxt)

        if not can_return:
            _err(f"回边 SUCCESS edge from='{fr}' to='{to}' —— 从目标 '{to}' "
                 f"无法再次到达源 stage '{fr}'，用户可能被剥夺重试机会", errors)


def _build_topo_order(stage_ids: dict, adj: dict) -> dict[str, int]:
    """Kahn 拓扑排序，返回 {stage_id: order}。"""
    in_degree = {sid: 0 for sid in stage_ids}
    for fr, tos in adj.items():
        for to in tos:
            if to in in_degree:
                in_degree[to] += 1
    queue = [sid for sid, deg in in_degree.items() if deg == 0]
    order: dict[str, int] = {}
    idx = 0
    while queue:
        curr = queue.pop(0)
        order[curr] = idx
        idx += 1
        for nxt in adj.get(curr, []):
            if nxt in in_degree:
                in_degree[nxt] -= 1
                if in_degree[nxt] == 0:
                    queue.append(nxt)
    for sid in stage_ids:
        if sid not in order:
            order[sid] = idx  # 环中节点放在最后
    return order


def validate_parallel_source(parallel_sources: set, stage_ids: dict, errors: list) -> None:
    """校验 parallel.source 引用的 stage 存在且在本 Stage 的上游。"""
    for psrc in sorted(parallel_sources):
        if psrc not in stage_ids:
            _err(f"parallel.source '{psrc}' 不存在于 stages 中", errors)


def validate_sub_workflow_depth(workflow_refs: dict, workflows_dir: Path | None,
                                depth: int, errors: list, visited: set | None = None) -> None:
    """递归校验子工作流嵌套深度 ≤ 3。"""
    if not workflow_refs:
        return
    if visited is None:
        visited = set()

    for stage_id, wf_ref in workflow_refs.items():
        if depth > 3:
            _err(f"stage '{stage_id}' 的子工作流嵌套深度为 {depth}，超过上限 3 层", errors)
            return
        if not workflows_dir or not workflows_dir.exists():
            continue

        # 解析 workflow 引用: <id>@<ver>
        wf_dir = workflows_dir / wf_ref
        child_yaml = wf_dir / "WORKFLOW.yaml"
        if not child_yaml.exists():
            _err(f"stage '{stage_id}' 引用的子工作流不存在: {child_yaml}", errors)
            continue

        # 避免循环引用
        if str(child_yaml) in visited:
            _err(f"stage '{stage_id}' 子工作流 '{wf_ref}' 形成循环引用", errors)
            continue
        visited.add(str(child_yaml))

        try:
            import yaml as _yaml
            text = child_yaml.read_text(encoding="utf-8")
            child_data = _yaml.safe_load(text)
        except Exception:
            continue

        if not isinstance(child_data, dict):
            continue
        child_stages = child_data.get("stages", [])
        # 收集子工作流中的 workflow 引用
        child_workflow_refs = {}
        for cs in child_stages:
            if isinstance(cs, dict):
                csid = cs.get("stage_id", "?")
                cwf = cs.get("workflow")
                if cwf and isinstance(cwf, str):
                    child_workflow_refs[csid] = cwf

        if child_workflow_refs:
            validate_sub_workflow_depth(child_workflow_refs, workflows_dir,
                                        depth + 1, errors, visited)


def validate_max_parallel_agents(data: dict, stage_info: dict, errors: list) -> None:
    """校验 max_parallel_agents 与 Stage 声明的适配性。"""
    mpa = data.get("max_parallel_agents", 1)
    parallel_stages = stage_info.get("parallel_stages", set())

    has_any_parallel = len(parallel_stages) > 0

    if not has_any_parallel and mpa > 3:
        # 全串行工作流不需要高并发上限
        pass  # 降级为 info，不报错——用户可能有自己的考虑

    if has_any_parallel:
        # 检查 parallel.max_instances 不超过 max_parallel_agents
        stages = data.get("stages", [])
        for stage in stages:
            if not isinstance(stage, dict):
                continue
            parallel = stage.get("parallel")
            if not isinstance(parallel, dict):
                continue
            max_inst = parallel.get("max_instances")
            if max_inst is not None and isinstance(max_inst, int) and max_inst > mpa:
                _err(f"stage '{stage.get('stage_id')}' parallel.max_instances={max_inst} 超过 max_parallel_agents={mpa}", errors)


def validate_retry_failure_exit(stages: list, stage_ids: dict, edges: list, errors: list) -> None:
    """校验 retry>0 的非确认点 stage 必须有 failure 或 loop_exceeded 出口。

    对应 audit_workflow.py SM-4：非确认点 retry 耗尽后若无 failure/loop_exceeded edge，
    实例只能强制 FAILED，无法优雅降级。
    """
    if not edges:
        return

    # 收集每个 stage 的出边条件
    out_conditions = {sid: set() for sid in stage_ids}
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        fr = edge.get("from")
        cond = edge.get("condition")
        if fr in out_conditions and cond:
            out_conditions[fr].add(cond)

    for stage in stages:
        if not isinstance(stage, dict):
            continue
        sid = stage.get("stage_id")
        if sid not in stage_ids:
            continue
        if sid in ("s00-workflow-start", "s99-workflow-end"):
            continue
        retry = stage.get("retry", 0)
        if not isinstance(retry, int) or retry <= 0:
            continue

        outs = out_conditions.get(sid, set())
        has_escape = bool(outs & {"failure", "loop_exceeded"})
        if not has_escape:
            _err(f"stage '{sid}' 设置了 retry={retry} 但不是确认点，且缺少 condition=failure 或 "
                 f"condition=loop_exceeded 出边——retry 耗尽后无降级路径", errors)


def validate_workflow_failure_edge(stages: list, stage_ids: dict, edges: list, errors: list) -> None:
    """校验含 workflow 的 stage 必须有 failure edge。

    对应 audit_workflow.py SW-1：子工作流 FAILED → 父 stage ERROR。
    若无 failure edge，传播链断裂，父工作流无法感知子工作流失败。
    """
    if not edges:
        return

    workflow_stage_ids = set()
    for stage in stages:
        if not isinstance(stage, dict):
            continue
        sid = stage.get("stage_id")
        if sid and stage.get("workflow"):
            workflow_stage_ids.add(sid)

    if not workflow_stage_ids:
        return

    # 收集每个 stage 的失效出口
    failure_exits = {sid: False for sid in workflow_stage_ids}
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        fr = edge.get("from")
        cond = edge.get("condition")
        if fr in workflow_stage_ids and cond == "failure":
            failure_exits[fr] = True

    for sid in workflow_stage_ids:
        if not failure_exits[sid]:
            _err(f"stage '{sid}' 声明了 workflow（子工作流），但缺少 condition=failure 出边——"
                 f"子工作流失败后父 stage 进入 ERROR 状态，无 failure edge 将导致传播链断裂", errors)


def validate_skills_exist(skill_ids: set, skills_dir: Path, errors: list) -> None:
    if not skills_dir or not skills_dir.exists():
        _err(f"skills 目录不存在: {skills_dir}", errors)
        return

    for sk_id in sorted(skill_ids):
        skill_path = skills_dir / sk_id / "SKILL.md"
        if not skill_path.exists():
            _err(f"skill_id '{sk_id}' 在 skills/ 目录下缺失产物（期望: {skill_path}）", errors)


def validate_workflow_yaml(path: Path, skills_dir: Path | None = None,
                           workflows_dir: Path | None = None,
                           strict: bool = False, mode: str = "standard") -> dict:
    errors = []

    data, syntax_errors = validate_yaml_syntax(path)
    if data is None:
        return {"valid": False, "errors": syntax_errors}

    validate_structure(data, errors)

    stages = data.get("stages", [])
    edges = data.get("edges", [])

    result = validate_stages(stages, errors)
    stage_ids = result["stage_ids"]
    skill_ids = result["skill_ids"]

    # DAG 拓扑分析（用于新增的深度检查）
    adj = None
    topo = None
    if _HAS_DAG_TOPOLOGY:
        try:
            adj = build_adjacency(data)
            topo = analyze_topology(adj)
        except Exception as e:
            errors.append(f"DAG 拓扑分析失败: {e}")

    validate_edges(edges, stage_ids, result, errors)
    validate_confirmation_points(stages, stage_ids, edges, errors)
    validate_choice_uniqueness(stage_ids, edges, errors)
    validate_ambiguous_routing(edges, errors)
    validate_loop_exceeded(stages, stage_ids, edges, errors)
    validate_orphan_loop_exceeded(edges, errors)
    validate_retry_failure_exit(stages, stage_ids, edges, errors)
    validate_dead_failure_edge(stages, edges, errors)
    validate_workflow_failure_edge(stages, stage_ids, edges, errors)
    if adj is not None:
        validate_cascade_reset(edges, stage_ids, adj, errors)
    validate_graph_structure(data, stage_ids, edges, errors)
    if topo is not None:
        validate_cycles(adj, topo, errors)
        validate_back_edge_max_loop(topo, errors)
    validate_s99_reachability(stage_ids, edges, errors)
    validate_rejected_returnability(stage_ids, edges, errors)
    validate_virtual_stage_edges(stage_ids, edges, errors)
    validate_terminal_leak(stages, edges, errors)
    validate_parallel_fanin(stages, edges, errors)
    validate_parallel_source(parallel_sources=result["parallel_sources"],
                             stage_ids=stage_ids, errors=errors)
    validate_sub_workflow_depth(workflow_refs=result["workflow_refs"],
                                workflows_dir=workflows_dir,
                                depth=1, errors=errors)
    validate_max_parallel_agents(data, result, errors)

    if skills_dir:
        validate_skills_exist(skill_ids, skills_dir, errors)

    # 检测旧版 confirmed/rejected 边条件残留（所有模式均检查）
    for i, edge in enumerate(edges):
        if not isinstance(edge, dict):
            continue
        cond = edge.get("condition")
        if cond in ("confirmed", "rejected"):
            _err(f"edges[{i}] condition={cond} 已废弃——改用 condition=success + choice。"
                 f"确认现在是 Skill 内部行为（AskUserQuestion），不再需要 confirmed/rejected 边条件。", errors)

    if strict:
        wf_id = data.get("workflow_id")
        ver = data.get("version")
        parent_name = path.parent.name
        expected_dir = f"{wf_id}@{ver}"
        if parent_name != expected_dir:
            errors.append(f"strict: 目录名 '{parent_name}' 与 workflow_id@version '{expected_dir}' 不一致")

        mandatory_count = sum(
            1 for s in stages
            if isinstance(s, dict) and s.get("mandatory") and
            s.get("stage_id") not in ("s00-workflow-start", "s99-workflow-end")
        )
        if mandatory_count == 0:
            errors.append("strict: 至少需要一个 mandatory=true 的非虚拟 stage")

    if mode == "optimization":
        # retry 合理性（>5 警告）
        for stage in stages:
            if not isinstance(stage, dict):
                continue
            sid = stage.get("stage_id")
            retry = stage.get("retry", 0)
            if isinstance(retry, int) and retry > 5:
                errors.append(f"optimization: stage '{sid}' retry={retry} 过大，建议 0~3")

        # max_loop 在非自环 edge 上的告警
        # 自环: from==to 是"循环完善"模式；非自环: from≠to 是"级联重置"模式（如 s08→s02）
        # 非自环的 max_loop 语义更复杂，提醒设计者确认意图
        for i, edge in enumerate(edges):
            if not isinstance(edge, dict):
                continue
            if edge.get("max_loop") and edge.get("from") != edge.get("to"):
                fr = edge.get("from")
                to = edge.get("to")
                errors.append(
                    f"optimization: edges[{i}] 在非自环边 (from='{fr}' to='{to}') 上设置了 "
                    f"max_loop={edge.get('max_loop')}——非自环边 max_loop 通常用于级联重置模式，"
                    f"请确认设计意图"
                )

    return {"valid": len(errors) == 0, "errors": errors}


def main():
    parser = argparse.ArgumentParser(description="WORKFLOW.yaml v3.0.0 合法性校验")
    parser.add_argument("--workflow-yaml", required=True, help="WORKFLOW.yaml 文件路径")
    parser.add_argument("--skills-dir", help="skills/ 目录路径")
    parser.add_argument("--workflows-dir", help="workflows/ 目录路径（用于校验子工作流引用和嵌套深度）")
    parser.add_argument("--strict", action="store_true", help="严格模式")
    parser.add_argument("--mode", choices=["standard", "optimization"], default="standard",
                        help="校验模式")
    args = parser.parse_args()

    yaml_path = Path(args.workflow_yaml).resolve()
    if not yaml_path.exists():
        print(json.dumps({"valid": False, "errors": [f"文件不存在: {yaml_path}"]},
                         ensure_ascii=False, indent=2))
        sys.exit(1)

    skills_dir = Path(args.skills_dir).resolve() if args.skills_dir else None
    workflows_dir = Path(args.workflows_dir).resolve() if args.workflows_dir else None
    result = validate_workflow_yaml(yaml_path, skills_dir=skills_dir,
                                    workflows_dir=workflows_dir,
                                    strict=args.strict, mode=args.mode)

    if result["valid"]:
        print(json.dumps({"valid": True}, ensure_ascii=False, indent=2))
        sys.exit(0)
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
