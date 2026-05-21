"""跨平台 ISO 8601 时间戳。"""

from datetime import datetime


def iso_timestamp() -> str:
    """返回带时区偏移的 ISO 8601 时间戳，跨平台兼容。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def parse_iso_timestamp(s: str) -> float:
    """解析 ISO 8601 时间戳为 Unix 时间戳，跨平台兼容。"""
    return datetime.fromisoformat(s).timestamp()
