"""V2 实例数据适配器：v2 → v3 迁移。"""

from __future__ import annotations

from typing import Any


class V2InstanceAdapter:
    """将 v2 格式的 instance.json dict 转为 v3 标准格式。

    v2 格式特点：
    - 无 stage_instance_id（stage_id 即实例 ID）
    - 存储在 .agent/workflows/instances/{id}.json
    - 缺少 consumed_message_ids、parent_instance_id、merge_confirmed 字段
    """

    def to_standard(self, raw: dict[str, Any]) -> dict[str, Any]:
        """v2 → v3 迁移。"""
        data = dict(raw)
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
