#!/usr/bin/env python3
"""
WORKFLOW.yaml 合法性校验脚本

职责：
1. YAML 语法校验
2. Schema 结构校验（字段类型、必填项、枚举值）
3. 交叉引用一致性（stage_id、edges、skill_id）
4. 图结构合法性（可达性、死节点、循环）
5. 确认点与 Edge 匹配校验
6. 并发规则合法性
7. 可选：Skill 产物存在性校验

调用方式：
    python validate_workflow.py \
        --workflow-yaml <path/to/WORKFLOW.yaml> \
        [--skills-dir <path/to/skills/>] \
        [--strict]

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


VALID_CONDITIONS = {"always", "success", "failure", "confirmed", "rejected", "loop_exceeded"}
VALID_RETRY_ON = {"timeout", "error"}

# kebab-case: 小写字母、数字、连字符
KEBAB_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
# semver 简单校验: x.y.z 或 x.y
SEMVER_RE = re.compile(r"^\d+\.\d+(\.\d+)?([+-].+)?$")


def _err(msg: str, errors: list):
    errors.append(msg)


def validate_yaml_syntax(path: Path) -> tuple[dict | None, list[str]]:
    """校验 YAML 语法，返回 (data, errors)"""
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
    """校验顶层结构和必填字段"""
    # schema_version
    sv = data.get("schema_version")
    if sv != "2.0.0":
        _err(f"schema_version 必须是 '2.0.0'，当前: {sv!r}", errors)

    # workflow_id
    wf_id = data.get("workflow_id")
    if not wf_id:
        _err("workflow_id 必填", errors)
    elif not isinstance(wf_id, str):
        _err(f"workflow_id 必须是字符串，当前类型: {type(wf_id).__name__}", errors)
    elif not KEBAB_RE.match(wf_id):
        _err(f"workflow_id 格式应为 kebab-case，当前: {wf_id!r}", errors)

    # version
    ver = data.get("version")
    if not ver:
        _err("version 必填", errors)
    elif not isinstance(ver, str):
        _err(f"version 必须是字符串，当前类型: {type(ver).__name__}", errors)
    elif not SEMVER_RE.match(ver):
        _err(f"version 应为语义化版本 (如 1.0.0)，当前: {ver!r}", errors)

    # description
    desc = data.get("description")
    if not desc:
        _err("description 必填", errors)
    elif not isinstance(desc, str):
        _err(f"description 必须是字符串，当前类型: {type(desc).__name__}", errors)

    # stages
    stages = data.get("stages")
    if not isinstance(stages, list):
        _err(f"stages 必须是数组，当前类型: {type(stages).__name__}", errors)
        return
    if len(stages) == 0:
        _err("stages 不能为空数组", errors)
        return

    # edges
    edges = data.get("edges")
    if not isinstance(edges, list):
        _err(f"edges 必须是数组，当前类型: {type(edges).__name__}", errors)

    # concurrency_rules
    cr = data.get("concurrency_rules")
    if cr is not None and not isinstance(cr, dict):
        _err(f"concurrency_rules 必须是对象或省略，当前类型: {type(cr).__name__}", errors)

    # conflict_resolution
    cres = data.get("conflict_resolution")
    if cres is not None and not isinstance(cres, dict):
        _err(f"conflict_resolution 必须是对象或省略，当前类型: {type(cres).__name__}", errors)

    # git_anchors
    ga = data.get("git_anchors")
    if ga is not None and not isinstance(ga, dict):
        _err(f"git_anchors 必须是对象或省略，当前类型: {type(ga).__name__}", errors)


def validate_stages(stages: list, errors: list) -> dict:
    """校验每个 stage 的字段，返回 stage_id -> index 映射"""
    stage_ids = {}
    skill_ids = set()
    required_fields = {"stage_id", "name", "skill_id", "mandatory", "confirmation_point", "retry_policy", "description"}

    for i, stage in enumerate(stages):
        prefix = f"stages[{i}]"
        if not isinstance(stage, dict):
            _err(f"{prefix} 必须是对象，当前类型: {type(stage).__name__}", errors)
            continue

        # 必填字段检查
        missing = required_fields - set(stage.keys())
        if missing:
            _err(f"{prefix} 缺少必填字段: {sorted(missing)}", errors)

        # stage_id
        sid = stage.get("stage_id")
        if sid:
            if not isinstance(sid, str):
                _err(f"{prefix}.stage_id 必须是字符串", errors)
            elif not KEBAB_RE.match(sid):
                _err(f"{prefix}.stage_id 格式应为 kebab-case，当前: {sid!r}", errors)
            elif sid in stage_ids:
                _err(f"stage_id '{sid}' 重复定义（stages[{stage_ids[sid]}] 和 stages[{i}]）", errors)
            else:
                stage_ids[sid] = i

        # name
        name = stage.get("name")
        if name and not isinstance(name, str):
            _err(f"{prefix}.name 必须是字符串", errors)

        # skill_id
        sk_id = stage.get("skill_id")
        if sk_id:
            if not isinstance(sk_id, str):
                _err(f"{prefix}.skill_id 必须是字符串", errors)
            else:
                skill_ids.add(sk_id)

        # mandatory
        m = stage.get("mandatory")
        if m is not None and not isinstance(m, bool):
            _err(f"{prefix}.mandatory 必须是布尔值", errors)

        # confirmation_point
        cp = stage.get("confirmation_point")
        if cp is not None and not isinstance(cp, bool):
            _err(f"{prefix}.confirmation_point 必须是布尔值", errors)

        # retry_policy
        rp = stage.get("retry_policy")
        if isinstance(rp, dict):
            ma = rp.get("max_attempts")
            if ma is not None:
                if not isinstance(ma, int) or isinstance(ma, bool) or ma < 1:
                    _err(f"{prefix}.retry_policy.max_attempts 必须是正整数", errors)
            on = rp.get("on", [])
            if isinstance(on, list):
                for item in on:
                    if item not in VALID_RETRY_ON:
                        _err(f"{prefix}.retry_policy.on 包含无效值: {item!r}，只允许 {VALID_RETRY_ON}", errors)
            else:
                _err(f"{prefix}.retry_policy.on 必须是数组", errors)
        elif rp is not None:
            _err(f"{prefix}.retry_policy 必须是对象", errors)

        # description
        d = stage.get("description")
        if d and not isinstance(d, str):
            _err(f"{prefix}.description 必须是字符串", errors)

    return stage_ids, skill_ids


def validate_edges(edges: list, stage_ids: dict, errors: list) -> None:
    """校验 edges 的交叉引用和条件合法性"""
    if not isinstance(edges, list):
        return

    seen = set()
    edge_from_count = {sid: 0 for sid in stage_ids}
    edge_to_count = {sid: 0 for sid in stage_ids}

    for i, edge in enumerate(edges):
        prefix = f"edges[{i}]"
        if not isinstance(edge, dict):
            _err(f"{prefix} 必须是对象", errors)
            continue

        # 必填字段
        for req in ("from", "to", "condition"):
            if req not in edge:
                _err(f"{prefix}.{req} 必填", errors)

        fr = edge.get("from")
        to = edge.get("to")
        cond = edge.get("condition")

        # from/to 存在于 stages
        if fr and fr not in stage_ids:
            _err(f"{prefix}.from '{fr}' 不存在于 stages 中", errors)
        if to and to not in stage_ids:
            _err(f"{prefix}.to '{to}' 不存在于 stages 中", errors)

        # condition 合法性
        if cond and cond not in VALID_CONDITIONS:
            _err(f"{prefix}.condition 无效: {cond!r}，只允许 {VALID_CONDITIONS}", errors)

        # max_loop / loop_counter_stage 配对检查
        max_loop = edge.get("max_loop")
        lcs = edge.get("loop_counter_stage")

        if max_loop is not None:
            if not isinstance(max_loop, int) or isinstance(max_loop, bool) or max_loop < 1:
                _err(f"{prefix}.max_loop 必须是正整数", errors)
            if not lcs:
                _err(f"{prefix} 设置了 max_loop，则 loop_counter_stage 必填", errors)

        if lcs:
            if lcs not in stage_ids:
                _err(f"{prefix}.loop_counter_stage '{lcs}' 不存在于 stages 中", errors)
            if max_loop is None:
                _err(f"{prefix} 设置了 loop_counter_stage，则 max_loop 必填", errors)

        # 重复 edge 检查
        if fr and to and cond:
            key = (fr, to, cond)
            if key in seen:
                _err(f"{prefix} 重复定义: from={fr}, to={to}, condition={cond}", errors)
            seen.add(key)

        # 统计入度/出度
        if fr in stage_ids:
            edge_from_count[fr] += 1
        if to in stage_ids:
            edge_to_count[to] += 1

    # 检查死节点（没有入边也没有出边的 stage）
    for sid in stage_ids:
        if edge_from_count[sid] == 0 and edge_to_count[sid] == 0:
            _err(f"stage '{sid}' 是孤立节点（没有任何 edge 引用）", errors)

    # 检查不可达节点（没有入边且不是初始 stage 的情况）
    # 初始 stage：没有入边但可能有出边
    # 但如果一个 stage 没有入边也没有出边，上面已经报了
    # 有出边无入边 = 初始 stage，这是合法的
    # 有入边无出边 = 终止 stage，也是合法的


def validate_confirmation_points(stages: list, stage_ids: dict, edges: list, errors: list) -> None:
    """校验 confirmation_point 与 edges 的匹配关系"""
    if not stages or not edges:
        return

    # 每个 confirmation_point=true 的 stage 必须有出边（否则无法继续）
    has_outgoing = {sid: False for sid in stage_ids}
    # 每个 confirmation_point=true 的 stage 的出边中应有 confirmed/rejected
    has_confirmed_out = {sid: False for sid in stage_ids}
    has_rejected_out = {sid: False for sid in stage_ids}

    for edge in edges:
        if not isinstance(edge, dict):
            continue
        fr = edge.get("from")
        cond = edge.get("condition")
        if fr in stage_ids:
            has_outgoing[fr] = True
            if cond == "confirmed":
                has_confirmed_out[fr] = True
            if cond == "rejected":
                has_rejected_out[fr] = True

    for stage in stages:
        if not isinstance(stage, dict):
            continue
        sid = stage.get("stage_id")
        cp = stage.get("confirmation_point")
        if cp and sid in stage_ids:
            if not has_outgoing[sid]:
                # 终止节点（没有出边）允许 confirmation_point=true，这是最终确认
                pass
            elif not has_confirmed_out[sid] and not has_rejected_out[sid]:
                _err(f"stage '{sid}' confirmation_point=true 且有出边，但出边中既没有 confirmed 也没有 rejected", errors)


def validate_concurrency_rules(data: dict, stage_ids: dict, errors: list) -> None:
    """校验并发规则"""
    cr = data.get("concurrency_rules")
    if not isinstance(cr, dict):
        return

    mpa = cr.get("max_parallel_agents")
    if mpa is not None:
        if not isinstance(mpa, int) or isinstance(mpa, bool) or mpa < 1:
            _err(f"concurrency_rules.max_parallel_agents 必须是正整数", errors)

    aps = cr.get("allowed_parallel_stages")
    if aps is not None:
        if not isinstance(aps, list):
            _err("concurrency_rules.allowed_parallel_stages 必须是数组", errors)
        else:
            seen_stages = set()
            for gi, group in enumerate(aps):
                if not isinstance(group, list):
                    _err(f"allowed_parallel_stages[{gi}] 必须是数组", errors)
                    continue
                for sid in group:
                    if sid not in stage_ids:
                        _err(f"allowed_parallel_stages[{gi}] 包含不存在的 stage_id: '{sid}'", errors)
                    if sid in seen_stages:
                        _err(f"stage '{sid}' 在 allowed_parallel_stages 中重复出现在多个并行组", errors)
                    seen_stages.add(sid)

    rcc = cr.get("resource_conflict_check")
    if rcc is not None and not isinstance(rcc, bool):
        _err("concurrency_rules.resource_conflict_check 必须是布尔值", errors)


def validate_conflict_resolution(data: dict, errors: list) -> None:
    """校验冲突解决规则"""
    cres = data.get("conflict_resolution")
    if not isinstance(cres, dict):
        return

    for key in ("user_override_requires_confirm", "mandatory_stage_skip_forbidden", "report_deviation_required"):
        val = cres.get(key)
        if val is not None and not isinstance(val, bool):
            _err(f"conflict_resolution.{key} 必须是布尔值", errors)


def validate_git_anchors(data: dict, errors: list) -> None:
    """校验 git 锚点配置"""
    ga = data.get("git_anchors")
    if not isinstance(ga, dict):
        return

    enabled = ga.get("enabled")
    if enabled is not None and not isinstance(enabled, bool):
        _err("git_anchors.enabled 必须是布尔值", errors)

    tag_prefix = ga.get("tag_prefix")
    if tag_prefix is not None and not isinstance(tag_prefix, str):
        _err("git_anchors.tag_prefix 必须是字符串", errors)

    pp = ga.get("preserve_paths")
    if pp is not None:
        if not isinstance(pp, list):
            _err("git_anchors.preserve_paths 必须是数组", errors)
        else:
            for i, p in enumerate(pp):
                if not isinstance(p, str):
                    _err(f"git_anchors.preserve_paths[{i}] 必须是字符串", errors)


def validate_skills_exist(skill_ids: set, skills_dir: Path, errors: list) -> None:
    """校验每个 skill_id 在 skills/ 目录下有对应产物"""
    if not skills_dir or not skills_dir.exists():
        _err(f"skills 目录不存在: {skills_dir}", errors)
        return

    for sk_id in sorted(skill_ids):
        skill_path = skills_dir / sk_id / "SKILL.md"
        if not skill_path.exists():
            _err(f"skill_id '{sk_id}' 在 skills/ 目录下缺失产物（期望: {skill_path}）", errors)


def validate_graph_structure(data: dict, stage_ids: dict, edges: list, errors: list) -> None:
    """校验图结构：可达性、终止性"""
    if not stage_ids or not edges:
        return

    # 构建邻接表
    adj = {sid: [] for sid in stage_ids}
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        fr = edge.get("from")
        to = edge.get("to")
        if fr in stage_ids and to in stage_ids:
            adj[fr].append(to)

    # 找出所有没有入边的 stage（潜在起始点）
    in_degree = {sid: 0 for sid in stage_ids}
    for edge in edges:
        if isinstance(edge, dict):
            to = edge.get("to")
            if to in stage_ids:
                in_degree[to] += 1

    # 从所有无入边的节点开始 BFS，标记可达节点
    start_nodes = [sid for sid, deg in in_degree.items() if deg == 0]
    if not start_nodes:
        _err("工作流图中没有起始 stage（所有 stage 都有入边），存在循环依赖", errors)
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

    # 检查不可达节点
    unreachable = set(stage_ids) - visited
    for sid in sorted(unreachable):
        _err(f"stage '{sid}' 不可达（无法从任何起始 stage 到达）", errors)

    # 检查 mandatory=true 的终止 stage（没有出边）是否合法
    for stage in data.get("stages", []):
        if not isinstance(stage, dict):
            continue
        sid = stage.get("stage_id")
        mandatory = stage.get("mandatory")
        if sid in stage_ids and mandatory and not adj.get(sid):
            # 终止 stage 允许没有出边，但需确认不是意外遗漏
            pass


def validate_workflow_yaml(path: Path, skills_dir: Path | None = None, strict: bool = False, mode: str = "standard") -> dict:
    """主校验函数"""
    errors = []

    # 1. YAML 语法
    data, syntax_errors = validate_yaml_syntax(path)
    if data is None:
        return {"valid": False, "errors": syntax_errors}

    # 2. 顶层结构
    validate_structure(data, errors)

    stages = data.get("stages", [])
    edges = data.get("edges", [])

    # 3. stages 校验
    stage_ids, skill_ids = validate_stages(stages, errors)

    # 4. edges 校验（需要 stage_ids）
    validate_edges(edges, stage_ids, errors)

    # 5. confirmation_point 与 edges 匹配
    validate_confirmation_points(stages, stage_ids, edges, errors)

    # 6. 并发规则
    validate_concurrency_rules(data, stage_ids, errors)

    # 7. 冲突解决
    validate_conflict_resolution(data, errors)

    # 8. git 锚点
    validate_git_anchors(data, errors)

    # 9. 图结构
    validate_graph_structure(data, stage_ids, edges, errors)

    # 10. Skill 产物存在性（可选）
    if skills_dir:
        validate_skills_exist(skill_ids, skills_dir, errors)

    # strict 模式：额外检查
    if strict:
        # 检查 workflow_id 与文件名目录名一致性
        wf_id = data.get("workflow_id")
        ver = data.get("version")
        parent_name = path.parent.name
        expected_dir = f"{wf_id}@{ver}"
        if parent_name != expected_dir:
            errors.append(f"strict: 目录名 '{parent_name}' 与 workflow_id@version '{expected_dir}' 不一致")

        # 检查 mandatory stage 数量 >= 1
        mandatory_count = sum(1 for s in stages if isinstance(s, dict) and s.get("mandatory"))
        if mandatory_count == 0:
            errors.append("strict: 至少需要一个 mandatory=true 的 stage")

    # optimization 模式额外校验
    if mode == "optimization":
        validate_edge_confirmation_strict(stages, stage_ids, edges, errors)
        validate_retry_policy(stages, errors)
        if skills_dir:
            validate_skill_consistency(stages, stage_ids, skills_dir, errors)

    return {"valid": len(errors) == 0, "errors": errors}


def validate_edge_confirmation_strict(stages: list, stage_ids: dict, edges: list, errors: list) -> None:
    """optimization 模式：condition=confirmed 的 edge 其 from stage 必须 confirmation_point=true"""
    if not stages or not edges:
        return
    cp_map = {}
    for stage in stages:
        if isinstance(stage, dict):
            cp_map[stage.get("stage_id")] = stage.get("confirmation_point", False)
    for i, edge in enumerate(edges):
        if not isinstance(edge, dict):
            continue
        fr = edge.get("from")
        cond = edge.get("condition")
        if cond == "confirmed" and fr in cp_map:
            if not cp_map[fr]:
                errors.append(f"optimization: edges[{i}] condition=confirmed，但 from stage '{fr}' 的 confirmation_point=false")


def validate_retry_policy(stages: list, errors: list) -> None:
    """optimization 模式：retry_policy.max_attempts 合理性"""
    for stage in stages:
        if not isinstance(stage, dict):
            continue
        sid = stage.get("stage_id")
        rp = stage.get("retry_policy")
        if isinstance(rp, dict):
            ma = rp.get("max_attempts")
            if isinstance(ma, int) and ma > 5:
                errors.append(f"optimization: stage '{sid}' retry_policy.max_attempts={ma} 过大，建议 1~3")


def validate_skill_consistency(stages: list, stage_ids: dict, skills_dir: Path, errors: list) -> None:
    """optimization 模式：confirmation_point 与 SKILL.md 的 PENDING_CONFIRM 匹配"""
    for stage in stages:
        if not isinstance(stage, dict):
            continue
        sid = stage.get("stage_id")
        sk_id = stage.get("skill_id")
        cp = stage.get("confirmation_point", False)
        if not sk_id:
            continue
        skill_path = skills_dir / sk_id / "SKILL.md"
        if not skill_path.exists():
            errors.append(f"optimization: stage '{sid}' skill_id '{sk_id}' 产物缺失（期望 {skill_path}）")
            continue
        try:
            content = skill_path.read_text(encoding="utf-8")
        except Exception as e:
            errors.append(f"optimization: 无法读取 {skill_path}: {e}")
            continue
        has_pending = "PENDING_CONFIRM" in content or "确认点上报" in content
        if cp and not has_pending:
            errors.append(f"optimization: stage '{sid}' confirmation_point=true，但 SKILL.md '{sk_id}' 未包含 PENDING_CONFIRM 上报段落")


def main():
    parser = argparse.ArgumentParser(description="WORKFLOW.yaml 合法性校验")
    parser.add_argument("--workflow-yaml", required=True, help="WORKFLOW.yaml 文件路径")
    parser.add_argument("--skills-dir", help="skills/ 目录路径（用于校验 skill_id 产物存在性）")
    parser.add_argument("--strict", action="store_true", help="严格模式：额外检查目录名一致性等")
    parser.add_argument("--mode", choices=["standard", "optimization"], default="standard", help="校验模式：standard 或 optimization")
    args = parser.parse_args()

    yaml_path = Path(args.workflow_yaml).resolve()
    if not yaml_path.exists():
        print(json.dumps({"valid": False, "errors": [f"文件不存在: {yaml_path}"]}, ensure_ascii=False, indent=2))
        sys.exit(1)

    skills_dir = Path(args.skills_dir).resolve() if args.skills_dir else None
    result = validate_workflow_yaml(yaml_path, skills_dir=skills_dir, strict=args.strict, mode=args.mode)

    if result["valid"]:
        print(json.dumps({"valid": True}, ensure_ascii=False, indent=2))
        sys.exit(0)
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
