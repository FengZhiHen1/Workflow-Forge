"""测试 state_manager 服务。"""

import json
from pathlib import Path

import pytest

from infrastructure.errors import InputError, StateError
from services.state_manager import (
    append_deviation,
    load_instance,
    save_instance,
)


def test_load_instance_v3(monkeypatch, tmp_path: Path):
    repo = tmp_path / "project"
    repo.mkdir()
    (repo / ".agent").mkdir()
    inst_dir = repo / ".agent" / "instances" / "20260517-001"
    inst_dir.mkdir(parents=True)
    data = {"instance_id": "20260517-001", "status": "ACTIVE", "stages": []}
    (inst_dir / "instance.json").write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.chdir(repo)
    result = load_instance("20260517-001")
    assert result["instance_id"] == "20260517-001"


def test_load_instance_v2_fallback(monkeypatch, tmp_path: Path):
    repo = tmp_path / "project"
    repo.mkdir()
    (repo / ".agent" / "workflows" / "instances").mkdir(parents=True)
    data = {"instance_id": "20260517-001", "status": "ACTIVE", "stages": []}
    (repo / ".agent" / "workflows" / "instances" / "20260517-001.json").write_text(
        json.dumps(data), encoding="utf-8"
    )
    monkeypatch.chdir(repo)
    result = load_instance("20260517-001")
    assert result["schema_version"] == "3.0.0"


def test_load_instance_not_found(monkeypatch, tmp_path: Path):
    repo = tmp_path / "project"
    repo.mkdir()
    (repo / ".agent").mkdir()
    monkeypatch.chdir(repo)
    with pytest.raises(InputError):
        load_instance("nonexistent")


def test_load_instance_corrupted(monkeypatch, tmp_path: Path):
    repo = tmp_path / "project"
    repo.mkdir()
    (repo / ".agent" / "instances" / "bad").mkdir(parents=True)
    (repo / ".agent" / "instances" / "bad" / "instance.json").write_text("not json", encoding="utf-8")
    monkeypatch.chdir(repo)
    with pytest.raises(StateError) as exc_info:
        load_instance("bad")
    assert exc_info.value.code == "STATE_CORRUPTED"


def test_save_instance(monkeypatch, tmp_path: Path):
    repo = tmp_path / "project"
    repo.mkdir()
    (repo / ".agent").mkdir()
    monkeypatch.chdir(repo)
    data = {"instance_id": "20260517-001", "status": "ACTIVE"}
    save_instance("20260517-001", data)
    path = repo / ".agent" / "instances" / "20260517-001" / "instance.json"
    assert path.exists()
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["instance_id"] == "20260517-001"
    assert saved["status"] == "ACTIVE"


def test_append_deviation(monkeypatch, tmp_path: Path):
    repo = tmp_path / "project"
    repo.mkdir()
    (repo / ".agent").mkdir()
    monkeypatch.chdir(repo)
    append_deviation("inst-001", "TEST", "reason", stage_id="s01", files=["a.py"])
    dev_path = repo / ".agent" / "instances" / "inst-001" / "logs" / "deviation.jsonl"
    assert dev_path.exists()
    lines = dev_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["type"] == "TEST"
    assert entry["stage_id"] == "s01"
