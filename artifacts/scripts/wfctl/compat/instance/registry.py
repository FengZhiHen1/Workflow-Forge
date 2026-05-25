"""instance.json 加载/保存，自动版本检测与迁移。

所有状态读写必须经过此模块，内部代码不应直接操作 instance.json 文件。
"""

from __future__ import annotations

import json
from pathlib import Path

from infrastructure.io import atomic_write_json
from infrastructure.errors import InputError, StateError
from infrastructure.project import find_root
from state.model import InstanceState
from compat import DataVersion
from compat.instance.v2 import V2InstanceAdapter


def load_instance_state(instance_id: str) -> InstanceState:
    """加载实例状态（自动处理 v2/v3 格式兼容）。

    - v3 实例：从 .agent/instances/{id}/instance.json 读取
    - v2 实例：从 .agent/workflows/instances/{id}.json 读取，
      迁移为 v3 格式保存，删除旧 v2 文件

    Returns:
        InstanceState（始终为当前标准格式）

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
        adapter = V2InstanceAdapter()
        standard_data = adapter.to_standard(json.loads(v2_path.read_text(encoding="utf-8")))
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
    """原子保存实例状态（始终以当前标准格式）。"""
    root = find_root()
    path = root / ".agent" / "instances" / instance_id / "instance.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, state.to_dict())

    # 自动生成/刷新 Dashboard（延迟导入避免循环依赖）
    try:
        from services.dashboard_builder import update_dashboards
        update_dashboards(instance_id)
    except Exception:
        pass
