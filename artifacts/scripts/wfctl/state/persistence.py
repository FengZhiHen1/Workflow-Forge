"""状态持久化兼容层。

实现"读取时适配"：v2 实例首次加载自动迁移到 v3 格式。
后续代码永远只处理 InstanceState。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from infrastructure.io import atomic_write_json
from infrastructure.errors import InputError, StateError
from infrastructure.project import find_root
from state.model import InstanceState


class DataVersion(Enum):
    V2 = "2.0.0"
    V3 = "3.0.0"


@dataclass(frozen=True)
class InstanceDataAdapter:
    """V2 实例数据适配器（只读，不写回旧格式）。"""

    raw: dict[str, Any]
    declared_version: DataVersion

    @classmethod
    def from_file(cls, path: Path) -> "InstanceDataAdapter":
        """从文件加载原始数据并检测版本。"""
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise StateError(
                f"Corrupted instance file: {e}", code="STATE_CORRUPTED"
            )

        version_str = data.get("schema_version", "")
        if version_str == "3.0.0":
            version = DataVersion.V3
        else:
            version = DataVersion.V2

        return cls(raw=data, declared_version=version)

    def to_standard(self) -> dict[str, Any]:
        """转换为 v3 标准格式 dict。"""
        if self.declared_version == DataVersion.V3:
            return dict(self.raw)
        return self._migrate_v2_to_v3()

    def _migrate_v2_to_v3(self) -> dict[str, Any]:
        """V2 → V3 迁移规则。"""
        data = dict(self.raw)
        data["schema_version"] = "3.0.0"

        for stage in data.get("stages", []):
            if "stage_instance_id" not in stage:
                stage["stage_instance_id"] = stage.get("stage_id", "")

        if "consumed_message_ids" not in data:
            data["consumed_message_ids"] = []

        if "parent_instance_id" not in data:
            data["parent_instance_id"] = None

        if "merge_confirmed" not in data:
            data["merge_confirmed"] = False

        return data


def load_instance_state(instance_id: str) -> InstanceState:
    """加载实例状态（自动处理 v2/v3 格式兼容）。

    - v3 实例：从 .agent/instances/{id}/instance.json 读取
    - v2 实例：从 .agent/workflows/instances/{id}.json 读取，
      迁移为 v3 格式保存，删除旧 v2 文件

    Returns:
        InstanceState（始终为标准化格式）

    Raises:
        InputError: 实例不存在
        StateError: 文件损坏
    """
    root = find_root()
    v3_path = root / ".agent" / "instances" / instance_id / "instance.json"
    v2_path = root / ".agent" / "workflows" / "instances" / f"{instance_id}.json"

    if v3_path.exists():
        try:
            data = json.loads(v3_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise StateError(
                f"Corrupted instance.json for {instance_id}: {e}",
                code="STATE_CORRUPTED",
            )
        return InstanceState.from_dict(data)

    if v2_path.exists():
        adapter = InstanceDataAdapter.from_file(v2_path)
        standard_data = adapter.to_standard()
        inst_state = InstanceState.from_dict(standard_data)

        save_instance_state(instance_id, inst_state)

        try:
            v2_path.unlink()
        except OSError:
            pass

        return inst_state

    raise InputError(
        f"Instance not found: {instance_id}", code="INSTANCE_NOT_FOUND"
    )


def save_instance_state(instance_id: str, state: InstanceState) -> None:
    """原子保存实例状态为 v3 格式。

    Args:
        instance_id: 实例 ID
        state: InstanceState 对象
    """
    root = find_root()
    path = root / ".agent" / "instances" / instance_id / "instance.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, state.to_dict())
