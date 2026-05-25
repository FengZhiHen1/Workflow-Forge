"""适配器协议。

定义 WORKFLOW.yaml 和 instance.json 的版本适配器接口。
后续版本只需实现对应协议，在注册表中挂载即可。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class WorkflowAdapter(Protocol):
    """WORKFLOW.yaml 解析适配器。

    每个 schema_version 对应一个实现，将 YAML 原始数据
    解析为标准的 WorkflowSpec。
    """

    def parse(self, raw: dict) -> "domain.workflow.spec.WorkflowSpec":
        """解析 YAML 原始数据为 WorkflowSpec。"""
        ...


@runtime_checkable
class InstanceAdapter(Protocol):
    """instance.json 适配器。

    处理实例数据的版本差异：旧格式 → 标准 dict → 新格式。
    """

    def to_standard(self, raw: dict) -> dict:
        """将旧版本 dict 转为当前标准格式。"""
        ...

    def from_standard(self, standard: dict) -> dict:
        """将标准格式 dict 转为目标版本格式（向下兼容写入）。"""
        ...
