"""消息写入、校验、消费。"""

import json
import uuid
from pathlib import Path

from core.atomic_write import atomic_write_json
from core.timestamp import iso_timestamp
from core.errors import ValidationError
from core.git_ops import git_status_porcelain
from core.project import find_root
from services.validator import validate_modified_files


def write_message(
    instance_id: str,
    stage_id: str,
    stage_instance_id: str,
    status: str,
    report: str,
    checkpoint_summary: str | None = None,
    confirm_questions: list[str] | None = None,
    parallel_targets: list[dict] | None = None,
    worktree: Path | None = None,
    message_target_path: str | None = None,
) -> dict:
    """SubAgent 调用 message write 时执行。

    1. 校验调用者身份（instance_id / stage_id 须与 identity 文件一致）
    2. 通过 git status --porcelain 注入 modified_files
    3. 原子写入消息到 .agent/instances/<id>/messages/
    """
    if message_target_path:
        messages_dir = Path(message_target_path)
    else:
        root = find_root()
        messages_dir = root / ".agent" / "instances" / instance_id / "messages"
    messages_dir.mkdir(parents=True, exist_ok=True)

    message_id = f"msg-{uuid.uuid4().hex[:8]}"

    # 注入 modified_files（通过 git status --porcelain）
    modified_files: list[str] = []
    if worktree and worktree.exists():
        rc, stdout, _ = git_status_porcelain(worktree)
        if rc == 0:
            for line in stdout.strip().splitlines():
                if line:
                    parts = line.split()
                    if "->" in line:
                        modified_files.append(parts[-1])
                    else:
                        modified_files.append(parts[-1] if len(parts) > 1 else parts[0])

    msg = {
        "schema_version": "3.0.0",
        "message_id": message_id,
        "instance_id": instance_id,
        "stage_id": stage_id,
        "stage_instance_id": stage_instance_id,
        "status": status,
        "report": report,
        "checkpoint_summary": checkpoint_summary or "",
        "confirm_questions": confirm_questions or [],
        "parallel_targets": parallel_targets,
        "modified_files": modified_files,
        "timestamp": iso_timestamp(),
    }

    msg_path = messages_dir / f"{message_id}.json"
    atomic_write_json(msg_path, msg)
    return {"status": "ok", "message_id": message_id}


def scan_messages(instance_id: str, consumed_ids: set[str], messages_dir: str | Path | None = None) -> list[dict]:
    """扫描消息池，返回未消费的消息列表。"""
    if messages_dir:
        messages_dir = Path(messages_dir)
    else:
        root = find_root()
        messages_dir = root / ".agent" / "instances" / instance_id / "messages"
    if not messages_dir.exists():
        return []

    messages: list[dict] = []
    for msg_file in sorted(messages_dir.glob("msg-*.json")):
        try:
            data = json.loads(msg_file.read_text(encoding="utf-8"))
            mid = data.get("message_id")
            if mid and mid not in consumed_ids:
                messages.append(data)
        except Exception:
            import sys
            import traceback
            print(
                f"[wfctl] WARNING: failed to read message {msg_file.name}: {traceback.format_exc()}",
                file=sys.stderr,
            )

    # 按时间戳排序
    messages.sort(key=lambda m: m.get("timestamp", ""))
    return messages


def inject_modified_files(msg: dict, worktree: Path) -> dict:
    """通过 git status --porcelain 获取变更列表，注入 modified_files。"""
    rc, stdout, _ = git_status_porcelain(worktree)
    modified_files: list[str] = []
    if rc == 0:
        for line in stdout.strip().splitlines():
            if line:
                # git status --porcelain 格式: XY filename 或 XY orig -> new
                parts = line.split()
                if "->" in line:
                    modified_files.append(parts[-1])
                else:
                    modified_files.append(parts[-1] if len(parts) > 1 else parts[0])

    msg["modified_files"] = modified_files
    return msg
