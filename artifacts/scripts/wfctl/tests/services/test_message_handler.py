"""测试 message_handler 服务。"""

import json
from pathlib import Path

import pytest

from services.message_handler import inject_modified_files, scan_messages, write_message


def test_write_message(monkeypatch, tmp_path: Path):
    repo = tmp_path / "project"
    repo.mkdir()
    (repo / ".agent" / "instances" / "inst-001" / "messages").mkdir(parents=True)
    monkeypatch.chdir(repo)
    result = write_message(
        instance_id="inst-001",
        stage_id="s01",
        stage_instance_id="s01",
        status="DONE",
        report="completed",
    )
    assert result["status"] == "ok"
    assert result["message_id"].startswith("msg-")

    msg_path = repo / ".agent" / "instances" / "inst-001" / "messages" / f"{result['message_id']}.json"
    assert msg_path.exists()
    data = json.loads(msg_path.read_text(encoding="utf-8"))
    assert data["stage_id"] == "s01"
    assert data["status"] == "DONE"


def test_scan_messages_skips_consumed(monkeypatch, tmp_path: Path):
    repo = tmp_path / "project"
    repo.mkdir()
    (repo / ".agent" / "instances" / "inst-001" / "messages").mkdir(parents=True)
    for mid in ["msg-a", "msg-b", "msg-c"]:
        msg = {
            "message_id": mid,
            "instance_id": "inst-001",
            "stage_id": "s01",
            "status": "DONE",
            "timestamp": f"2026-05-17T10:00:0{mid[-1]}+0800",
        }
        (repo / ".agent" / "instances" / "inst-001" / "messages" / f"{mid}.json").write_text(
            json.dumps(msg), encoding="utf-8"
        )
    monkeypatch.chdir(repo)

    messages = scan_messages("inst-001", {"msg-a"})
    mids = [m["message_id"] for m in messages]
    assert "msg-a" not in mids
    assert "msg-b" in mids
    assert "msg-c" in mids


def test_scan_messages_empty(monkeypatch, tmp_path: Path):
    repo = tmp_path / "project"
    repo.mkdir()
    (repo / ".agent" / "instances" / "inst-001" / "messages").mkdir(parents=True)
    monkeypatch.chdir(repo)
    messages = scan_messages("inst-001", set())
    assert messages == []


def test_inject_modified_files(tmp_path: Path):
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=str(repo), check=True, capture_output=True)
    (repo / "file.txt").write_text("hello", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(repo), check=True, capture_output=True)

    msg = {"message_id": "msg-x"}
    result = inject_modified_files(msg, repo)
    assert "modified_files" in result
    # file.txt 已 staged，porcelain 会显示
    assert "file.txt" in result["modified_files"]
