"""测试 state.persistence — 数据兼容层。"""

import json
import os
from pathlib import Path

import pytest

from core.errors import InputError, StateError
from services.scheduler.state_model import InstanceState, StageState, CycleMeta
from state.persistence import (
    DataVersion,
    InstanceDataAdapter,
    load_instance_state,
    save_instance_state,
)


class TestDataVersion:
    def test_enum_values(self):
        assert DataVersion.V2.value == "2.0.0"
        assert DataVersion.V3.value == "3.0.0"


class TestInstanceDataAdapter:
    def test_from_file_v3(self, tmp_path: Path):
        path = tmp_path / "instance.json"
        path.write_text(json.dumps({
            "schema_version": "3.0.0",
            "instance_id": "test",
            "status": "ACTIVE",
            "stages": [],
        }), encoding="utf-8")
        adapter = InstanceDataAdapter.from_file(path)
        assert adapter.declared_version == DataVersion.V3

    def test_from_file_v2_no_schema_version(self, tmp_path: Path):
        path = tmp_path / "instance.json"
        path.write_text(json.dumps({
            "instance_id": "test",
            "status": "active",
            "stages": [],
        }), encoding="utf-8")
        adapter = InstanceDataAdapter.from_file(path)
        assert adapter.declared_version == DataVersion.V2

    def test_from_file_v2_old_schema_version(self, tmp_path: Path):
        path = tmp_path / "instance.json"
        path.write_text(json.dumps({
            "schema_version": "2.0.0",
            "instance_id": "test",
            "stages": [],
        }), encoding="utf-8")
        adapter = InstanceDataAdapter.from_file(path)
        assert adapter.declared_version == DataVersion.V2

    def test_from_file_corrupt_json(self, tmp_path: Path):
        path = tmp_path / "instance.json"
        path.write_text("{not valid json", encoding="utf-8")
        with pytest.raises(StateError, match="Corrupted instance file"):
            InstanceDataAdapter.from_file(path)

    def test_to_standard_v3_passthrough(self):
        adapter = InstanceDataAdapter(
            raw={"schema_version": "3.0.0", "instance_id": "test"},
            declared_version=DataVersion.V3,
        )
        result = adapter.to_standard()
        assert result["schema_version"] == "3.0.0"
        assert result["instance_id"] == "test"

    def test_migrate_v2_to_v3_adds_fields(self):
        adapter = InstanceDataAdapter(
            raw={"instance_id": "test", "stages": [{"stage_id": "s01"}]},
            declared_version=DataVersion.V2,
        )
        result = adapter.to_standard()
        assert result["schema_version"] == "3.0.0"
        assert result["parent_instance_id"] is None
        assert result["merge_confirmed"] is False
        assert result["consumed_message_ids"] == []
        assert result["stages"][0]["stage_instance_id"] == "s01"

    def test_migrate_v2_preserves_existing_fields(self):
        adapter = InstanceDataAdapter(
            raw={
                "instance_id": "test",
                "parent_instance_id": "parent-123",
                "merge_confirmed": True,
                "stages": [{"stage_id": "s01", "stage_instance_id": "s01_0"}],
            },
            declared_version=DataVersion.V2,
        )
        result = adapter.to_standard()
        assert result["parent_instance_id"] == "parent-123"
        assert result["merge_confirmed"] is True
        assert result["stages"][0]["stage_instance_id"] == "s01_0"


class TestLoadSaveRoundTrip:
    def _setup_project(self, tmp_path: Path):
        """设置模拟项目根目录结构。"""
        (tmp_path / ".agent" / "instances").mkdir(parents=True, exist_ok=True)
        (tmp_path / ".agent" / "workflows" / "instances").mkdir(parents=True, exist_ok=True)
        # project root marker
        (tmp_path / ".git").mkdir(exist_ok=True)

    def test_save_and_load_v3(self, tmp_path: Path, monkeypatch):
        self._setup_project(tmp_path)
        monkeypatch.chdir(tmp_path)

        inst = InstanceState(
            instance_id="20260525-001",
            workflow_id="test-wf",
            version="1.0.0",
        )
        save_instance_state("20260525-001", inst)

        loaded = load_instance_state("20260525-001")
        assert loaded.instance_id == "20260525-001"
        assert loaded.workflow_id == "test-wf"

    def test_load_v3_not_found(self, tmp_path: Path, monkeypatch):
        self._setup_project(tmp_path)
        monkeypatch.chdir(tmp_path)

        with pytest.raises(InputError, match="Instance not found"):
            load_instance_state("nonexistent")

    def test_load_v3_corrupt_json(self, tmp_path: Path, monkeypatch):
        self._setup_project(tmp_path)
        monkeypatch.chdir(tmp_path)

        inst_dir = tmp_path / ".agent" / "instances" / "bad-inst"
        inst_dir.mkdir(parents=True)
        (inst_dir / "instance.json").write_text("{corrupt", encoding="utf-8")

        with pytest.raises(StateError, match="Corrupted instance.json"):
            load_instance_state("bad-inst")

    def test_v2_auto_migration(self, tmp_path: Path, monkeypatch):
        self._setup_project(tmp_path)
        monkeypatch.chdir(tmp_path)

        v2_path = tmp_path / ".agent" / "workflows" / "instances" / "old-instance.json"
        v2_path.parent.mkdir(parents=True, exist_ok=True)
        v2_path.write_text(json.dumps({
            "instance_id": "old-instance",
            "workflow_id": "old-wf",
            "version": "1.0.0",
            "goal": "test goal",
            "status": "ACTIVE",
            "consumed_message_ids": [],
            "stages": [
                {"stage_id": "s01", "status": "PENDING"},
            ],
        }), encoding="utf-8")

        inst = load_instance_state("old-instance")

        assert inst.instance_id == "old-instance"
        assert inst.workflow_id == "old-wf"
        assert inst.goal == "test goal"
        assert len(inst.stages) == 1
        assert inst.stages[0].stage_instance_id == "s01"

        # v3 目录已创建
        v3_path = tmp_path / ".agent" / "instances" / "old-instance" / "instance.json"
        assert v3_path.exists()

        # v2 文件已删除
        assert not v2_path.exists()

    def test_v2_migration_stage_instance_id_filled(self, tmp_path: Path, monkeypatch):
        """v2 stage 缺少 stage_instance_id → 迁移时用 stage_id 填充。"""
        self._setup_project(tmp_path)
        monkeypatch.chdir(tmp_path)

        v2_path = tmp_path / ".agent" / "workflows" / "instances" / "migrate-me.json"
        v2_path.parent.mkdir(parents=True, exist_ok=True)
        v2_path.write_text(json.dumps({
            "instance_id": "migrate-me",
            "stages": [
                {"stage_id": "s01", "status": "PENDING"},
                {"stage_id": "s02", "stage_instance_id": "s02_custom", "status": "DONE"},
            ],
        }), encoding="utf-8")

        inst = load_instance_state("migrate-me")
        assert inst.stages[0].stage_instance_id == "s01"
        assert inst.stages[1].stage_instance_id == "s02_custom"

    def test_cycle_meta_not_persisted(self, tmp_path: Path, monkeypatch):
        """cycle_meta 不持久化到 instance.json。"""
        self._setup_project(tmp_path)
        monkeypatch.chdir(tmp_path)

        inst = InstanceState(
            instance_id="test",
            cycle_meta=CycleMeta(
                newly_done_stage_instance_ids=frozenset(["a", "b"]),
            ),
        )
        save_instance_state("test", inst)

        loaded = load_instance_state("test")
        assert loaded.cycle_meta.newly_done_stage_instance_ids == frozenset()

    def test_save_creates_parent_dirs(self, tmp_path: Path, monkeypatch):
        self._setup_project(tmp_path)
        monkeypatch.chdir(tmp_path)

        inst = InstanceState(instance_id="new-id")
        save_instance_state("new-id", inst)

        path = tmp_path / ".agent" / "instances" / "new-id" / "instance.json"
        assert path.exists()
