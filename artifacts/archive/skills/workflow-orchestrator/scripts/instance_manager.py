#!/usr/bin/env python3
"""
Instance Manager

工作流实例管理工具。

【核心用途】创建实例（推荐通过脚本执行，节省编排器上下文）
    python instance_manager.py create \
        --workflow mathematical-model --version 1.0.0 \
        --special-instructions "优先处理性能瓶颈"

【可选辅助】以下子命令保留供编排器可选调用，但编排器也可直接读写 Instance JSON：
    update-stage  : 原子更新 stage 状态（含流转校验、重试计数、pending_confirmations 维护）
    log-deviation : 记录偏差日志（含规范枚举校验）
    rollback      : 重置 Instance 状态机（含 Git 操作指引、依赖并发任务 CANCELLED）
    restore-agent : 从 rollback 备份恢复 .agent/ 目录
"""

import argparse
import copy
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from _common import (
    atomic_write_json,
    backups_dir,
    find_project_root,
    instances_dir,
    now_iso,
    sets_dir,
    workflows_ref_dir,
)

# ── 多实例辅助函数 ──────────────────────────────────────────

def get_stage_instance_id(stage: dict) -> str:
    """获取 stage 的唯一实例标识。兼容旧数据模型（无 stage_instance_id 字段时回退到 stage_id）。"""
    return stage.get("stage_instance_id") or stage.get("stage_id", "unknown")


def find_stages(instance: dict, stage_id: str = None, stage_instance_id: str = None) -> list:
    """
    查找匹配的 stage 记录。
    - stage_instance_id 精确匹配（优先级最高）
    - stage_id 匹配（可能返回多条）
    - 按 stages 数组原始顺序返回
    """
    matches = []
    for s in instance.get("stages", []):
        if stage_instance_id:
            if get_stage_instance_id(s) == stage_instance_id:
                return [s]
        elif stage_id:
            if s.get("stage_id") == stage_id:
                matches.append(s)
    return matches


def count_stage_instances(instance: dict, stage_id: str) -> int:
    """统计指定 stage_id 的实例数量。"""
    return sum(1 for s in instance.get("stages", []) if s.get("stage_id") == stage_id)

# ── 规范常量 ──────────────────────────────────────────────

# 偏差记录 type 枚举（规范 3.4）
DEVIATION_TYPES = {
    "USER_OVERRIDE",
    "USER_ROLLBACK",
    "SKILL_FAILURE",
    "TIMEOUT",
    "RESOURCE_CONFLICT",
    "MANUAL_ADJUSTMENT",
    "LOOP_EXCEEDED",
}

# Stage 状态流转表（规范 6.1）
VALID_TRANSITIONS = {
    "PENDING": ["RUNNING", "SKIPPED"],
    "RUNNING": ["DONE", "ERROR", "BLOCKED"],
    "BLOCKED": ["RUNNING", "CANCELLED"],
    "DONE": ["SUPERSEDED"],
    "ERROR": ["PENDING", "CANCELLED"],
    "SKIPPED": [],
    "CANCELLED": [],
    "SUPERSEDED": [],
}

# Instance 状态枚举（规范 6.2）
INSTANCE_STATUSES = {
    "PLANNING", "EXECUTING", "SUSPENDED", "COMPLETED", "FAILED", "CANCELLED"
}


# ── YAML 解析 ─────────────────────────────────────────────

def parse_reference_yaml(filepath: Path) -> dict:
    """
    解析独立的 WORKFLOW.yaml 文件。
    优先使用标准 yaml 库；若不可用则回退到内置简单解析器。
    """
    if not filepath.exists():
        raise ValueError(f"YAML file not found: {filepath}")

    yaml_content = filepath.read_text(encoding="utf-8")

    try:
        import yaml
        return yaml.safe_load(yaml_content) or {}
    except ImportError:
        pass

    # 内置回退解析器（仅处理扁平结构，不支持深层嵌套）
    def parse_value(raw: str):
        val = raw.strip().strip('"\'')
        if val.lower() == "true":
            return True
        if val.lower() == "false":
            return False
        if val.isdigit():
            return int(val)
        if val == "[]":
            return []
        return val

    result = {}
    lines = yaml_content.split("\n")
    i = 0
    current_list = None
    current_obj = None

    while i < len(lines):
        line = lines[i]
        stripped = line.rstrip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue

        indent = len(line) - len(line.lstrip())

        # 顶层键
        if indent == 0 and ":" in stripped:
            key = stripped.split(":", 1)[0].strip()
            rest = stripped.split(":", 1)[1].strip()
            current_list = None
            current_obj = None

            if rest == "":
                result[key] = []
                current_list = result[key]
            else:
                result[key] = parse_value(rest)

        # 列表项
        elif stripped.lstrip().startswith("-") and current_list is not None:
            item_text = stripped.lstrip()[1:].strip()
            if ":" in item_text:
                obj = {}
                key, val = item_text.split(":", 1)
                obj[key.strip()] = parse_value(val)

                j = i + 1
                while j < len(lines):
                    next_line = lines[j]
                    next_stripped = next_line.rstrip()
                    if not next_stripped or next_stripped.startswith("#"):
                        j += 1
                        continue
                    next_indent = len(next_line) - len(next_line.lstrip())
                    if next_indent <= 2 and next_stripped and not next_stripped.lstrip().startswith("-"):
                        break
                    if ":" in next_stripped and not next_stripped.lstrip().startswith("-"):
                        k, v = next_stripped.split(":", 1)
                        obj[k.strip()] = parse_value(v)
                    elif next_stripped.lstrip().startswith("-"):
                        break
                    j += 1
                current_list.append(obj)
                current_obj = obj
                i = j - 1
            else:
                current_list.append(parse_value(item_text))

        # 子对象属性
        elif indent >= 2 and current_obj is not None and ":" in stripped:
            k, v = stripped.split(":", 1)
            current_obj[k.strip()] = parse_value(v)

        i += 1

    return result


# ── 辅助函数 ──────────────────────────────────────────────

def calc_file_hash(filepath: Path) -> str:
    """计算文件 SHA256。"""
    h = hashlib.sha256()
    h.update(filepath.read_bytes())
    return f"sha256:{h.hexdigest()}"


def generate_instance_id(workflow_id: str) -> str:
    """生成实例 ID: wf-{workflow_id}-{timestamp}-{seq}-{random}"""
    ts = datetime.now().strftime("%Y%m%d")
    inst_dir = instances_dir()
    seq = 1
    if inst_dir.exists():
        existing = list(inst_dir.glob(f"wf-{workflow_id}-{ts}-*.json"))
        seq = len(existing) + 1
    rand = os.urandom(2).hex()
    safe_wf_id = re.sub(r"[^a-zA-Z0-9_-]", "-", workflow_id)
    return f"wf-{safe_wf_id}-{ts}-{seq:03d}-{rand}"


def generate_set_id(workflow_id: str) -> str:
    """生成 Set ID: set-{workflow_id}-{timestamp}-{seq}-{random}"""
    ts = datetime.now().strftime("%Y%m%d")
    sdir = sets_dir()
    seq = 1
    if sdir.exists():
        existing = list(sdir.glob(f"set-{workflow_id}-{ts}-*.json"))
        seq = len(existing) + 1
    rand = os.urandom(2).hex()
    safe_wf_id = re.sub(r"[^a-zA-Z0-9_-]", "-", workflow_id)
    return f"set-{safe_wf_id}-{ts}-{seq:03d}-{rand}"


def find_workflow_ref_dir(workflow_id: str, version: str) -> Path | None:
    """
    查找 Reference 工作流目录。
    严格使用规范路径 .claude/workflows/<workflow_id>@<version>/。
    """
    primary = workflows_ref_dir() / f"{workflow_id}@{version}"
    if primary.exists():
        return primary

    # 若版本号未指定或部分匹配，扫描 primary 目录
    candidates = []
    if workflows_ref_dir().exists():
        for entry in workflows_ref_dir().iterdir():
            if entry.is_dir() and entry.name.startswith(f"{workflow_id}@"):
                candidates.append(entry)
    if len(candidates) == 1:
        return candidates[0]

    return None


def build_instance(workflow_id: str, version: str, ref_dir: Path, special_instructions: str = "") -> dict:
    """从 Reference 目录构建 Instance 状态机。"""
    yaml_path = ref_dir / "WORKFLOW.yaml"
    ref_data = parse_reference_yaml(yaml_path)

    instance_id = generate_instance_id(workflow_id)
    now = now_iso()

    def normalize_retry_policy(rp):
        """修复 YAML 1.1 布尔别名导致 'on' 键被解析为 True 的问题。"""
        if not isinstance(rp, dict):
            return {"max_attempts": 1, "on": []}
        result = dict(rp)
        if True in result:
            result["on"] = result.pop(True)
        if "on" not in result:
            result["on"] = []
        return result

    # 模型档位配置（可选）
    model_tiers = ref_data.get("model_tiers", [])
    if not isinstance(model_tiers, list):
        model_tiers = []
    default_model_tier = ref_data.get("default_model_tier")
    if default_model_tier and default_model_tier not in model_tiers:
        default_model_tier = None
    if not default_model_tier and model_tiers:
        default_model_tier = model_tiers[0]

    stages = []
    for ref_stage in ref_data.get("stages", []):
        sid = ref_stage.get("stage_id", "unknown")
        # 解析 model_tier：显式声明 -> 全局默认 -> null
        stage_model_tier = ref_stage.get("model_tier")
        if stage_model_tier and stage_model_tier in model_tiers:
            pass
        elif default_model_tier:
            stage_model_tier = default_model_tier
        else:
            stage_model_tier = None

        # 单实例：stage_instance_id 与 stage_id 相同，兼容旧数据模型
        stage = {
            "stage_id": sid,
            "stage_instance_id": sid,
            "status": "PENDING",
            "skill_id": ref_stage.get("skill_id", ""),
            "model_tier": stage_model_tier,
            "resolved_model": None,  # 由编排器在启动 SubAgent 前根据 Skill 映射解析并回填
            "mandatory": ref_stage.get("mandatory", True),
            "confirmation_point": ref_stage.get("confirmation_point", False),
            "retry_policy": normalize_retry_policy(ref_stage.get("retry_policy")),
            "output_message_id": None,
            "history_message_ids": [],
            "git_anchor_tag": None,
            "loop_counter": 0,
            "attempt_count": 0,
            "blocked_by_confirm": False,
            # 非规范扩展字段（编排器实用）
            "assigned_agent_id": None,
            "system_agent_id": None,
            "start_time": None,
            "end_time": None,
            # fan_out 配置（可选，从 YAML 拷贝）
            "fan_out": ref_stage.get("fan_out"),
        }
        stages.append(stage)

    edges = []
    for e in ref_data.get("edges", []):
        edges.append({
            "from": e.get("from", ""),
            "to": e.get("to", ""),
            "condition": e.get("condition", "always"),
            "max_loop": e.get("max_loop", None),
            "loop_counter_stage": e.get("loop_counter_stage", None),
            "aggregation": e.get("aggregation", "all"),
        })

    instance = {
        "schema_version": "2.0.0",
        "instance_id": instance_id,
        "reference": {
            "workflow_id": workflow_id,
            "version": version,
            "snapshot_hash": calc_file_hash(yaml_path),
            "model_tiers": model_tiers,
            "default_model_tier": default_model_tier,
        },
        "status": "PLANNING",
        "created_at": now,
        "updated_at": now,
        "current_stage": stages[0]["stage_id"] if stages else None,
        "stages": stages,
        "edges": edges,
        "concurrency_rules": ref_data.get("concurrency_rules", {}),
        "conflict_resolution": ref_data.get("conflict_resolution", {}),
        "git_anchors": ref_data.get("git_anchors", {"enabled": True, "tag_prefix": "wf", "preserve_paths": [".agent/"]}),
        "pending_confirmations": [],
        "deviation_log": [],
        "execution_summary": {
            "completed_stages": 0,
            "total_stages": len(stages),
            "active_agents": 0,
            "last_message_id": None,
            "total_loops": 0,
        },
        "special_instructions": special_instructions,
        "metadata": {},
    }

    return instance


# ── 子命令实现 ────────────────────────────────────────────

def _create_instance_core(workflow_id: str, version: str, ref_dir: Path, special_instructions: str = "") -> dict:
    """核心实例创建逻辑，返回 instance dict，不输出到 stdout。"""
    instance = build_instance(workflow_id, version, ref_dir, special_instructions)
    out_path = instances_dir() / f"{instance['instance_id']}.json"
    atomic_write_json(out_path, instance)
    return instance


def cmd_create(args):
    inst_dir = instances_dir()
    ref_dir = find_workflow_ref_dir(args.workflow, args.version)

    if ref_dir is None:
        print(json.dumps({
            "error": f"Reference directory not found for {args.workflow}@{args.version}",
            "searched": [
                str(workflows_ref_dir() / f"{args.workflow}@{args.version}").replace("\\", "/"),
            ],
        }, ensure_ascii=False, indent=2))
        sys.exit(1)

    # 若通过扫描找到，重新解析版本号
    dir_version = ref_dir.name.split("@", 1)[1]
    args.version = dir_version

    instance = _create_instance_core(
        args.workflow,
        args.version,
        ref_dir,
        args.special_instructions or "",
    )

    out_path = inst_dir / f"{instance['instance_id']}.json"

    # 从已构建的 instance 中提取 model_tiers 等元数据用于输出摘要
    ref_meta = instance.get("reference", {})

    print(json.dumps({
        "success": True,
        "instance_id": instance["instance_id"],
        "path": str(out_path).replace("\\", "/"),
        "workflow_id": ref_meta.get("workflow_id"),
        "version": ref_meta.get("version"),
        "total_stages": len(instance["stages"]),
        "first_stage": instance["current_stage"],
        "model_tiers": ref_meta.get("model_tiers", []),
        "default_model_tier": ref_meta.get("default_model_tier"),
        "stages": [
            {
                "stage_id": s["stage_id"],
                "model_tier": s.get("model_tier"),
                "resolved_model": s.get("resolved_model"),
            }
            for s in instance["stages"]
        ],
    }, ensure_ascii=False, indent=2))


def cmd_update_stage(args):
    inst_dir = instances_dir()
    inst_path = inst_dir / f"{args.instance}.json"

    if not inst_path.exists():
        print(json.dumps({"error": f"Instance not found: {args.instance}"}, ensure_ascii=False, indent=2))
        sys.exit(1)

    instance = json.loads(inst_path.read_text(encoding="utf-8"))

    # 解析定位参数：--stage-instance-id 优先，回退到 --stage
    if args.stage_instance_id:
        matches = find_stages(instance, stage_instance_id=args.stage_instance_id)
    elif args.stage:
        matches = find_stages(instance, stage_id=args.stage)
    else:
        print(json.dumps({"error": "Either --stage or --stage-instance-id is required"}, ensure_ascii=False, indent=2))
        sys.exit(1)

    if not matches:
        target = args.stage_instance_id or args.stage
        print(json.dumps({"error": f"Stage not found: {target}"}, ensure_ascii=False, indent=2))
        sys.exit(1)

    if len(matches) > 1:
        print(json.dumps({
            "error": f"Ambiguous: {len(matches)} instances match stage_id '{args.stage}'",
            "hint": "Use --stage-instance-id to specify exact instance",
            "candidates": [
                {"stage_instance_id": get_stage_instance_id(s), "status": s["status"], "assigned_agent_id": s.get("assigned_agent_id")}
                for s in matches
            ],
        }, ensure_ascii=False, indent=2))
        sys.exit(1)

    stage = matches[0]

    old_status = stage["status"]
    new_status = args.status

    # ── 特殊流转校验 ─────────────────────────────────────

    # ERROR -> PENDING 重试校验（规范 7.1）
    if old_status == "ERROR" and new_status == "PENDING":
        retry_policy = stage.get("retry_policy", {"max_attempts": 1, "on": []})
        max_attempts = retry_policy.get("max_attempts", 1)
        if stage.get("attempt_count", 0) >= max_attempts and not args.force:
            print(json.dumps({
                "error": f"Retry limit exceeded for stage {args.stage}: "
                         f"attempt_count={stage['attempt_count']} >= max_attempts={max_attempts}",
                "valid": ["CANCELLED"],
            }, ensure_ascii=False, indent=2))
            sys.exit(1)

    # DONE -> PENDING 回退校验（规范 8.3 / 5.2）
    if old_status == "DONE" and new_status == "PENDING":
        meta = instance.get("metadata", {})
        if not meta.get("rolled_back_at") and not args.force:
            print(json.dumps({
                "error": "Invalid state transition: DONE -> PENDING without rollback metadata",
                "hint": "Use rollback command first, or set --force to override",
            }, ensure_ascii=False, indent=2))
            sys.exit(1)

    # 通用流转校验
    allowed = VALID_TRANSITIONS.get(old_status, [])
    if new_status not in allowed and not args.force:
        print(json.dumps({
            "error": f"Invalid state transition: {old_status} -> {new_status}",
            "valid": allowed,
        }, ensure_ascii=False, indent=2))
        sys.exit(1)

    now = now_iso()

    # ── message 与 history 维护 ───────────────────────────
    if args.message_id:
        if new_status in ("DONE", "ERROR", "SKIPPED", "CANCELLED", "BLOCKED"):
            # 保存旧的 output_message_id 到 history（规范 5.2 第5步）
            old_msg = stage.get("output_message_id")
            if old_msg and old_msg not in stage["history_message_ids"]:
                stage["history_message_ids"].append(old_msg)

            stage["output_message_id"] = args.message_id
            if args.message_id not in stage["history_message_ids"]:
                stage["history_message_ids"].append(args.message_id)

        # pending_confirmations 维护（规范 3.2 / 6.1）
        if new_status == "BLOCKED":
            stage["blocked_by_confirm"] = True
            pends = instance.setdefault("pending_confirmations", [])
            if args.message_id not in pends:
                pends.append(args.message_id)

    # BLOCKED -> RUNNING：解除 pending_confirmations
    if old_status == "BLOCKED" and new_status == "RUNNING":
        stage["blocked_by_confirm"] = False
        old_msg = stage.get("output_message_id")
        if old_msg:
            pends = instance.get("pending_confirmations", [])
            if old_msg in pends:
                pends.remove(old_msg)

    # ERROR -> PENDING：递增 attempt_count（规范 7.1）
    if old_status == "ERROR" and new_status == "PENDING":
        stage["attempt_count"] = stage.get("attempt_count", 0) + 1

    # 设置新状态
    stage["status"] = new_status
    instance["updated_at"] = now

    if new_status == "RUNNING":
        stage["start_time"] = now
        stage["assigned_agent_id"] = args.agent_id or stage.get("assigned_agent_id")

    if args.system_agent_id:
        stage["system_agent_id"] = args.system_agent_id

    if args.resolved_model:
        stage["resolved_model"] = args.resolved_model

    if new_status in ("DONE", "ERROR", "SKIPPED", "CANCELLED"):
        stage["end_time"] = now

    # ── Instance 状态自动判断（规范 6.2）──────────────────
    running = [s for s in instance["stages"] if s["status"] == "RUNNING"]
    blocked = [s for s in instance["stages"] if s["status"] == "BLOCKED"]
    pending = [s for s in instance["stages"] if s["status"] == "PENDING"]
    errors = [s for s in instance["stages"] if s["status"] == "ERROR"]
    done_or_skipped = [s for s in instance["stages"] if s["status"] in ("DONE", "SKIPPED")]

    if blocked:
        instance["status"] = "SUSPENDED"
    elif running:
        instance["status"] = "EXECUTING"
    elif errors:
        # 检查是否所有 ERROR 都已耗尽重试次数
        fatal = 0
        for s in errors:
            rp = s.get("retry_policy", {"max_attempts": 1, "on": []})
            if s.get("attempt_count", 0) >= rp.get("max_attempts", 1):
                fatal += 1
        if fatal == len(errors) and not pending:
            instance["status"] = "FAILED"
        else:
            instance["status"] = "EXECUTING"
    elif len(done_or_skipped) == len(instance["stages"]):
        instance["status"] = "COMPLETED"
    else:
        instance["status"] = "PLANNING"

    # current_stage 指向最靠前的非终止活跃 stage
    if instance["status"] == "COMPLETED":
        instance["current_stage"] = None
    else:
        active_seq = running + pending + blocked
        if active_seq:
            instance["current_stage"] = active_seq[0]["stage_id"]
        else:
            instance["current_stage"] = None

    # execution_summary 更新
    instance["execution_summary"]["completed_stages"] = len(done_or_skipped)
    instance["execution_summary"]["active_agents"] = len(running)
    if args.message_id:
        instance["execution_summary"]["last_message_id"] = args.message_id

    atomic_write_json(inst_path, instance)

    print(json.dumps({
        "success": True,
        "instance_id": args.instance,
        "stage_id": stage["stage_id"],
        "stage_instance_id": get_stage_instance_id(stage),
        "old_status": old_status,
        "new_status": new_status,
        "instance_status": instance["status"],
        "attempt_count": stage.get("attempt_count", 0),
    }, ensure_ascii=False, indent=2))


def cmd_skip_stage(args):
    """将 stage 标记为 SKIPPED，并记录预检跳过元数据。"""
    inst_dir = instances_dir()
    inst_path = inst_dir / f"{args.instance}.json"

    if not inst_path.exists():
        print(json.dumps({"error": f"Instance not found: {args.instance}"}, ensure_ascii=False, indent=2))
        sys.exit(1)

    instance = json.loads(inst_path.read_text(encoding="utf-8"))

    if args.stage_instance_id:
        matches = find_stages(instance, stage_instance_id=args.stage_instance_id)
    elif args.stage:
        matches = find_stages(instance, stage_id=args.stage)
    else:
        print(json.dumps({"error": "Either --stage or --stage-instance-id is required"}, ensure_ascii=False, indent=2))
        sys.exit(1)

    if not matches:
        target = args.stage_instance_id or args.stage
        print(json.dumps({"error": f"Stage not found: {target}"}, ensure_ascii=False, indent=2))
        sys.exit(1)

    if len(matches) > 1:
        print(json.dumps({
            "error": f"Ambiguous: {len(matches)} instances match stage_id '{args.stage}'",
            "hint": "Use --stage-instance-id to specify exact instance",
            "candidates": [
                {"stage_instance_id": get_stage_instance_id(s), "status": s["status"]}
                for s in matches
            ],
        }, ensure_ascii=False, indent=2))
        sys.exit(1)

    stage = matches[0]

    old_status = stage["status"]

    # 只允许从 PENDING 跳过（规范 6.1：PENDING -> SKIPPED）
    if old_status != "PENDING" and not args.force:
        print(json.dumps({
            "error": f"Cannot skip stage from status '{old_status}'. Only PENDING stages can be skipped.",
            "hint": "Use --force to override.",
        }, ensure_ascii=False, indent=2))
        sys.exit(1)

    now = now_iso()
    stage["status"] = "SKIPPED"
    stage["end_time"] = now
    stage["skipped_reason"] = args.reason or "PREFLIGHT_DETECTION"
    stage["skipped_evidence"] = args.evidence or ""
    stage["skipped_at"] = now
    stage["user_confirmed"] = args.user_confirmed
    instance["updated_at"] = now

    # Instance 状态自动判断（与 update-stage 保持一致）
    running = [s for s in instance["stages"] if s["status"] == "RUNNING"]
    blocked = [s for s in instance["stages"] if s["status"] == "BLOCKED"]
    pending = [s for s in instance["stages"] if s["status"] == "PENDING"]
    errors = [s for s in instance["stages"] if s["status"] == "ERROR"]
    done_or_skipped = [s for s in instance["stages"] if s["status"] in ("DONE", "SKIPPED")]

    if blocked:
        instance["status"] = "SUSPENDED"
    elif running:
        instance["status"] = "EXECUTING"
    elif errors:
        fatal = 0
        for s in errors:
            rp = s.get("retry_policy", {"max_attempts": 1, "on": []})
            if s.get("attempt_count", 0) >= rp.get("max_attempts", 1):
                fatal += 1
        if fatal == len(errors) and not pending:
            instance["status"] = "FAILED"
        else:
            instance["status"] = "EXECUTING"
    elif len(done_or_skipped) == len(instance["stages"]):
        instance["status"] = "COMPLETED"
    else:
        instance["status"] = "PLANNING"

    # current_stage 指向最靠前的非终止活跃 stage
    if instance["status"] == "COMPLETED":
        instance["current_stage"] = None
    else:
        active_seq = running + pending + blocked
        if active_seq:
            instance["current_stage"] = active_seq[0]["stage_id"]
        else:
            instance["current_stage"] = None

    instance["execution_summary"]["completed_stages"] = len(done_or_skipped)
    instance["execution_summary"]["active_agents"] = len(running)

    atomic_write_json(inst_path, instance)

    print(json.dumps({
        "success": True,
        "instance_id": args.instance,
        "stage_id": stage["stage_id"],
        "stage_instance_id": get_stage_instance_id(stage),
        "old_status": old_status,
        "new_status": "SKIPPED",
        "instance_status": instance["status"],
        "skipped_reason": stage["skipped_reason"],
    }, ensure_ascii=False, indent=2))


def cmd_log_deviation(args):
    inst_dir = instances_dir()
    inst_path = inst_dir / f"{args.instance}.json"

    if not inst_path.exists():
        print(json.dumps({"error": f"Instance not found: {args.instance}"}, ensure_ascii=False, indent=2))
        sys.exit(1)

    # type 枚举校验（规范 3.4）
    if args.type not in DEVIATION_TYPES:
        print(json.dumps({
            "error": f"Invalid deviation type: {args.type}",
            "valid_types": sorted(DEVIATION_TYPES),
        }, ensure_ascii=False, indent=2))
        sys.exit(1)

    instance = json.loads(inst_path.read_text(encoding="utf-8"))

    deviation = {
        "timestamp": now_iso(),
        "type": args.type,
        "reason": args.reason,
        "user_confirmed": args.user_confirmed,
        "original_stage_id": args.original_stage or None,
        "impact_stages": args.impact_stages.split(",") if args.impact_stages else [],
        "resolution": args.resolution or "",
        "reported_in_summary": True,
    }

    instance["deviation_log"].append(deviation)
    instance["updated_at"] = now_iso()

    if args.original_stage:
        for s in instance["stages"]:
            if s["stage_id"] == args.original_stage:
                # 使用 metadata 记录 deviation 关联，而非 stage 上的 flag
                meta = instance.setdefault("metadata", {})
                meta.setdefault("deviation_stages", []).append(args.original_stage)

    atomic_write_json(inst_path, instance)

    print(json.dumps({
        "success": True,
        "instance_id": args.instance,
        "deviation_logged": True,
    }, ensure_ascii=False, indent=2))


def cmd_rollback(args):
    inst_dir = instances_dir()
    inst_path = inst_dir / f"{args.instance}.json"

    if not inst_path.exists():
        print(json.dumps({"error": f"Instance not found: {args.instance}"}, ensure_ascii=False, indent=2))
        sys.exit(1)

    instance = json.loads(inst_path.read_text(encoding="utf-8"))

    # 定位目标 stage 实例
    if args.target_stage_instance_id:
        matches = find_stages(instance, stage_instance_id=args.target_stage_instance_id)
    elif args.target_stage:
        matches = find_stages(instance, stage_id=args.target_stage)
    else:
        print(json.dumps({"error": "Either --target-stage or --target-stage-instance-id is required"}, ensure_ascii=False, indent=2))
        sys.exit(1)

    if not matches:
        target = args.target_stage_instance_id or args.target_stage
        print(json.dumps({"error": f"Target stage not found: {target}"}, ensure_ascii=False, indent=2))
        sys.exit(1)

    if len(matches) > 1:
        print(json.dumps({
            "error": f"Ambiguous: {len(matches)} instances match stage_id '{args.target_stage}'",
            "hint": "Use --target-stage-instance-id to specify exact instance",
            "candidates": [
                {"stage_instance_id": get_stage_instance_id(s), "status": s["status"], "assigned_agent_id": s.get("assigned_agent_id")}
                for s in matches
            ],
        }, ensure_ascii=False, indent=2))
        sys.exit(1)

    target_stage = matches[0]
    target_siid = get_stage_instance_id(target_stage)

    if not target_stage.get("git_anchor_tag"):
        print(json.dumps({
            "error": f"No git anchor tag found for stage {args.target_stage}",
            "hint": "Git anchor should be created before stage execution (see workflow spec section 5.1)",
        }, ensure_ascii=False, indent=2))
        sys.exit(1)

    # 备份 .agent/
    backup_ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = backups_dir() / args.instance / backup_ts
    backup_dir.parent.mkdir(parents=True, exist_ok=True)

    agent_dir = Path.cwd() / ".agent"
    if agent_dir.exists():
        shutil.copytree(agent_dir, backup_dir, dirs_exist_ok=True)

    # 找到目标实例在 stages 数组中的位置
    target_idx = None
    for i, s in enumerate(instance["stages"]):
        if get_stage_instance_id(s) == target_siid:
            target_idx = i
            break

    # 重置状态机（规范 5.2 第4步）
    # 按数组位置遍历，target 及之后的所有 stage 都需要重置
    for i, s in enumerate(instance["stages"]):
        if i < target_idx:
            continue

        if i == target_idx:
            # target_stage 本身重置为 PENDING
            s["status"] = "PENDING"
            s["output_message_id"] = None
            s["assigned_agent_id"] = None
            s["system_agent_id"] = None
            s["start_time"] = None
            s["end_time"] = None
            s["blocked_by_confirm"] = False
            s["loop_counter"] = 0
            s["attempt_count"] = 0
            # history_message_ids 保留（规范 5.2 第5步）
        else:
            # target_stage 之后的 stage
            if s["status"] == "RUNNING":
                # 并发活跃任务标记为 CANCELLED（规范 5.2 第4步、6.1）
                s["status"] = "CANCELLED"
                s["end_time"] = now_iso()
            else:
                s["status"] = "PENDING"
                s["output_message_id"] = None
                s["assigned_agent_id"] = None
                s["system_agent_id"] = None
                s["start_time"] = None
                s["end_time"] = None
                s["blocked_by_confirm"] = False
                s["loop_counter"] = 0
                s["attempt_count"] = 0

    instance["status"] = "EXECUTING"
    instance["current_stage"] = target_stage["stage_id"]
    instance["updated_at"] = now_iso()
    instance["execution_summary"]["completed_stages"] = len([
        s for s in instance["stages"] if s["status"] in ("DONE", "SKIPPED")
    ])
    instance["execution_summary"]["active_agents"] = 0

    # 记录 rollback 元数据（规范 8.3：DONE -> PENDING 需要 rolled_back_at）
    meta = instance.setdefault("metadata", {})
    meta["rolled_back_at"] = now_iso()
    meta["rolled_back_target"] = args.target_stage
    meta["last_rollback_backup"] = str(backup_dir).replace("\\", "/")

    atomic_write_json(inst_path, instance)

    print(json.dumps({
        "success": True,
        "instance_id": args.instance,
        "target_stage": target_stage["stage_id"],
        "target_stage_instance_id": target_siid,
        "git_anchor_tag": target_stage["git_anchor_tag"],
        "backup_path": str(backup_dir).replace("\\", "/"),
        "instruction": f"Run: git checkout {target_stage['git_anchor_tag']} -- .",
    }, ensure_ascii=False, indent=2))


def cmd_restore_agent(args):
    """从最近一次 rollback 的备份中恢复 .agent/ 目录。"""
    inst_dir = instances_dir()
    inst_path = inst_dir / f"{args.instance}.json"

    if not inst_path.exists():
        print(json.dumps({"error": f"Instance not found: {args.instance}"}, ensure_ascii=False, indent=2))
        sys.exit(1)

    instance = json.loads(inst_path.read_text(encoding="utf-8"))
    meta = instance.get("metadata", {})
    backup_path_str = meta.get("last_rollback_backup") or args.backup_path

    if not backup_path_str:
        print(json.dumps({"error": "No backup path found. Provide --backup-path or run rollback first."}, ensure_ascii=False, indent=2))
        sys.exit(1)

    backup_dir = Path(backup_path_str)
    if not backup_dir.exists():
        backup_dir = Path.cwd() / backup_path_str
    if not backup_dir.exists():
        print(json.dumps({"error": f"Backup directory not found: {backup_path_str}"}, ensure_ascii=False, indent=2))
        sys.exit(1)

    agent_dir = Path.cwd() / ".agent"

    if agent_dir.exists():
        temp_old = agent_dir.with_suffix(".old")
        try:
            if temp_old.exists():
                shutil.rmtree(temp_old)
            shutil.move(str(agent_dir), str(temp_old))
        except Exception as e:
            print(json.dumps({"error": f"Failed to move existing .agent/: {e}"}, ensure_ascii=False, indent=2))
            sys.exit(1)

    try:
        shutil.copytree(backup_dir, agent_dir, dirs_exist_ok=True)
    except Exception as e:
        print(json.dumps({"error": f"Failed to restore .agent/ from backup: {e}"}, ensure_ascii=False, indent=2))
        sys.exit(1)

    print(json.dumps({
        "success": True,
        "instance_id": args.instance,
        "restored_from": str(backup_dir).replace("\\", "/"),
        "agent_path": str(agent_dir).replace("\\", "/"),
    }, ensure_ascii=False, indent=2))


def cmd_add_stage_instance(args):
    """动态添加一个 stage 实例（从已有 stage 定义克隆）。"""
    inst_dir = instances_dir()
    inst_path = inst_dir / f"{args.instance}.json"

    if not inst_path.exists():
        print(json.dumps({"error": f"Instance not found: {args.instance}"}, ensure_ascii=False, indent=2))
        sys.exit(1)

    instance = json.loads(inst_path.read_text(encoding="utf-8"))

    # 查找模板 stage（取第一个匹配的 stage_id 作为模板）
    template = None
    for s in instance["stages"]:
        if s["stage_id"] == args.stage:
            template = s
            break

    if not template:
        print(json.dumps({"error": f"No stage found with stage_id '{args.stage}' to use as template"}, ensure_ascii=False, indent=2))
        sys.exit(1)

    # 计算新实例编号
    count = count_stage_instances(instance, args.stage)
    new_siid = f"{args.stage}#{count + 1}"

    # 检查是否已存在
    for s in instance["stages"]:
        if get_stage_instance_id(s) == new_siid:
            print(json.dumps({"error": f"Stage instance already exists: {new_siid}"}, ensure_ascii=False, indent=2))
            sys.exit(1)

    # 解析 fan_out_target（可选）
    fan_out_target = None
    if args.fan_out_target:
        try:
            fan_out_target = json.loads(args.fan_out_target)
            if not isinstance(fan_out_target, dict):
                print(json.dumps({"error": "fan_out_target must be a JSON object"}, ensure_ascii=False, indent=2))
                sys.exit(1)
        except json.JSONDecodeError as e:
            print(json.dumps({"error": f"Invalid JSON for --fan-out-target: {e}"}, ensure_ascii=False, indent=2))
            sys.exit(1)

    # 深拷贝模板并重置运行时字段
    new_stage = copy.deepcopy(template)
    new_stage["stage_instance_id"] = new_siid
    new_stage["status"] = "PENDING"
    new_stage["output_message_id"] = None
    new_stage["history_message_ids"] = []
    new_stage["git_anchor_tag"] = None
    new_stage["loop_counter"] = 0
    new_stage["attempt_count"] = 0
    new_stage["blocked_by_confirm"] = False
    new_stage["assigned_agent_id"] = None
    new_stage["system_agent_id"] = None
    new_stage["start_time"] = None
    new_stage["end_time"] = None
    new_stage["resolved_model"] = None  # fan-out 新实例运行时重新解析
    # 存储 fan_out_target 信息，供编排器注入 [STAGE_DIRECTION]
    if fan_out_target:
        new_stage["fan_out_target"] = fan_out_target

    # 插入到模板所在位置之后（保持所有同 stage_id 实例相邻）
    insert_idx = 0
    for i, s in enumerate(instance["stages"]):
        if s["stage_id"] == args.stage:
            insert_idx = i + 1

    instance["stages"].insert(insert_idx, new_stage)
    instance["execution_summary"]["total_stages"] = len(instance["stages"])
    instance["updated_at"] = now_iso()

    atomic_write_json(inst_path, instance)

    print(json.dumps({
        "success": True,
        "instance_id": args.instance,
        "stage_id": args.stage,
        "stage_instance_id": new_siid,
        "template_siid": get_stage_instance_id(template),
        "fan_out_target_id": fan_out_target.get("id") if fan_out_target else None,
        "position": insert_idx,
        "total_instances_for_stage": count + 1,
    }, ensure_ascii=False, indent=2))


def cmd_list_stages(args):
    """列出实例中所有 stage 及其多实例信息。"""
    inst_dir = instances_dir()
    inst_path = inst_dir / f"{args.instance}.json"

    if not inst_path.exists():
        print(json.dumps({"error": f"Instance not found: {args.instance}"}, ensure_ascii=False, indent=2))
        sys.exit(1)

    instance = json.loads(inst_path.read_text(encoding="utf-8"))

    stages_out = []
    for s in instance["stages"]:
        stages_out.append({
            "stage_instance_id": get_stage_instance_id(s),
            "stage_id": s["stage_id"],
            "status": s["status"],
            "skill_id": s.get("skill_id", ""),
            "assigned_agent_id": s.get("assigned_agent_id"),
            "attempt_count": s.get("attempt_count", 0),
            "loop_counter": s.get("loop_counter", 0),
        })

    # 统计多实例 stage
    stage_counts = {}
    for s in stages_out:
        sid = s["stage_id"]
        stage_counts[sid] = stage_counts.get(sid, 0) + 1
    multi_instance_stages = {k: v for k, v in stage_counts.items() if v > 1}

    print(json.dumps({
        "instance_id": args.instance,
        "total_stages": len(stages_out),
        "multi_instance_stages": multi_instance_stages,
        "stages": stages_out,
    }, ensure_ascii=False, indent=2))


# ── Instance Set 管理（v2.1+）─────────────────────────────

def _sync_set_status(set_data: dict) -> dict:
    """读取 Set 内所有实例的最新状态，同步更新 Set 数据。返回更新后的 set_data。"""
    total = len(set_data["instances"])
    completed = 0
    running = 0
    failed = 0
    pending_confirm = 0
    cancelled = 0
    inst_dir = instances_dir()

    for entry in set_data["instances"]:
        inst_path = inst_dir / f"{entry['instance_id']}.json"
        if not inst_path.exists():
            continue
        try:
            instance = json.loads(inst_path.read_text(encoding="utf-8"))
        except Exception:
            continue

        inst_status = instance.get("status", "UNKNOWN")
        entry["status"] = inst_status

        if inst_status == "COMPLETED":
            completed += 1
        elif inst_status == "EXECUTING":
            running += 1
        elif inst_status == "FAILED":
            failed += 1
        elif inst_status == "SUSPENDED":
            if instance.get("pending_confirmations"):
                pending_confirm += 1
        elif inst_status == "CANCELLED":
            cancelled += 1

    summary = set_data.setdefault("execution_summary", {})
    summary["total"] = total
    summary["completed"] = completed
    summary["running"] = running
    summary["failed"] = failed
    summary["pending_confirm"] = pending_confirm
    summary["cancelled"] = cancelled

    policy = set_data.get("policy", {})
    completion = policy.get("completion", "all")

    active = running + pending_confirm
    terminal = completed + failed + cancelled

    if active > 0:
        if pending_confirm > 0:
            set_data["set_status"] = "SUSPENDED"
        else:
            set_data["set_status"] = "EXECUTING"
    elif terminal == total:
        if completion == "all" and completed == total:
            set_data["set_status"] = "COMPLETED"
        elif completion == "any" and completed >= 1:
            set_data["set_status"] = "COMPLETED"
        elif failed > 0:
            set_data["set_status"] = "FAILED"
        else:
            set_data["set_status"] = "COMPLETED"
    else:
        set_data["set_status"] = "PLANNING"

    set_data["updated_at"] = now_iso()
    return set_data


def cmd_set_create(args):
    ref_dir = find_workflow_ref_dir(args.workflow, args.version)
    if ref_dir is None:
        print(json.dumps({
            "error": f"Reference directory not found for {args.workflow}@{args.version}",
            "searched": [
                str(workflows_ref_dir() / f"{args.workflow}@{args.version}").replace("\\", "/"),
            ],
        }, ensure_ascii=False, indent=2))
        sys.exit(1)

    dir_version = ref_dir.name.split("@", 1)[1]

    try:
        param_list = json.loads(args.param_list)
        if not isinstance(param_list, list):
            raise ValueError("param_list must be a JSON array")
    except Exception as e:
        print(json.dumps({"error": f"Invalid param-list: {e}"}, ensure_ascii=False, indent=2))
        sys.exit(1)

    if not param_list:
        print(json.dumps({"error": "param-list is empty"}, ensure_ascii=False, indent=2))
        sys.exit(1)

    instance_entries = []
    for idx, params in enumerate(param_list):
        instance = _create_instance_core(args.workflow, dir_version, ref_dir, args.special_instructions or "")
        entry = {
            "instance_id": instance["instance_id"],
            "params": params if isinstance(params, dict) else {},
            "status": instance["status"],
            "index": idx,
        }
        instance_entries.append(entry)

    set_id = generate_set_id(args.workflow)
    set_data = {
        "schema_version": "2.0.0",
        "set_id": set_id,
        "workflow_ref": {
            "workflow_id": args.workflow,
            "version": dir_version,
        },
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "policy": {
            "completion": args.completion_policy or "all",
            "confirmation_mode": args.confirmation_mode or "batch",
        },
        "instances": instance_entries,
        "set_status": "PLANNING",
        "execution_summary": {
            "total": len(instance_entries),
            "completed": 0,
            "running": 0,
            "failed": 0,
            "pending_confirm": 0,
            "cancelled": 0,
        },
        "special_instructions": args.special_instructions or "",
    }

    set_path = sets_dir() / f"{set_id}.json"
    atomic_write_json(set_path, set_data)

    print(json.dumps({
        "success": True,
        "set_id": set_id,
        "instances": [{"instance_id": e["instance_id"], "params": e["params"]} for e in instance_entries],
        "workflow_id": args.workflow,
        "version": dir_version,
        "total_instances": len(instance_entries),
    }, ensure_ascii=False, indent=2))


def cmd_set_status(args):
    sdir = sets_dir()
    if args.set_id:
        set_path = sdir / f"{args.set_id}.json"
        if not set_path.exists():
            print(json.dumps({"error": f"Set not found: {args.set_id}"}, ensure_ascii=False, indent=2))
            sys.exit(1)
        set_data = json.loads(set_path.read_text(encoding="utf-8"))
        set_data = _sync_set_status(set_data)
        atomic_write_json(set_path, set_data)
        print(json.dumps({
            "success": True,
            "set_id": set_data["set_id"],
            "set_status": set_data["set_status"],
            "policy": set_data["policy"],
            "execution_summary": set_data["execution_summary"],
            "instances": set_data["instances"],
        }, ensure_ascii=False, indent=2))
    else:
        # 列出所有 Set
        if not sdir.exists():
            print(json.dumps({"sets": [], "total": 0}, ensure_ascii=False, indent=2))
            return

        sets_out = []
        for set_path in sorted(sdir.glob("*.json")):
            try:
                sd = json.loads(set_path.read_text(encoding="utf-8"))
                sd = _sync_set_status(sd)
                atomic_write_json(set_path, sd)
                sets_out.append({
                    "set_id": sd["set_id"],
                    "set_status": sd["set_status"],
                    "workflow_ref": sd.get("workflow_ref", {}),
                    "execution_summary": sd.get("execution_summary", {}),
                })
            except Exception:
                continue

        print(json.dumps({"sets": sets_out, "total": len(sets_out)}, ensure_ascii=False, indent=2))


def cmd_set_cancel(args):
    sdir = sets_dir()
    set_path = sdir / f"{args.set_id}.json"
    if not set_path.exists():
        print(json.dumps({"error": f"Set not found: {args.set_id}"}, ensure_ascii=False, indent=2))
        sys.exit(1)

    set_data = json.loads(set_path.read_text(encoding="utf-8"))
    inst_dir = instances_dir()
    now = now_iso()
    cancelled_count = 0

    for entry in set_data["instances"]:
        inst_path = inst_dir / f"{entry['instance_id']}.json"
        if not inst_path.exists():
            continue
        try:
            instance = json.loads(inst_path.read_text(encoding="utf-8"))
        except Exception:
            continue

        modified = False
        for stage in instance.get("stages", []):
            if stage["status"] in ("RUNNING", "PENDING", "BLOCKED"):
                stage["status"] = "CANCELLED"
                stage["end_time"] = now
                modified = True

        if modified:
            running = [s for s in instance["stages"] if s["status"] == "RUNNING"]
            pending = [s for s in instance["stages"] if s["status"] == "PENDING"]
            blocked = [s for s in instance["stages"] if s["status"] == "BLOCKED"]
            errors = [s for s in instance["stages"] if s["status"] == "ERROR"]
            terminal = [s for s in instance["stages"] if s["status"] in ("DONE", "SKIPPED", "CANCELLED")]

            if blocked:
                instance["status"] = "SUSPENDED"
            elif running:
                instance["status"] = "EXECUTING"
            elif errors:
                fatal = 0
                for s in errors:
                    rp = s.get("retry_policy", {"max_attempts": 1, "on": []})
                    if s.get("attempt_count", 0) >= rp.get("max_attempts", 1):
                        fatal += 1
                if fatal == len(errors) and not pending:
                    instance["status"] = "FAILED"
                else:
                    instance["status"] = "EXECUTING"
            elif len(terminal) == len(instance["stages"]):
                instance["status"] = "COMPLETED"
            else:
                instance["status"] = "PLANNING"

            instance["updated_at"] = now
            atomic_write_json(inst_path, instance)
            cancelled_count += 1
            entry["status"] = instance["status"]

    set_data["set_status"] = "CANCELLED"
    set_data["updated_at"] = now
    atomic_write_json(set_path, set_data)

    print(json.dumps({
        "success": True,
        "set_id": args.set_id,
        "cancelled_instances": cancelled_count,
        "set_status": "CANCELLED",
    }, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description="Instance Manager")
    sub = parser.add_subparsers(dest="command", required=True)

    # create
    p_create = sub.add_parser("create", help="Create a new workflow instance")
    p_create.add_argument("--workflow", required=True)
    p_create.add_argument("--version", required=True)
    p_create.add_argument("--special-instructions", default="")

    # update-stage
    p_update = sub.add_parser("update-stage", help="Update stage status with transition validation")
    p_update.add_argument("--instance", required=True)
    p_update.add_argument("--stage", default="", help="Stage ID (accepts ambiguous match if only one instance)")
    p_update.add_argument("--stage-instance-id", default="", help="Exact stage instance ID (preferred for multi-instance stages)")
    p_update.add_argument("--status", required=True)
    p_update.add_argument("--message-id", default="")
    p_update.add_argument("--agent-id", default="", help="Logical assigned_agent_id (generated by orchestrator)")
    p_update.add_argument("--system-agent-id", default="", help="System-returned real agent_id (backfilled after Agent creation)")
    p_update.add_argument("--resolved-model", default="", help="Resolved concrete model name for this stage (backfilled by orchestrator)")
    p_update.add_argument("--force", action="store_true", help="Bypass state transition validation")

    # log-deviation
    p_dev = sub.add_parser("log-deviation", help="Log a deviation")
    p_dev.add_argument("--instance", required=True)
    p_dev.add_argument("--type", required=True, help=f"Deviation type. One of: {', '.join(sorted(DEVIATION_TYPES))}")
    p_dev.add_argument("--reason", required=True)
    p_dev.add_argument("--user-confirmed", action="store_true", default=False)
    p_dev.add_argument("--original-stage", default="")
    p_dev.add_argument("--impact-stages", default="")
    p_dev.add_argument("--resolution", default="")

    # rollback
    p_roll = sub.add_parser("rollback", help="Rollback to a previous stage")
    p_roll.add_argument("--instance", required=True)
    p_roll.add_argument("--target-stage", default="", help="Stage ID to rollback to")
    p_roll.add_argument("--target-stage-instance-id", default="", help="Exact stage instance ID to rollback to")

    # skip-stage
    p_skip = sub.add_parser("skip-stage", help="Mark a stage as SKIPPED with preflight metadata")
    p_skip.add_argument("--instance", required=True)
    p_skip.add_argument("--stage", default="", help="Stage ID (accepts ambiguous match if only one instance)")
    p_skip.add_argument("--stage-instance-id", default="", help="Exact stage instance ID (preferred for multi-instance stages)")
    p_skip.add_argument("--reason", default="PREFLIGHT_DETECTION", help="Why this stage is skipped")
    p_skip.add_argument("--evidence", default="", help="Evidence that the stage was already completed")
    p_skip.add_argument("--user-confirmed", action="store_true", default=False, help="Whether user explicitly confirmed skipping")
    p_skip.add_argument("--force", action="store_true", help="Bypass status check")

    # restore-agent
    p_restore = sub.add_parser("restore-agent", help="Restore .agent/ from rollback backup")
    p_restore.add_argument("--instance", required=True)
    p_restore.add_argument("--backup-path", default="", help="Override backup path (default: use instance metadata)")

    # add-stage-instance (新增)
    p_add = sub.add_parser("add-stage-instance", help="Dynamically add a stage instance from an existing stage template")
    p_add.add_argument("--instance", required=True)
    p_add.add_argument("--stage", required=True, help="Stage ID to clone from (template)")
    p_add.add_argument("--fan-out-target", default="", help="JSON object with id/label/context for this fan-out instance")

    # list-stages (新增)
    p_list = sub.add_parser("list-stages", help="List all stage instances in an instance")
    p_list.add_argument("--instance", required=True)

    # set-create (Instance Set 批量创建)
    p_set_create = sub.add_parser("set-create", help="Create a batch of workflow instances (Instance Set)")
    p_set_create.add_argument("--workflow", required=True)
    p_set_create.add_argument("--version", required=True)
    p_set_create.add_argument("--param-list", required=True, help='JSON array of params, e.g. \'[{"target":"A"},{"target":"B"}]\'')
    p_set_create.add_argument("--special-instructions", default="")
    p_set_create.add_argument("--completion-policy", default="all", choices=["all", "any"], help="Set completion policy")
    p_set_create.add_argument("--confirmation-mode", default="batch", choices=["batch", "stream", "individual"], help="How to aggregate confirmations across instances")

    # set-status / set-list
    p_set_status = sub.add_parser("set-status", help="Query status of an Instance Set (omit --set-id to list all sets)")
    p_set_status.add_argument("--set-id", default="", help="Set ID to query")

    # set-cancel
    p_set_cancel = sub.add_parser("set-cancel", help="Cancel all instances in a Set")
    p_set_cancel.add_argument("--set-id", required=True)

    args = parser.parse_args()

    if args.command == "create":
        cmd_create(args)
    elif args.command == "update-stage":
        cmd_update_stage(args)
    elif args.command == "skip-stage":
        cmd_skip_stage(args)
    elif args.command == "log-deviation":
        cmd_log_deviation(args)
    elif args.command == "rollback":
        cmd_rollback(args)
    elif args.command == "restore-agent":
        cmd_restore_agent(args)
    elif args.command == "add-stage-instance":
        cmd_add_stage_instance(args)
    elif args.command == "list-stages":
        cmd_list_stages(args)
    elif args.command == "set-create":
        cmd_set_create(args)
    elif args.command == "set-status":
        cmd_set_status(args)
    elif args.command == "set-cancel":
        cmd_set_cancel(args)


if __name__ == "__main__":
    main()
