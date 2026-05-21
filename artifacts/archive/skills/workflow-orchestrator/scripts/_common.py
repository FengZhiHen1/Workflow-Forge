#!/usr/bin/env python3
"""
公共工具模块

供 workflow-orchestrator 的 scripts/ 目录下各脚本共享的底层 IO 工具函数。
不包含业务逻辑，只提供路径查找、文件读写、时间戳等基础设施。
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


def find_project_root() -> Path:
    """
    查找项目根目录（包含 .agent/ 和 .claude/ 的目录）。
    支持从当前工作目录或其父目录向上搜索。
    """
    cwd = Path.cwd()
    candidate_agent = cwd / ".agent"
    candidate_claude = cwd / ".claude"
    if candidate_agent.exists() and candidate_claude.exists():
        return cwd
    for parent in [cwd.parent, cwd.parent.parent]:
        a = parent / ".agent"
        c = parent / ".claude"
        if a.exists() and c.exists():
            return parent
    return cwd


def messages_dir() -> Path:
    return find_project_root() / ".agent" / "messages"


def instances_dir() -> Path:
    return find_project_root() / ".agent" / "workflows" / "instances"


def sets_dir() -> Path:
    return find_project_root() / ".agent" / "workflows" / "sets"


def backups_dir() -> Path:
    return find_project_root() / ".agent" / "backups"


def workflows_ref_dir() -> Path:
    return find_project_root() / ".claude" / "workflows"


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def atomic_write_json(filepath: Path, data: dict):
    """原子写入 JSON。先写 .tmp 再 os.replace，避免半写文件。"""
    filepath.parent.mkdir(parents=True, exist_ok=True)
    tmp = filepath.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(str(tmp), str(filepath))


def find_message_path(message_id: str, msgs_dir: Path) -> Path | None:
    """按日期分区查找 message 文件。返回 Path 或 None。"""
    if not msgs_dir.exists():
        return None
    date_prefix = message_id[:4] + "-" + message_id[4:6] + "-" + message_id[6:8]
    date_dir = msgs_dir / date_prefix
    if date_dir.exists():
        path = date_dir / f"{message_id}.json"
        if path.exists():
            return path
    for subdir in msgs_dir.iterdir():
        if subdir.is_dir():
            path = subdir / f"{message_id}.json"
            if path.exists():
                return path
    return None


def load_message(message_id: str, msgs_dir: Path) -> dict | None:
    """加载指定 message_id 的 JSON 内容。失败返回 None。"""
    path = find_message_path(message_id, msgs_dir)
    if not path:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
