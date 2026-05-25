"""测试 runtime.agent.manager — RunningAgentManager。"""

import json
from pathlib import Path

import pytest

from runtime.agent.manager import RunningAgentManager


class TestRunningAgentManager:
    def test_load_empty(self, tmp_path: Path):
        mgr = RunningAgentManager(root=tmp_path)
        assert mgr.load() == []

    def test_register_and_load(self, tmp_path: Path):
        mgr = RunningAgentManager(root=tmp_path)
        agent = {
            "skill_id": "skill-a",
            "system_agent_id": "sys-001",
            "stage_id": "s01",
            "instance_id": "inst-1",
        }
        mgr.register(agent)
        all_agents = mgr.load()
        assert len(all_agents) == 1
        assert all_agents[0]["skill_id"] == "skill-a"

    def test_register_deduplicates_by_system_agent_id(self, tmp_path: Path):
        mgr = RunningAgentManager(root=tmp_path)
        mgr.register({
            "skill_id": "skill-a",
            "system_agent_id": "sys-001",
            "stage_id": "s01",
            "instance_id": "inst-1",
        })
        mgr.register({
            "skill_id": "skill-b",
            "system_agent_id": "sys-001",  # 相同 system_agent_id
            "stage_id": "s02",
            "instance_id": "inst-1",
        })
        all_agents = mgr.load()
        assert len(all_agents) == 1
        assert all_agents[0]["skill_id"] == "skill-b"

    def test_lookup_found(self, tmp_path: Path):
        mgr = RunningAgentManager(root=tmp_path)
        mgr.register({
            "skill_id": "skill-x",
            "system_agent_id": "sys-x",
            "stage_id": "s01",
            "instance_id": "inst-1",
        })
        found = mgr.lookup("skill-x")
        assert found is not None
        assert found["system_agent_id"] == "sys-x"

    def test_lookup_not_found(self, tmp_path: Path):
        mgr = RunningAgentManager(root=tmp_path)
        assert mgr.lookup("nonexistent") is None

    def test_remove_for_instance_all(self, tmp_path: Path):
        mgr = RunningAgentManager(root=tmp_path)
        mgr.register({
            "skill_id": "skill-a",
            "system_agent_id": "sys-a",
            "stage_id": "s01",
            "instance_id": "inst-1",
        })
        mgr.register({
            "skill_id": "skill-b",
            "system_agent_id": "sys-b",
            "stage_id": "s02",
            "instance_id": "inst-2",
        })
        mgr.remove_for_instance("inst-1")
        all_agents = mgr.load()
        assert len(all_agents) == 1
        assert all_agents[0]["instance_id"] == "inst-2"

    def test_remove_for_instance_with_stage_ids(self, tmp_path: Path):
        mgr = RunningAgentManager(root=tmp_path)
        mgr.register({
            "skill_id": "skill-a",
            "system_agent_id": "sys-a",
            "stage_id": "s01",
            "instance_id": "inst-1",
        })
        mgr.register({
            "skill_id": "skill-b",
            "system_agent_id": "sys-b",
            "stage_id": "s02",
            "instance_id": "inst-1",
        })
        # 仅移除 s01
        mgr.remove_for_instance("inst-1", stage_ids=["s01"])
        all_agents = mgr.load()
        assert len(all_agents) == 1
        assert all_agents[0]["stage_id"] == "s02"

    def test_remove_by_system_agent_id(self, tmp_path: Path):
        mgr = RunningAgentManager(root=tmp_path)
        mgr.register({
            "skill_id": "skill-a",
            "system_agent_id": "sys-a",
            "stage_id": "s01",
            "instance_id": "inst-1",
        })
        mgr.register({
            "skill_id": "skill-b",
            "system_agent_id": "sys-b",
            "stage_id": "s02",
            "instance_id": "inst-1",
        })
        mgr.remove_by_system_agent_id("sys-a")
        all_agents = mgr.load()
        assert len(all_agents) == 1
        assert all_agents[0]["system_agent_id"] == "sys-b"

    def test_custom_root(self, tmp_path: Path):
        custom = tmp_path / "custom"
        custom.mkdir()
        mgr = RunningAgentManager(root=custom)
        mgr.register({
            "skill_id": "skill-a",
            "system_agent_id": "sys-a",
            "stage_id": "s01",
            "instance_id": "inst-1",
        })
        path = custom / ".agent" / "running_agents.json"
        assert path.exists()

    def test_load_corrupt_json(self, tmp_path: Path):
        agent_dir = tmp_path / ".agent"
        agent_dir.mkdir(parents=True)
        (agent_dir / "running_agents.json").write_text("{corrupt", encoding="utf-8")

        mgr = RunningAgentManager(root=tmp_path)
        assert mgr.load() == []

    def test_save_persistence(self, tmp_path: Path):
        mgr = RunningAgentManager(root=tmp_path)
        agent = {
            "skill_id": "skill-a",
            "system_agent_id": "sys-001",
            "stage_id": "s01",
            "instance_id": "inst-1",
        }
        mgr.register(agent)

        # 新 manager 实例从同一文件加载
        mgr2 = RunningAgentManager(root=tmp_path)
        loaded = mgr2.load()
        assert len(loaded) == 1
        assert loaded[0]["skill_id"] == "skill-a"
