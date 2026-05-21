#!/usr/bin/env python3
"""
Message 写入脚本

职责：
1. 读取 SubAgent 生成的草稿 JSON
2. 注入 schema_version, message_id, timestamp, tmp_dir
3. Schema 校验
4. 原子写入到 .agent/messages/YYYY-MM-DD/<message_id>.json
5. stdout 返回最终文件路径

调用方式：
    python .agent/scripts/write_message.py \
        --input <草稿_JSON路径> \
        --workflow <workflow_instance_id> \
        --agent-id <agent_id> \
        --skill-id <skill_id>
"""

import argparse
import json
import os
import random
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
MESSAGES_DIR = PROJECT_ROOT / ".agent" / "messages"
SCHEMA_VERSION = "1.1.0"

# ---------------------------------------------------------------------------
# ID 生成
# ---------------------------------------------------------------------------

def generate_message_id() -> str:
    """格式：YYYYMMDD-序号-4位随机后缀"""
    today_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    date_dir = MESSAGES_DIR / today_str
    date_dir.mkdir(parents=True, exist_ok=True)

    existing = [f.stem for f in date_dir.glob("*.json")]
    max_seq = 0
    for name in existing:
        parts = name.split("-")
        if len(parts) >= 2 and parts[0] == today_str:
            try:
                max_seq = max(max_seq, int(parts[1]))
            except ValueError:
                pass

    seq = max_seq + 1
    suffix = "".join(random.choices("0123456789abcdef", k=4))
    return f"{today_str}-{seq:03d}-{suffix}"


# ---------------------------------------------------------------------------
# 路径校验
# ---------------------------------------------------------------------------

def validate_path(path: str, field_name: str) -> list[str]:
    """校验路径规范：相对路径、无冗余、统一 / 分隔符"""
    errors = []
    if path.startswith("/"):
        errors.append(f"{field_name}: 必须以相对路径开头，不能以 '/' 开头: {path}")
    if "//" in path or "./" in path or "../" in path:
        errors.append(f"{field_name}: 禁止包含冗余片段（./, ../, //）: {path}")
    if "\\" in path:
        errors.append(f"{field_name}: 必须使用 '/' 作为分隔符: {path}")
    return errors


def validate_path_list(paths: list, field_name: str, allow_dir_ending: bool = False) -> list[str]:
    errors = []
    if not isinstance(paths, list):
        errors.append(f"{field_name}: 必须是数组")
        return errors
    for i, p in enumerate(paths):
        if not isinstance(p, str):
            errors.append(f"{field_name}[{i}]: 必须是字符串")
            continue
        errors.extend(validate_path(p, f"{field_name}[{i}]"))
        if not allow_dir_ending and p.endswith("/"):
            errors.append(f"{field_name}[{i}]: 文件路径不能以 '/' 结尾: {p}")
    return errors


# ---------------------------------------------------------------------------
# Schema 校验
# ---------------------------------------------------------------------------

VALID_STATUSES = {"RUNNING", "PENDING_CONFIRM", "AWAITING_USER", "CONFIRMED", "DONE", "ERROR", "CANCELLED"}


def validate_draft(draft: dict, workflow_id: str, agent_id: str, skill_id: str) -> list[str]:
    errors = []

    # status
    status = draft.get("status")
    if not isinstance(status, str) or status not in VALID_STATUSES:
        errors.append(f"status: 必须是封闭枚举之一 {VALID_STATUSES}，当前: {status}")

    # skill_id / workflow_id / agent_id 一致性
    if draft.get("workflow_instance_id") != workflow_id:
        errors.append(f"workflow_instance_id: 必须与 --workflow 一致 ({workflow_id})")
    if draft.get("agent_id") != agent_id:
        errors.append(f"agent_id: 必须与 --agent-id 一致 ({agent_id})")
    if draft.get("skill_id") != skill_id:
        errors.append(f"skill_id: 必须与 --skill-id 一致 ({skill_id})")

    # upstream_files
    errors.extend(validate_path_list(draft.get("upstream_files", []), "upstream_files"))

    # modified_files
    errors.extend(validate_path_list(draft.get("modified_files", []), "modified_files"))

    # draft_files
    errors.extend(validate_path_list(draft.get("draft_files", []), "draft_files"))

    # output_files
    errors.extend(validate_path_list(draft.get("output_files", []), "output_files"))

    # report
    report = draft.get("report")
    if not isinstance(report, str) or not report:
        errors.append("report: 必须是非空字符串")
    elif isinstance(report, str) and report.startswith("#"):
        errors.append("report: 禁止以 Markdown 标题语法 '#' 开头")

    # confirm_required / confirm_questions
    confirm_required = draft.get("confirm_required")
    if not isinstance(confirm_required, bool):
        errors.append("confirm_required: 必须是布尔值")
    confirm_questions = draft.get("confirm_questions", [])
    if confirm_required:
        if not isinstance(confirm_questions, list) or not (1 <= len(confirm_questions) <= 4):
            errors.append(f"confirm_questions: confirm_required=true 时必须存在且长度 ∈ [1, 4]，当前: {len(confirm_questions) if isinstance(confirm_questions, list) else 'N/A'}")
        else:
            for i, q in enumerate(confirm_questions):
                if not isinstance(q, str) or not q:
                    errors.append(f"confirm_questions[{i}]: 必须是非空字符串")
    else:
        if not isinstance(confirm_questions, list) or confirm_questions:
            errors.append("confirm_questions: confirm_required=false 时必须为空数组 []")

    # checkpoint_summary
    checkpoint = draft.get("checkpoint_summary")
    if not isinstance(checkpoint, str) or not checkpoint:
        errors.append("checkpoint_summary: 必须是非空字符串")

    # metadata (可选)
    metadata = draft.get("metadata")
    if metadata is not None and not isinstance(metadata, dict):
        errors.append("metadata: 若存在则必须是对象")

    return errors


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Message 原子写入脚本")
    parser.add_argument("--input", required=True, help="SubAgent 生成的草稿 JSON 路径")
    parser.add_argument("--workflow", required=True, help="workflow_instance_id")
    parser.add_argument("--agent-id", required=True, help="agent_id")
    parser.add_argument("--skill-id", required=True, help="skill_id")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        _err({"error": "INPUT_NOT_FOUND", "reason": f"输入文件不存在: {args.input}"})
        sys.exit(1)

    try:
        with open(input_path, "r", encoding="utf-8") as f:
            draft = json.load(f)
    except json.JSONDecodeError as e:
        _err({"error": "JSON_DECODE_FAILED", "reason": str(e), "input_path": str(input_path)})
        sys.exit(1)
    except Exception as e:
        _err({"error": "READ_FAILED", "reason": str(e), "input_path": str(input_path)})
        sys.exit(1)

    # Schema 校验
    errors = validate_draft(draft, args.workflow, args.agent_id, args.skill_id)
    if errors:
        _err({
            "error": "VALIDATION_FAILED",
            "reason": "; ".join(errors),
            "input_path": str(input_path)
        })
        sys.exit(1)

    # 注入字段
    message_id = generate_message_id()
    today_str = message_id.split("-")[0]
    timestamp = datetime.now().astimezone().isoformat()
    tmp_dir = f".tmp/{args.workflow}/{message_id}/"

    final = {
        "schema_version": SCHEMA_VERSION,
        "message_id": message_id,
        "workflow_instance_id": draft.get("workflow_instance_id"),
        "agent_id": draft.get("agent_id"),
        "skill_id": draft.get("skill_id"),
        "status": draft.get("status"),
        "timestamp": timestamp,
        "upstream_files": draft.get("upstream_files", []),
        "modified_files": draft.get("modified_files", []),
        "draft_files": draft.get("draft_files", []),
        "output_files": draft.get("output_files", []),
        "report": draft.get("report"),
        "confirm_required": draft.get("confirm_required"),
        "confirm_questions": draft.get("confirm_questions", []),
        "checkpoint_summary": draft.get("checkpoint_summary"),
        "tmp_dir": tmp_dir,
    }
    if "metadata" in draft:
        final["metadata"] = draft["metadata"]

    # fan_out_targets（可选）：上游 SubAgent 上报的拆分目标列表，供编排器 fan-out
    ft = draft.get("fan_out_targets")
    if ft is not None:
        if not isinstance(ft, list):
            _err({"error": "VALIDATION_FAILED", "reason": "fan_out_targets: 若存在则必须是数组"})
            sys.exit(1)
        validated = []
        for i, t in enumerate(ft):
            if not isinstance(t, dict):
                _err({"error": "VALIDATION_FAILED", "reason": f"fan_out_targets[{i}]: 必须是对象"})
                sys.exit(1)
            if not t.get("id") or not isinstance(t.get("id"), str):
                _err({"error": "VALIDATION_FAILED", "reason": f"fan_out_targets[{i}].id: 必须是非空字符串"})
                sys.exit(1)
            validated.append({
                "id": t["id"],
                "label": t.get("label", t["id"]),
                "context": t.get("context", ""),
            })
        final["fan_out_targets"] = validated

    # 原子写入
    date_dir = MESSAGES_DIR / today_str
    date_dir.mkdir(parents=True, exist_ok=True)
    final_path = date_dir / f"{message_id}.json"
    tmp_path = date_dir / f"{message_id}.json.tmp"

    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(final, f, ensure_ascii=False, indent=2)
        os.replace(str(tmp_path), str(final_path))
    except Exception as e:
        _err({"error": "WRITE_FAILED", "reason": str(e), "target": str(final_path)})
        sys.exit(1)

    # stdout 输出最终路径
    # 使用相对于项目根目录的路径
    rel_path = final_path.relative_to(PROJECT_ROOT).as_posix()
    print(rel_path)
    sys.exit(0)


def _err(obj: dict):
    print(json.dumps(obj, ensure_ascii=False, indent=2), file=sys.stderr)


if __name__ == "__main__":
    main()
