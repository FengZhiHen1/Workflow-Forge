#!/usr/bin/env python3
"""
Workflow v3.0.0 符号审计引擎。

不运行 wfctl——从 YAML 构建状态图，按 wfctl 行为模型穷举所有可达路径，
注入攻击向量，检测状态机死锁、循环缺口、异常路径断裂、Skill 交叉不一致等问题。

Phase 1-2: 工作流级攻击（状态机/并发/用户行为/基础设施/子工作流）
Phase 3:   Skill 交叉审计（存在性/禁词/资源引用）——需 --skills-dir

输出结构化 findings JSON，供 AI（SKILL.md）消费并补充语义分析。

调用方式：
    python audit_workflow.py \\
        --workflow-yaml <path/to/WORKFLOW.yaml> \\
        [--workflows-dir <path/to/workflows/>] \\
        [--skills-dir <path/to/skills/>] \\
        [--mode symbolic|lite]

返回 JSON：
    {"findings": [...], "summary": {...}, "graph_stats": {...}}
"""

import argparse
import json
import sys
from collections import defaultdict
from itertools import product
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None

# ─── constants ──────────────────────────────────────────────

COND_ALWAYS = "always"
COND_SUCCESS = "success"
COND_FAILURE = "failure"
COND_LOOP_EXCEEDED = "loop_exceeded"

SEVERITY_CRITICAL = "critical"
SEVERITY_WARNING = "warning"
SEVERITY_INFO = "info"

START_STAGE = "s00-workflow-start"
END_STAGE = "s99-workflow-end"

MAX_NESTING_DEPTH = 3

# ─── data structures ────────────────────────────────────────


class Stage:
    __slots__ = ("stage_id", "name", "skill_id", "workflow", "mandatory",
                 "retry", "timeout_seconds", "model",
                 "exclusive", "parallel", "parallel_source", "parallel_max_instances")

    def __init__(self, raw: dict):
        self.stage_id = raw.get("stage_id", "")
        self.name = raw.get("name", "")
        self.skill_id = raw.get("skill_id")
        self.workflow = raw.get("workflow")
        self.mandatory = raw.get("mandatory", True)
        self.retry = raw.get("retry", 0)
        if not isinstance(self.retry, int):
            self.retry = 0
        self.timeout_seconds = raw.get("timeout_seconds")
        self.model = raw.get("model", "standard")
        self.exclusive = raw.get("exclusive", False)
        parallel = raw.get("parallel")
        if isinstance(parallel, dict):
            self.parallel = True
            self.parallel_source = parallel.get("source", "")
            self.parallel_max_instances = parallel.get("max_instances")
        else:
            self.parallel = False
            self.parallel_source = None
            self.parallel_max_instances = None


class Edge:
    __slots__ = ("from_id", "to_id", "condition", "choice", "max_loop",
                 "loop_counter_stage", "aggregation")

    def __init__(self, raw: dict):
        self.from_id = raw.get("from", "")
        self.to_id = raw.get("to", "")
        self.condition = raw.get("condition", "")
        self.choice = raw.get("choice")
        self.max_loop = raw.get("max_loop")
        self.loop_counter_stage = raw.get("loop_counter_stage")
        self.aggregation = raw.get("aggregation")


def is_virtual(stage_id: str) -> bool:
    return stage_id in (START_STAGE, END_STAGE)


# ─── state graph ────────────────────────────────────────────


def build_graph(stages: list[Stage], edges: list[Edge]) -> dict:
    """构建邻接表：{from_id: [Edge, ...]}"""
    adj = defaultdict(list)
    for e in edges:
        adj[e.from_id].append(e)
    # 确保所有 stage 都在 keys 中
    for s in stages:
        if s.stage_id not in adj:
            adj[s.stage_id] = []
    return dict(adj)


def collect_choices(stages: list[Stage], edges: list[Edge]) -> list:
    """
    收集所有 SUCCESS + choice 边的选项，返回列表：
    [{stage_id, choices: [{value, condition, to_id}]}, ...]
    用于路径穷举的笛卡尔积。
    """
    choice_map = defaultdict(list)
    for e in edges:
        if e.condition == COND_SUCCESS and e.choice:
            choice_map[e.from_id].append({
                "value": e.choice,
                "condition": e.condition,
                "to_id": e.to_id,
                "max_loop": e.max_loop,
            })

    result = []
    for s in stages:
        if s.stage_id in choice_map:
            result.append({
                "stage_id": s.stage_id,
                "choices": choice_map[s.stage_id],
            })
    return result


# ─── path traversal ─────────────────────────────────────────


def follow_edge(adj: dict, current_id: str, condition: str, choice: str | None = None) -> list[str]:
    """
    从 current_id 出发，按 condition 和可选的 choice 匹配 edge，
    返回可达的下游 stage_id 列表。
    """
    candidates = adj.get(current_id, [])
    next_ids = []
    for e in candidates:
        if e.condition != condition:
            continue
        if e.choice and choice:
            if e.choice == choice:
                next_ids.append(e.to_id)
        elif not e.choice:
            next_ids.append(e.to_id)
    return next_ids


def find_loop_exceeded_exit(adj: dict, stage_id: str) -> list[str]:
    """返回某 stage 的 loop_exceeded edge 指向的下游。"""
    return follow_edge(adj, stage_id, COND_LOOP_EXCEEDED)



def find_failure_exits(adj: dict, stage_id: str) -> list[str]:
    """返回某 stage 的所有 failure edge 指向的下游。"""
    return follow_edge(adj, stage_id, COND_FAILURE)


def has_path_to(adj: dict, from_id: str, target_id: str, visited: set | None = None) -> bool:
    """BFS 判断 from_id 是否能到达 target_id。"""
    if visited is None:
        visited = set()
    if from_id == target_id:
        return True
    visited.add(from_id)
    for e in adj.get(from_id, []):
        if e.to_id not in visited and e.condition != COND_FAILURE:
            if has_path_to(adj, e.to_id, target_id, visited):
                return True
    return False


def can_reach_terminal(adj: dict, from_id: str, visited: set | None = None) -> bool:
    """判断 from_id 是否能到达至少一个终态（s99 或无出边节点）。"""
    if visited is None:
        visited = set()
    if from_id == END_STAGE:
        return True
    if from_id in visited:
        return False
    visited.add(from_id)
    edges_out = adj.get(from_id, [])
    if not edges_out:
        return True  # 无出边 = 终态（允许非 s99 终态）
    for e in edges_out:
        if e.condition == COND_FAILURE or e.condition == COND_LOOP_EXCEEDED:
            continue  # 非正常出口，但也是出口
        if can_reach_terminal(adj, e.to_id, visited.copy()):
            return True
    # 即使只有 failure/loop_exceeded 出边也算可达终态
    if edges_out:
        return True
    return False


# ─── attack vectors ─────────────────────────────────────────

_findings: list[dict] = []


def _f(severity: str, category: str, attack: str, stages_involved: list[str],
       finding: str, expected: str, recommendation: str) -> None:
    _findings.append({
        "severity": severity,
        "category": category,
        "attack": attack,
        "stages_involved": stages_involved,
        "finding": finding,
        "expected": expected,
        "recommendation": recommendation,
    })


def audit_sm1_loop_exhaustion(stages: list[Stage], adj: dict, edges: list[Edge]) -> None:
    """SM-1: 检测 SUCCESS 自循环边是否缺少 loop_exceeded 出口。"""
    for s in stages:
        # 找 SUCCESS(to=self) 且有 choice 的 edge
        self_loops = [e for e in adj.get(s.stage_id, [])
                      if e.condition == COND_SUCCESS
                      and e.to_id == s.stage_id
                      and e.max_loop is not None]
        if not self_loops:
            continue

        for e in self_loops:
            loop_exits = find_loop_exceeded_exit(adj, s.stage_id)
            if not loop_exits:
                _f(SEVERITY_CRITICAL, "state_machine",
                   f"确认点全部选'{e.choice}'——循环到底",
                   [s.stage_id],
                   f"stage '{s.stage_id}' 有 self-loop (choice={e.choice!r}, max_loop={e.max_loop})，但缺少 loop_exceeded 出口",
                   f"loop_counter >= {e.max_loop} 时应触发 loop_exceeded edge",
                   f"添加 from={s.stage_id} to=<终态> condition=loop_exceeded edge")
            else:
                for lexit_id in loop_exits:
                    if not can_reach_terminal(adj, lexit_id):
                        _f(SEVERITY_WARNING, "state_machine",
                           f"loop_exceeded 出口 '{lexit_id}' 可能不可达终态",
                           [s.stage_id, lexit_id],
                           f"loop_exceeded from={s.stage_id} into={lexit_id}，但 '{lexit_id}' 无法到达终态",
                           "loop_exceeded 出口应最终可达 s99 或安全终态",
                           f"检查 '{lexit_id}' 的后续路径")


def audit_sm2_all_reject(stages: list[Stage], adj: dict) -> None:
    """SM-2 (已废弃): rejected 边条件已移除。确认现在是 Skill 内部 AskUserQuestion 行为，
    用户选择由 SubAgent 通过 DONE + routing_choice 处理，不再有独立的 rejected 路径。"""
    pass


def audit_sm3_choice_combinations(stages: list[Stage], adj: dict, choices_info: list[dict]) -> None:
    """SM-3 (已废弃): 确认点字段已移除。choice 路由现在由 SubAgent 通过 DONE + routing_choice 选择。"""
    if not choices_info:
        return

    # 构建 choice 值列表（笛卡尔积用）
    choice_values = []
    for ci in choices_info:
        choice_values.append([c["value"] for c in ci["choices"]])

    # 笛卡尔积穷举（限制上限防爆炸）
    total_combos = 1
    for cv in choice_values:
        total_combos *= len(cv)
    if total_combos > 1000:
        _f(SEVERITY_INFO, "state_machine",
           f"选项组合过多（{total_combos}），仅上报统计不逐一穷举",
           [ci["stage_id"] for ci in choices_info],
           f"确认点组合数={total_combos} 超过穷举上限 1000",
           "", "")
        return

    for combo in product(*choice_values):
        # combo 是一组 (choice_value, ...) 元组
        current = START_STAGE
        dead = False
        path = []

        # 简化推演：从 s00 开始，依次通过各确认点
        for idx, ci in enumerate(choices_info):
            sid = ci["stage_id"]
            choice_val = combo[idx]

            # 找到这个 choice 值对应的 edge
            matched = None
            for c in ci["choices"]:
                if c["value"] == choice_val:
                    matched = c
                    break

            if not matched:
                dead = True
                path.append(f"{sid}:{choice_val}? unmatched")
                break

            # 检查这个 edge 是否是 self-loop（中继确认）
            if matched["to_id"] == sid:
                # self-loop——需要检查 max_loop
                edge_obj = None
                for e in adj.get(sid, []):
                    if e.choice == choice_val and e.condition == COND_SUCCESS and e.to_id == sid:
                        edge_obj = e
                        break
                if edge_obj and edge_obj.max_loop:
                    exits = find_loop_exceeded_exit(adj, sid)
                    if not exits:
                        dead = True
                        path.append(f"{sid}:{choice_val}→self(loop, no exit)")
                        break

            # 检查是否能到达终态
            next_stage = matched["to_id"]
            if not can_reach_terminal(adj, next_stage):
                dead = True
                path.append(f"{sid}:{choice_val}→{next_stage}?(dead)")
                break

            path.append(f"{sid}:{choice_val}→{next_stage}")

        if dead:
            combo_str = " → ".join(path)
            _f(SEVERITY_CRITICAL, "state_machine",
               "选项组合穷举发现死路",
               [ci["stage_id"] for ci in choices_info],
               f"组合 [{', '.join(combo)}] 导致卡死: {combo_str}",
               "所有确认选项的组合都应有可达终态的路径",
               "检查相关 edges 的 to 目标是否完整覆盖终态")


def audit_sm4_failure_exhaustion(stages: list[Stage], adj: dict) -> None:
    """SM-4: 失败路径。模拟 ERROR + retry 耗尽。"""
    for s in stages:
        if is_virtual(s.stage_id):
            continue
        # 所有 stage 同等待遇

        failure_exits = find_failure_exits(adj, s.stage_id)
        if not failure_exits:
            _f(SEVERITY_WARNING, "state_machine",
               f"非确认点 '{s.stage_id}' 缺少 failure 出口",
               [s.stage_id],
               f"stage '{s.stage_id}' 没有 failure edge——如果该 stage 失败且 retry({s.retry}) 耗尽，实例将直接 FAILED",
               "非确认点应有 failure edge，或显式标注 '失败即终止实例'",
               f"考虑添加 from={s.stage_id} to=<终态/上游> condition=failure edge")
            continue

        for fexit_id in failure_exits:
            if not can_reach_terminal(adj, fexit_id):
                _f(SEVERITY_WARNING, "state_machine",
                   f"failure 出口 '{fexit_id}' 可能不可达终态",
                   [s.stage_id, fexit_id],
                   f"failure edge to={fexit_id} 无法到达终态",
                   "failure 出口应最终可达 s99 或安全终态",
                   f"检查 '{fexit_id}' 的后续路径")


def audit_cc_parallel_exclusive(stages: list[Stage]) -> None:
    """CC-1: parallel + exclusive 共存（validate_workflow.py 已拦截，此处二次确认）。"""
    for s in stages:
        if s.parallel and s.exclusive:
            _f(SEVERITY_WARNING, "concurrency",
               f"stage '{s.stage_id}' parallel 与 exclusive 共存",
               [s.stage_id],
               "parallel 和 exclusive 语义冲突——parallel 允许多实例并发，exclusive 禁止其他 stage 并行",
               "二选一: 去掉 parallel 或去掉 exclusive",
               f"修改 stage '{s.stage_id}' 的定义")


def audit_cc_parallel_vs_max_agents(stages: list[Stage], max_parallel_agents: int) -> None:
    """CC-2: parallel.max_instances vs max_parallel_agents。"""
    for s in stages:
        if s.parallel and s.parallel_max_instances and s.parallel_max_instances > max_parallel_agents:
            _f(SEVERITY_WARNING, "concurrency",
               f"parallel.max_instances ({s.parallel_max_instances}) 超过 max_parallel_agents ({max_parallel_agents})",
               [s.stage_id],
               f"stage '{s.stage_id}' 声明最多 {s.parallel_max_instances} 个并行实例，但全局上限为 {max_parallel_agents}",
               "parallel.max_instances 应 ≤ max_parallel_agents",
               f"调整 parallel.max_instances 或 max_parallel_agents")


def audit_cc_aggregation_any(stages: list[Stage], edges: list[Edge]) -> None:
    """CC-4: aggregation:any 使用场景标记（需 AI 补充语义判断）。"""
    for e in edges:
        if e.aggregation == "any":
            _f(SEVERITY_INFO, "concurrency",
               f"aggregation='any' 使用场景需人工确认",
               [e.from_id, e.to_id],
               f"edge from={e.from_id} to={e.to_id} 使用 aggregation=any——仅适用于互斥替代方案",
               "aggregation=any 不应用于互补拆分场景，否则部分结果丢失",
               f"确认 '{e.from_id}' 的并行分支是否为互斥替代方案")


def audit_ub1_backward_edge_returnability(stages: list, adj: dict, findings: list) -> None:
    """UB-1 (已废弃): rejected 边条件已移除。SUCCESS 回边可回复性已由 wfctl domain/dag/validator.py 的
    validate_rejected_returnability 改为通用回边检查。此检查不再需要独立的 rejected 语义。"""
    pass


# ─── skill cross-audit (Phase 3: mechanical) ──────────────────

BANNED_PATTERNS = [
    (r"artifacts/", "生产车间路径 'artifacts/'——消费者项目中没有此目录"),
    (r"workshop/", "生产车间路径 'workshop/'——消费者项目中没有此目录"),
    (r"\[WORKFLOW_CONFIG\]", "v3 已移除 [WORKFLOW_CONFIG] 代码块"),
    (r"SubAgent\s*\(|Agent\s*\(|subagent_type", "内部 SubAgent 调度——Skill 不应嵌套 SubAgent"),
    (r"stage_id\s*[=:]|workflow_id\s*[=:]", "Stage ID 或 Workflow ID 感知——Skill 不应感知工作流结构"),
    (r"\.\./references/|\.\./scripts/", "相对路径引用——应使用项目根相对路径 (.claude/...)"),
]

RESOURCE_PATTERN = r"(?:references|scripts|assets)/[\w./-]+"


def _f_skill(severity: str, category: str, attack: str, skill_id: str,
             finding: str, expected: str, recommendation: str,
             location: str = "") -> None:
    _findings.append({
        "severity": severity,
        "category": category,
        "attack": attack,
        "stages_involved": [skill_id],
        "finding": finding + (f" [{location}]" if location else ""),
        "expected": expected,
        "recommendation": recommendation,
    })


def _resolve_skill_path(skill_id: str, skill_search_dirs: list[Path]) -> Path | None:
    """在多个技能搜索目录中查找 skill_id 的 SKILL.md，返回第一个匹配的路径。"""
    for base in skill_search_dirs:
        sp = base / skill_id / "SKILL.md"
        if sp.exists():
            return sp
    return None


def _resolve_resource_path(ref: str, search_bases: list[Path]) -> Path | None:
    """在多个基础目录中查找引用的资源文件，返回第一个存在的路径。

    ref 格式如 'references/directory-convention.md'。
    search_bases 按优先级排列，如 [skill_dir, workflow_refs_dir, parent_workflow_refs_dirs...]。
    """
    for base in search_bases:
        p = base / ref
        if p.exists():
            return p
    return None


def _run_phase3_on_stages(stages: list[Stage], skill_search_dirs: list[Path],
                          resource_search_bases: list[Path] | None = None,
                          workflow_label: str = "") -> None:
    """对一组 stages 运行 SK-1/SK-2/SK-3 检查。

    skill_search_dirs: 按优先级排列的技能搜索目录列表（如 [工作流局部 skills/, 全局 skills/]）。
    resource_search_bases: SK-3 资源查找的基础目录列表，按优先级排列。
                           默认 = [skill 自身目录]。
                           工作流级共享资源应加入此列表（如 workflow 的根目录）。
    workflow_label: 用于 findings 中标识所属工作流（如 "子工作流 xxx@1.0.0"）。"""
    import re
    if not skill_search_dirs:
        return

    checked = set()
    for s in stages:
        if is_virtual(s.stage_id) or not s.skill_id:
            continue

        # SK-1: 存在性
        sp = _resolve_skill_path(s.skill_id, skill_search_dirs)
        if sp is None:
            dirs_str = ", ".join(str(d) for d in skill_search_dirs)
            _f_skill(SEVERITY_CRITICAL, "skill_cross_audit",
                     f"[{workflow_label}] stage '{s.stage_id}' 引用的 Skill '{s.skill_id}' 不存在",
                     s.skill_id,
                     f"已查找: {dirs_str}",
                     "每个 skill_id 必须在工作流局部 skills/ 或全局 skills/ 下存在",
                     f"创建 {s.skill_id}/SKILL.md 或修正 stage '{s.stage_id}' 的 skill_id")
            continue

        if s.skill_id in checked:
            continue
        checked.add(s.skill_id)

        try:
            body = sp.read_text(encoding="utf-8")
        except Exception:
            continue

        # SK-2: 禁词扫描
        for pattern, description in BANNED_PATTERNS:
            matches = list(re.finditer(pattern, body))
            if matches:
                locations = [f"L{body[:m.start()].count(chr(10)) + 1}" for m in matches[:3]]
                loc_str = ", ".join(locations)
                if len(matches) > 3:
                    loc_str += f" ... 及其他 {len(matches) - 3} 处"
                _f_skill(SEVERITY_CRITICAL if pattern in (r"artifacts/", r"\[WORKFLOW_CONFIG\]")
                         else SEVERITY_WARNING,
                         "skill_cross_audit",
                         f"[{workflow_label}] Skill '{s.skill_id}' 包含违规内容: {description}",
                         s.skill_id,
                         f"发现 {len(matches)} 处匹配",
                         "SKILL.md 应按消费者项目规范编写，不感知生产车间和工作流结构",
                         loc_str)

        # SK-3: 资源引用（查找顺序: skill自身 → 各级工作流共享资源目录）
        skill_dir = sp.parent
        bases = [skill_dir]
        if resource_search_bases:
            bases.extend(resource_search_bases)
        refs = set(re.findall(RESOURCE_PATTERN, body))
        for ref in sorted(refs):
            found = _resolve_resource_path(ref, bases)
            if found is None:
                checked_str = "、".join(str(b / ref) for b in bases[:3])
                _f_skill(SEVERITY_WARNING, "skill_cross_audit",
                         f"[{workflow_label}] Skill '{s.skill_id}' 引用的资源文件不存在: {ref}",
                         s.skill_id,
                         f"已查找: {checked_str}",
                         "资源文件应在 Skill 自身目录或工作流级共享目录中存在",
                         ref)
        # 反向检查: Skill 自身目录中有但 SKILL.md 未引用的孤立文件
        for subdir in ("references", "scripts", "assets"):
            sub_path = skill_dir / subdir
            if not sub_path.is_dir():
                continue
            for f in sub_path.rglob("*"):
                if f.is_file():
                    rel = str(f.relative_to(skill_dir)).replace("\\", "/")
                    if rel not in refs:
                        _f_skill(SEVERITY_INFO, "skill_cross_audit",
                                 f"[{workflow_label}] Skill '{s.skill_id}' 存在未被引用的孤立文件",
                                 s.skill_id,
                                 f"文件 '{rel}' 存在于目录但 SKILL.md 未引用",
                                 "所有资源文件应在 SKILL.md 正文中明确说明'何时读取'",
                                 rel)


# ─── orchestration ──────────────────────────────────────────


def audit(workflow_yaml_path: Path, workflows_dir: Path | None,
          skills_dir: Path | None = None, mode: str = "symbolic") -> dict:
    """主审计函数。返回 {findings: [...], summary: {...}, graph_stats: {...}}"""
    global _findings
    _findings = []

    # 加载
    if yaml is None:
        return {"findings": [], "summary": {"error": "PyYAML not installed"},
                "graph_stats": {}}

    try:
        text = workflow_yaml_path.read_text(encoding="utf-8")
        data = yaml.safe_load(text)
    except Exception as e:
        return {"findings": [], "summary": {"error": str(e)}, "graph_stats": {}}

    if not isinstance(data, dict):
        return {"findings": [], "summary": {"error": "YAML root not dict"},
                "graph_stats": {}}

    stages_raw = data.get("stages", [])
    edges_raw = data.get("edges", [])
    max_parallel_agents = data.get("max_parallel_agents", 1)

    stages = [Stage(s) for s in stages_raw if isinstance(s, dict)]
    edges = [Edge(e) for e in edges_raw if isinstance(e, dict)]
    adj = build_graph(stages, edges)

    # 运行攻击向量
    audit_sm1_loop_exhaustion(stages, adj, edges)
    audit_sm2_all_reject(stages, adj)

    if mode == "symbolic":
        choices_info = collect_choices(stages, edges)
        audit_sm3_choice_combinations(stages, adj, choices_info)

    audit_sm4_failure_exhaustion(stages, adj)
    audit_cc_parallel_exclusive(stages)
    audit_cc_parallel_vs_max_agents(stages, max_parallel_agents)
    audit_cc_aggregation_any(stages, edges)
    audit_ub1_backward_edge_returnability(stages, adj)
    audit_if1_timeout_retry_chain(stages, adj)
    audit_sw1_sub_workflow_failure_propagation(stages, adj)
    audit_sw2_sub_workflow_blocking(stages, adj)

    # Phase 3 前置: 构建工作流级资源查找路径
    workflow_dir = workflow_yaml_path.parent
    resource_bases = [workflow_dir] if skills_dir else None

    # 子工作流嵌套深度
    workflow_refs = {s.stage_id: s.workflow for s in stages if s.workflow}
    if workflow_refs:
        audit_sw3_nested_failure_cascade(workflow_refs, workflows_dir, skills_dir,
                                         resource_bases, 1, _findings)

    # Phase 3: Skill 交叉审计（机械层）
    if skills_dir:
        workflow_skills_dir = workflow_dir / "skills"
        skill_search_dirs = [workflow_skills_dir, skills_dir]
        _run_phase3_on_stages(stages, skill_search_dirs,
                              resource_search_bases=resource_bases,
                              workflow_label="父工作流")

    # 汇总
    critical = sum(1 for f in _findings if f["severity"] == SEVERITY_CRITICAL)
    warning = sum(1 for f in _findings if f["severity"] == SEVERITY_WARNING)
    info = sum(1 for f in _findings if f["severity"] == SEVERITY_INFO)

    if critical > 0:
        overall = "fail"
    elif warning > 0:
        overall = "conditional_pass"
    else:
        overall = "pass"

    # 去重
    seen = set()
    deduped = []
    for f in _findings:
        key = (f["severity"], f["category"], f["attack"], tuple(f["stages_involved"]), f["finding"])
        if key not in seen:
            seen.add(key)
            deduped.append(f)

    return {
        "findings": deduped,
        "summary": {
            "critical_count": critical,
            "warning_count": warning,
            "info_count": info,
            "overall_result": overall,
        },
        "graph_stats": {
            "stage_count": len([s for s in stages if not is_virtual(s.stage_id)]),
            "edge_count": len(edges),
            "confirmation_count": 0  # (已废弃——confirmation_point 字段已移除),
            "parallel_stage_count": sum(1 for s in stages if s.parallel),
            "workflow_stage_count": len(workflow_refs),
            "nesting_max_depth": audit_sw3_nested_failure_cascade(
                workflow_refs, workflows_dir, skills_dir,
                resource_bases, 1, []) if workflow_refs else 0,
            "skill_count": len({s.skill_id for s in stages if s.skill_id}),
            "skill_existence_ok": not any(
                f["category"] == "skill_cross_audit" and f["severity"] == SEVERITY_CRITICAL
                for f in _findings),
            "skill_banned_found": sum(
                1 for f in _findings
                if f["category"] == "skill_cross_audit" and "违规内容" in f["attack"]),
            "skill_orphaned_files": sum(
                1 for f in _findings
                if f["category"] == "skill_cross_audit" and "孤立文件" in f["attack"]),
        },
    }


# ─── CLI ────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="Workflow v3.0.0 符号审计引擎")
    parser.add_argument("--workflow-yaml", required=True,
                        help="WORKFLOW.yaml 文件路径")
    parser.add_argument("--workflows-dir",
                        help="workflows/ 目录路径（用于子工作流审计）")
    parser.add_argument("--skills-dir",
                        help="skills/ 目录路径（用于 Phase 3 Skill 交叉审计）")
    parser.add_argument("--output",
                        help="输出 JSON 文件路径（不指定则输出到 stdout）")
    parser.add_argument("--mode", choices=["symbolic", "lite"],
                        default="symbolic",
                        help="symbolic=全量推演, lite=跳过选项组合穷举")
    args = parser.parse_args()

    yaml_path = Path(args.workflow_yaml).resolve()
    if not yaml_path.exists():
        result = {"findings": [], "summary": {"error": f"文件不存在: {yaml_path}"},
                  "graph_stats": {}}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(1)

    workflows_dir = Path(args.workflows_dir).resolve() if args.workflows_dir else None
    skills_dir = Path(args.skills_dir).resolve() if args.skills_dir else None
    result = audit(yaml_path, workflows_dir, skills_dir=skills_dir, mode=args.mode)

    output_json = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(output_json, encoding="utf-8")
        print(f"审计结果已写入: {args.output}", file=sys.stderr)
    else:
        print(output_json)
    sys.exit(0 if result["summary"].get("overall_result", "fail") != "fail" else 1)


if __name__ == "__main__":
    main()
