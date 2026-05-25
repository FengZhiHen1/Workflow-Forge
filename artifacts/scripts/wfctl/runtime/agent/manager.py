"""RunningAgentManager: running_agents.json 的集中管理。

消除 services/scheduler_legacy.py 和 processors/allocate_spawn.py
之间的代码重复。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from infrastructure.io import atomic_write_json
from infrastructure.project import find_root


class RunningAgentManager:
    """SubAgent 生命周期管理器。

    管理 .agent/running_agents.json 中的项目级 SubAgent 注册表。
    每条记录包含: skill_id, system_agent_id, stage_id, instance_id
    """

    def __init__(self, root: Path | None = None):
        self._root = root or find_root()

    @property
    def _path(self) -> Path:
        return self._root / ".agent" / "running_agents.json"

    def load(self) -> list[dict[str, Any]]:
        """加载全部 running_agents 记录。"""
        if not self._path.exists():
            return []
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, Exception):
            return []

    def save(self, agents: list[dict[str, Any]]) -> None:
        """原子保存 running_agents 记录。"""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self._path, agents)

    def lookup(self, skill_id: str) -> dict[str, Any] | None:
        """按 skill_id 查找存活的 SubAgent（多条命中时取第一条）。"""
        for agent in self.load():
            if agent.get("skill_id") == skill_id:
                return agent
        return None

    def register(self, agent: dict[str, Any]) -> None:
        """注册或更新 SubAgent（按 system_agent_id 去重）。

        Args:
            agent: {"skill_id", "system_agent_id", "stage_id", "instance_id"}
        """
        agents = self.load()
        sid = agent.get("system_agent_id")
        agents = [a for a in agents if a.get("system_agent_id") != sid]
        agents.append(agent)
        self.save(agents)

    def remove_for_instance(
        self, instance_id: str, stage_ids: list[str] | None = None
    ) -> None:
        """移除指定实例的 running_agents。

        Args:
            instance_id: 实例 ID
            stage_ids: 可选，仅移除匹配的 stage_id
        """
        agents = self.load()
        if stage_ids:
            stage_set = set(stage_ids)
            agents = [
                a for a in agents
                if not (
                    a.get("instance_id") == instance_id
                    and a.get("stage_id") in stage_set
                )
            ]
        else:
            agents = [a for a in agents if a.get("instance_id") != instance_id]
        self.save(agents)

    def remove_by_system_agent_id(self, system_agent_id: str) -> None:
        """按 system_agent_id 移除单个 SubAgent。"""
        agents = self.load()
        agents = [a for a in agents if a.get("system_agent_id") != system_agent_id]
        self.save(agents)
