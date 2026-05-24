"""instance.json 读写 + 消息消费。"""

import json
from pathlib import Path

from core.atomic_write import atomic_write_json
from core.errors import InputError, StateError
from core.timestamp import iso_timestamp
from core.lock import FileLock
from core.project import find_root
from services.message_handler import scan_messages
from services.validator import validate_modified_files


def load_instance(instance_id: str) -> dict:
    """读取 instance.json。"""
    root = find_root()
    path = root / ".agent" / "instances" / instance_id / "instance.json"
    if not path.exists():
        # 兼容 v2 平铺式
        v2_path = root / ".agent" / "workflows" / "instances" / f"{instance_id}.json"
        if v2_path.exists():
            data = json.loads(v2_path.read_text(encoding="utf-8"))
            # 迁移到 v3 格式
            data["schema_version"] = "3.0.0"
            return data
        raise InputError(f"Instance not found: {instance_id}", code="INVALID_ARGUMENT")

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise StateError(f"Corrupted instance.json: {e}", code="STATE_CORRUPTED")


def save_instance(instance_id: str, data: dict) -> None:
    """原子写入 instance.json。"""
    root = find_root()
    path = root / ".agent" / "instances" / instance_id / "instance.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, data)


def consume_messages(instance_id: str, instance: dict, worktree_map: dict[str, Path]) -> list[dict]:
    """消费消息池，更新 stage 状态，返回状态变更摘要。"""
    consumed_ids = set(instance.get("consumed_message_ids", []))
    messages = scan_messages(instance_id, consumed_ids)
    changes: list[dict] = []

    stage_map = {s["stage_id"]: s for s in instance.get("stages", [])}

    for msg in messages:
        stage_id = msg.get("stage_id")
        stage = stage_map.get(stage_id)
        if not stage:
            continue

        # 校验 modified_files（已在 message write 时注入）
        try:
            wt = worktree_map.get(stage_id) if worktree_map else None
            if msg.get("modified_files") and wt:
                validate_modified_files(wt, msg["modified_files"], stage_id)
        except Exception as e:
            import traceback
            stage["status"] = "ERROR"
            _append_timeline(instance_id, stage_id, "running→error", {
                "reason": str(e),
                "message_id": msg["message_id"],
                "intended_status": msg.get("status"),
                "modified_files": msg.get("modified_files", []),
                "traceback": traceback.format_exc(),
            })
            consumed_ids.add(msg["message_id"])
            changes.append({
                "stage_id": stage_id,
                "old_status": "RUNNING",
                "new_status": "ERROR",
                "message": msg,
            })
            continue

        old_status = stage.get("status", "PENDING")
        new_status = msg.get("status", old_status)

        # 状态无变化 → 只消费消息，不覆盖已有字段（防止后续消息覆盖
        # confirm/skip 写入的 exit_condition、confirm_questions 等）
        if old_status == new_status:
            consumed_ids.add(msg["message_id"])
            continue

        if new_status == "DONE":
            stage["status"] = "DONE"
            stage["exit_condition"] = "success"
            stage["output_message_id"] = msg["message_id"]
            _append_timeline(instance_id, stage_id, "running→done", {"message_id": msg["message_id"]})
        elif new_status == "ERROR":
            stage["status"] = "ERROR"
            _append_timeline(instance_id, stage_id, "running→error", {"message_id": msg["message_id"], "reason": msg.get("report", "")})
        elif new_status == "AWAITING_CONFIRM":
            stage["status"] = "AWAITING_CONFIRM"
            stage["output_message_id"] = msg["message_id"]
            stage["confirm_questions"] = msg.get("confirm_questions", [])
            _append_timeline(instance_id, stage_id, "running→awaiting_confirm", {"message_id": msg["message_id"]})
        elif new_status == "RUNNING":
            stage["status"] = "RUNNING"
            _append_timeline(instance_id, stage_id, "scheduled", {"message_id": msg["message_id"]})

        consumed_ids.add(msg["message_id"])
        changes.append({
            "stage_id": stage_id,
            "old_status": old_status,
            "new_status": stage["status"],
            "message": msg,
        })

    instance["consumed_message_ids"] = list(consumed_ids)
    return changes


def _append_timeline(instance_id: str, stage_id: str, event: str, extra: dict | None = None) -> None:
    """追加 timeline 日志。"""
    root = find_root()
    logs_dir = root / ".agent" / "instances" / instance_id / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    timeline_path = logs_dir / "timeline.jsonl"

    entry = {
        "stage_id": stage_id,
        "event": event,
        "timestamp": iso_timestamp(),
    }
    if extra:
        entry.update(extra)

    with open(timeline_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def append_deviation(instance_id: str, dev_type: str, reason: str, stage_id: str | None = None, files: list[str] | None = None) -> None:
    """追加 deviation 日志。"""
    root = find_root()
    logs_dir = root / ".agent" / "instances" / instance_id / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    dev_path = logs_dir / "deviation.jsonl"

    entry = {
        "timestamp": iso_timestamp(),
        "type": dev_type,
        "reason": reason,
    }
    if stage_id:
        entry["stage_id"] = stage_id
    if files:
        entry["files"] = files

    with open(dev_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
