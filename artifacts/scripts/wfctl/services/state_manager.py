"""instance.json 读写 + timeline/deviations 日志。"""

import json

from infrastructure.io import atomic_write_json
from infrastructure.errors import InputError, StateError
from infrastructure.timestamp import iso_timestamp
from infrastructure.lock import FileLock
from infrastructure.project import find_root


def load_instance(instance_id: str) -> dict:
    """[DEPRECATED] 读取 instance.json。使用 state.persistence.load_instance_state() 替代。"""
    from state.persistence import load_instance_state
    state = load_instance_state(instance_id)
    return state.to_dict()


def save_instance(instance_id: str, data: dict) -> None:
    """[DEPRECATED] 原子写入 instance.json。使用 state.persistence.save_instance_state() 替代。"""
    from state.persistence import save_instance_state
    from state.model import InstanceState
    state = InstanceState.from_dict(data)
    save_instance_state(instance_id, state)


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
