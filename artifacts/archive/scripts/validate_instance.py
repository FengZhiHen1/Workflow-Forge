#!/usr/bin/env python3
"""
Instance 状态机校验脚本

职责：
1. 语法校验（JSON 格式、字段类型）
2. 引用完整性（reference.workflow_id@version 文件存在）
3. 版本一致性（snapshot_hash 匹配，可选）
4. Stage 合法性
5. Message 存在性
6. Git 锚点存在性（--strict 模式）
7. 状态流转合法性
8. 循环计数器
9. 并发一致性
10. 时间戳格式、布尔类型、数值范围

调用方式：
    python .agent/scripts/validate_instance.py \
        --instance <instance_id> \
        [--strict]
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
AGENT_DIR = PROJECT_ROOT / ".agent"
CLAUEDE_DIR = PROJECT_ROOT / ".claude"
WORKFLOWS_DIR = CLAUEDE_DIR / "workflows"
INSTANCES_DIR = AGENT_DIR / "workflows" / "instances"
MESSAGES_DIR = AGENT_DIR / "messages"

VALID_INSTANCE_STATUSES = {"PLANNING", "EXECUTING", "SUSPENDED", "COMPLETED", "FAILED", "CANCELLED"}
VALID_STAGE_STATUSES = {"PENDING", "RUNNING", "BLOCKED", "DONE", "ERROR", "SKIPPED", "CANCELLED", "SUPERSEDED"}
VALID_DEVIATION_TYPES = {"USER_OVERRIDE", "USER_ROLLBACK", "SKILL_FAILURE", "TIMEOUT", "RESOURCE_CONFLICT", "MANUAL_ADJUSTMENT", "LOOP_EXCEEDED"}
VALID_EDGE_CONDITIONS = {"always", "success", "failure", "confirmed", "rejected", "loop_exceeded"}


ISO8601_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$"
)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return f"sha256:{h.hexdigest()}"


def _is_iso8601(s) -> bool:
    return isinstance(s, str) and bool(ISO8601_RE.match(s))


def _is_non_negative_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


def _err(obj: dict):
    print(json.dumps(obj, ensure_ascii=False, indent=2), file=sys.stderr)


def validate(instance_id: str, strict: bool = False) -> dict:
    errors = []

    instance_path = INSTANCES_DIR / f"{instance_id}.json"
    if not instance_path.exists():
        return {"valid": False, "errors": [f"Instance 文件不存在: {instance_path}"]}

    # 1. 语法校验
    try:
        with open(instance_path, "r", encoding="utf-8") as f:
            inst = json.load(f)
    except json.JSONDecodeError as e:
        return {"valid": False, "errors": [f"JSON 解析失败: {e}"]}

    if not isinstance(inst, dict):
        return {"valid": False, "errors": ["Instance 根节点必须是对象"]}

    # schema_version
    if inst.get("schema_version") != "2.0.0":
        errors.append(f"schema_version 必须是 '2.0.0'，当前: {inst.get('schema_version')}")

    # instance_id 一致性
    if inst.get("instance_id") != instance_id:
        errors.append(f"instance_id 不匹配: 文件名 {instance_id} vs 内容 {inst.get('instance_id')}")

    # status
    status = inst.get("status")
    if status not in VALID_INSTANCE_STATUSES:
        errors.append(f"status 必须是 {VALID_INSTANCE_STATUSES} 之一，当前: {status}")

    # created_at / updated_at
    for ts_field in ("created_at", "updated_at"):
        ts = inst.get(ts_field)
        if ts and not _is_iso8601(ts):
            errors.append(f"{ts_field} 必须是 ISO8601 格式，当前: {ts}")

    # special_instructions
    si = inst.get("special_instructions")
    if si is not None and not isinstance(si, str):
        errors.append(f"special_instructions 必须是字符串或 null，当前类型: {type(si).__name__}")

    # 收集 instance 内所有 stage_id，供后续交叉引用
    stages = inst.get("stages", [])
    instance_stage_ids = set()
    if isinstance(stages, list):
        for stage in stages:
            if isinstance(stage, dict):
                sid = stage.get("stage_id")
                if sid:
                    instance_stage_ids.add(sid)

    # current_stage
    current_stage = inst.get("current_stage")
    if current_stage and current_stage not in instance_stage_ids:
        errors.append(f"current_stage '{current_stage}' 不存在于 stages 列表中")

    # reference
    ref = inst.get("reference")
    if not isinstance(ref, dict):
        errors.append("reference 必须是对象")
    else:
        wf_id = ref.get("workflow_id")
        version = ref.get("version")
        snapshot_hash = ref.get("snapshot_hash")

        if not wf_id or not version:
            errors.append("reference.workflow_id 和 reference.version 必填")
        else:
            ref_file = WORKFLOWS_DIR / f"{wf_id}@{version}" / "WORKFLOW.yaml"
            if not ref_file.exists():
                errors.append(f"Reference 文件不存在: {ref_file}")
            elif snapshot_hash:
                current_hash = sha256_file(ref_file)
                if current_hash != snapshot_hash:
                    errors.append(f"snapshot_hash 不匹配: 绑定 {snapshot_hash} vs 当前 {current_hash}")

            # Stage 合法性（需要读取 Reference）
            ref_stages = set()
            ref_skill_ids = set()
            ref_edges = []
            if ref_file.exists():
                ref_stages = _extract_stage_ids_from_reference(ref_file)
                ref_skill_ids = _extract_skill_ids_from_reference(ref_file)
                ref_edges = _extract_edges_from_reference(ref_file)
                ref_model_tiers = _extract_model_tiers_from_reference(ref_file)

                for stage in stages:
                    if not isinstance(stage, dict):
                        continue
                    sid = stage.get("stage_id")
                    if sid and ref_stages and sid not in ref_stages:
                        errors.append(f"stage_id '{sid}' 不存在于 Reference 中")

                    # skill_id 存在于 Reference
                    sk_id = stage.get("skill_id")
                    if sk_id and ref_skill_ids and sk_id not in ref_skill_ids:
                        errors.append(f"skill_id '{sk_id}' (stage '{sid}') 不存在于 Reference 中")

                    # model_tier 存在于 Reference 的 model_tiers 中（若 Reference 定义了档位列表）
                    mt = stage.get("model_tier")
                    if mt is not None and ref_model_tiers and mt not in ref_model_tiers:
                        errors.append(f"model_tier '{mt}' (stage '{sid}') 不在 Reference 的 model_tiers {sorted(ref_model_tiers)} 中")

                # 循环计数器
                for stage in stages:
                    if not isinstance(stage, dict):
                        continue
                    sid = stage.get("stage_id")
                    loop_counter = stage.get("loop_counter", 0)
                    max_loop = _get_max_loop_for_stage(ref_edges, sid)
                    if max_loop is not None and loop_counter > max_loop:
                        errors.append(f"stage '{sid}' loop_counter ({loop_counter}) > max_loop ({max_loop})")

    # edges（Instance 级，应与 Reference 一致）
    inst_edges = inst.get("edges", [])
    if not isinstance(inst_edges, list):
        errors.append("edges 必须是数组")
    else:
        for i, edge in enumerate(inst_edges):
            if not isinstance(edge, dict):
                errors.append(f"edges[{i}] 必须是对象")
                continue
            for req in ("from", "to", "condition"):
                if req not in edge:
                    errors.append(f"edges[{i}].{req} 必填")
            cond = edge.get("condition")
            if cond and cond not in VALID_EDGE_CONDITIONS:
                errors.append(f"edges[{i}].condition 无效: {cond}")
            fr = edge.get("from")
            to = edge.get("to")
            if fr and instance_stage_ids and fr not in instance_stage_ids:
                errors.append(f"edges[{i}].from '{fr}' 不存在于 stages 中")
            if to and instance_stage_ids and to not in instance_stage_ids:
                errors.append(f"edges[{i}].to '{to}' 不存在于 stages 中")
            # max_loop / loop_counter_stage 配对检查
            if edge.get("max_loop") is not None:
                if not _is_non_negative_int(edge.get("max_loop")):
                    errors.append(f"edges[{i}].max_loop 必须是非负整数")
                if not edge.get("loop_counter_stage"):
                    errors.append(f"edges[{i}] 有 max_loop 时 loop_counter_stage 必填")

    # concurrency_rules
    cr = inst.get("concurrency_rules")
    if cr is not None:
        if not isinstance(cr, dict):
            errors.append("concurrency_rules 必须是对象")
        else:
            mpa = cr.get("max_parallel_agents")
            if mpa is not None and not _is_non_negative_int(mpa):
                errors.append(f"concurrency_rules.max_parallel_agents 必须是非负整数")
            aps = cr.get("allowed_parallel_stages")
            if aps is not None and not isinstance(aps, list):
                errors.append("concurrency_rules.allowed_parallel_stages 必须是数组")
            elif isinstance(aps, list):
                for group_idx, group in enumerate(aps):
                    if not isinstance(group, list):
                        errors.append(f"allowed_parallel_stages[{group_idx}] 必须是数组")
                    else:
                        for sid in group:
                            if sid not in instance_stage_ids:
                                errors.append(f"allowed_parallel_stages 中的 '{sid}' 不存在于 stages 中")
            rcc = cr.get("resource_conflict_check")
            if rcc is not None and not isinstance(rcc, bool):
                errors.append("concurrency_rules.resource_conflict_check 必须是布尔值")

    # conflict_resolution
    cres = inst.get("conflict_resolution")
    if cres is not None and not isinstance(cres, dict):
        errors.append("conflict_resolution 必须是对象或 null")

    # stages
    if not isinstance(stages, list):
        errors.append("stages 必须是数组")
    else:
        running_count = 0
        for i, stage in enumerate(stages):
            if not isinstance(stage, dict):
                errors.append(f"stages[{i}] 必须是对象")
                continue
            s_status = stage.get("status")
            if s_status not in VALID_STAGE_STATUSES:
                errors.append(f"stages[{i}].status 无效: {s_status}")
            if s_status == "RUNNING":
                running_count += 1

            sid = stage.get("stage_id")

            # assigned_agent_id
            aa = stage.get("assigned_agent_id")
            if aa is not None and (not isinstance(aa, str) or not aa.strip()):
                errors.append(f"stages[{i}].assigned_agent_id 必须是非空字符串")

            # system_agent_id
            sa = stage.get("system_agent_id")
            if sa is not None and sa is not None and not isinstance(sa, str):
                errors.append(f"stages[{i}].system_agent_id 必须是字符串或 null")

            # model_tier
            mt = stage.get("model_tier")
            if mt is not None and not isinstance(mt, str):
                errors.append(f"stages[{i}].model_tier 必须是字符串或 null")

            # resolved_model
            rm = stage.get("resolved_model")
            if rm is not None and not isinstance(rm, str):
                errors.append(f"stages[{i}].resolved_model 必须是字符串或 null")

            # input_message_ids
            ims = stage.get("input_message_ids", [])
            if isinstance(ims, list):
                for msg_id in ims:
                    if not _find_message_file(msg_id):
                        errors.append(f"stages[{i}].input_message_ids 中消息不存在: {msg_id}")
            else:
                errors.append(f"stages[{i}].input_message_ids 必须是数组")

            # output_message_id
            out_msg = stage.get("output_message_id")
            if out_msg:
                if not _find_message_file(out_msg):
                    errors.append(f"stages[{i}].output_message_id 指向的消息不存在: {out_msg}")

            # history_message_ids
            hist = stage.get("history_message_ids", [])
            if isinstance(hist, list):
                for msg_id in hist:
                    if not _find_message_file(msg_id):
                        errors.append(f"stages[{i}].history_message_ids 中消息不存在: {msg_id}")
            else:
                errors.append(f"stages[{i}].history_message_ids 必须是数组")

            # start_time / end_time
            for tf in ("start_time", "end_time"):
                tv = stage.get(tf)
                if tv is not None and tv is not None and not _is_iso8601(tv):
                    errors.append(f"stages[{i}].{tf} 必须是 ISO8601 格式或 null")

            # deviation_flag
            df = stage.get("deviation_flag")
            if df is not None and not isinstance(df, bool):
                errors.append(f"stages[{i}].deviation_flag 必须是布尔值")

            # blocked_by_confirm
            bbc = stage.get("blocked_by_confirm")
            if bbc is not None and not isinstance(bbc, bool):
                errors.append(f"stages[{i}].blocked_by_confirm 必须是布尔值")

            # loop_counter
            lc = stage.get("loop_counter")
            if lc is not None and not _is_non_negative_int(lc):
                errors.append(f"stages[{i}].loop_counter 必须是非负整数")

            # attempt_count
            ac = stage.get("attempt_count")
            if ac is not None and not _is_non_negative_int(ac):
                errors.append(f"stages[{i}].attempt_count 必须是非负整数")

            # Git 锚点（strict 模式）
            if strict and s_status in ("DONE", "RUNNING"):
                tag = stage.get("git_anchor_tag")
                if not tag:
                    errors.append(f"stages[{i}].git_anchor_tag 在 strict 模式下必填（status={s_status}）")
                elif not _git_tag_exists(tag):
                    errors.append(f"stages[{i}].git_anchor_tag 不存在于 git: {tag}")

        # 并发一致性
        active_agents = inst.get("execution_summary", {}).get("active_agents", 0)
        if active_agents != running_count:
            errors.append(f"active_agents ({active_agents}) 与 RUNNING stage 数量 ({running_count}) 不一致")

    # execution_summary
    es = inst.get("execution_summary")
    if es is not None:
        if not isinstance(es, dict):
            errors.append("execution_summary 必须是对象")
        else:
            cs = es.get("completed_stages")
            if cs is not None:
                if not _is_non_negative_int(cs):
                    errors.append("execution_summary.completed_stages 必须是非负整数")
                elif isinstance(stages, list) and cs > len(stages):
                    errors.append(f"execution_summary.completed_stages ({cs}) 不能大于 stages 总数 ({len(stages)})")
            ts = es.get("total_stages")
            if ts is not None:
                if not _is_non_negative_int(ts):
                    errors.append("execution_summary.total_stages 必须是非负整数")
                elif isinstance(stages, list) and ts != len(stages):
                    errors.append(f"execution_summary.total_stages ({ts}) 与 stages 数组长度 ({len(stages)}) 不一致")
            lmi = es.get("last_message_id")
            if lmi and not _find_message_file(lmi):
                errors.append(f"execution_summary.last_message_id 指向的消息不存在: {lmi}")
            tl = es.get("total_loops")
            if tl is not None and not _is_non_negative_int(tl):
                errors.append("execution_summary.total_loops 必须是非负整数")

    # pending_confirmations
    pc = inst.get("pending_confirmations", [])
    if not isinstance(pc, list):
        errors.append("pending_confirmations 必须是数组")
    else:
        for msg_id in pc:
            if not _find_message_file(msg_id):
                errors.append(f"pending_confirmations 中消息不存在: {msg_id}")

    # deviation_log
    dl = inst.get("deviation_log", [])
    if isinstance(dl, list):
        for i, entry in enumerate(dl):
            if not isinstance(entry, dict):
                errors.append(f"deviation_log[{i}] 必须是对象")
                continue
            ts = entry.get("timestamp")
            if ts and not _is_iso8601(ts):
                errors.append(f"deviation_log[{i}].timestamp 必须是 ISO8601 格式")
            reason = entry.get("reason")
            if reason is not None and (not isinstance(reason, str) or not reason.strip()):
                errors.append(f"deviation_log[{i}].reason 必须是非空字符串")
            uc = entry.get("user_confirmed")
            if uc is not None and not isinstance(uc, bool):
                errors.append(f"deviation_log[{i}].user_confirmed 必须是布尔值")
            osid = entry.get("original_stage_id")
            if osid and osid not in instance_stage_ids:
                errors.append(f"deviation_log[{i}].original_stage_id '{osid}' 不存在于 stages 中")
            impact = entry.get("impact_stages", [])
            if isinstance(impact, list):
                for isid in impact:
                    if isid not in instance_stage_ids:
                        errors.append(f"deviation_log[{i}].impact_stages 中的 '{isid}' 不存在于 stages 中")
            else:
                errors.append(f"deviation_log[{i}].impact_stages 必须是数组")
            res = entry.get("resolution")
            if res is not None and (not isinstance(res, str) or not res.strip()):
                errors.append(f"deviation_log[{i}].resolution 必须是非空字符串")
            ris = entry.get("reported_in_summary")
            if ris is not None and not isinstance(ris, bool):
                errors.append(f"deviation_log[{i}].reported_in_summary 必须是布尔值")
            dtype = entry.get("type")
            if dtype not in VALID_DEVIATION_TYPES:
                errors.append(f"deviation_log[{i}].type 无效: {dtype}")

    # metadata
    metadata = inst.get("metadata")
    if metadata is not None:
        if not isinstance(metadata, dict):
            errors.append("metadata 必须是对象")
        else:
            lrt = metadata.get("last_rollback_target")
            if lrt and lrt not in instance_stage_ids:
                errors.append(f"metadata.last_rollback_target '{lrt}' 不存在于 stages 中")
            lra = metadata.get("last_rollback_at")
            if lra is not None and not _is_iso8601(lra):
                errors.append("metadata.last_rollback_at 必须是 ISO8601 格式或 null")

    # 状态流转检查：DONE -> PENDING 需要 rolled_back_at
    for stage in stages:
        if not isinstance(stage, dict):
            continue
        hist = stage.get("history_message_ids", [])
        if stage.get("status") == "PENDING" and hist:
            metadata = inst.get("metadata", {})
            if not metadata.get("last_rollback_at"):
                errors.append(
                    f"stage '{stage.get('stage_id')}' 状态为 PENDING 且有历史消息，"
                    f"说明发生了回退，但 metadata.last_rollback_at 未记录"
                )

    return {"valid": len(errors) == 0, "errors": errors}


def _extract_stage_ids_from_reference(path: Path) -> set:
    """从 Reference YAML 文件中提取 stage_id 列表"""
    try:
        import yaml
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {s["stage_id"] for s in data.get("stages", []) if isinstance(s, dict) and "stage_id" in s}
    except Exception:
        pass
    return set()


def _extract_skill_ids_from_reference(path: Path) -> set:
    """从 Reference YAML 文件中提取 skill_id 列表"""
    try:
        import yaml
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {s["skill_id"] for s in data.get("stages", []) if isinstance(s, dict) and "skill_id" in s}
    except Exception:
        pass
    return set()


def _extract_model_tiers_from_reference(path: Path) -> set:
    """从 Reference YAML 文件中提取 model_tiers 列表"""
    try:
        import yaml
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            mt = data.get("model_tiers", [])
            if isinstance(mt, list):
                return {str(item) for item in mt if isinstance(item, str)}
    except Exception:
        pass
    return set()


def _extract_edges_from_reference(path: Path) -> list:
    """从 Reference YAML 文件中提取 edges 列表"""
    try:
        import yaml
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data.get("edges", [])
    except Exception:
        pass
    return []


def _get_max_loop_for_stage(edges: list, stage_id: str) -> int | None:
    """从 edges 中找到 loop_counter_stage 等于 stage_id 的 edge，返回 max_loop"""
    for edge in edges:
        if isinstance(edge, dict) and edge.get("loop_counter_stage") == stage_id:
            return edge.get("max_loop")
    return None


def _find_message_file(message_id: str) -> Path | None:
    """在 messages/ 下按日期目录搜索 message_id.json"""
    if not MESSAGES_DIR.exists():
        return None
    for date_dir in MESSAGES_DIR.iterdir():
        if date_dir.is_dir():
            candidate = date_dir / f"{message_id}.json"
            if candidate.exists():
                return candidate
    return None


def _git_tag_exists(tag: str) -> bool:
    try:
        result = subprocess.run(
            ["git", "tag", "-l", tag],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        return tag in result.stdout
    except Exception:
        return False


def main():
    parser = argparse.ArgumentParser(description="Instance 状态机校验")
    parser.add_argument("--instance", required=True, help="Instance ID")
    parser.add_argument("--strict", action="store_true", help="严格模式：检查 git tag 存在性")
    args = parser.parse_args()

    result = validate(args.instance, strict=args.strict)

    if result["valid"]:
        print(json.dumps({"valid": True}, ensure_ascii=False, indent=2))
        sys.exit(0)
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
