"""Timeline 日志写入。

纯副作用函数：追加 JSONL 日志到实例的 timeline 文件。
"""

from __future__ import annotations

import json

from infrastructure.project import find_root
from infrastructure.timestamp import iso_timestamp


def append_timeline(instance_id: str, stage_id: str, event: str, extra: dict | None = None) -> None:
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
