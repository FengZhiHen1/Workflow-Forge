"""版本兼容层。

所有版本差异在此消化，对上层暴露统一的标准模型。
- WORKFLOW.yaml 适配器 → compat/workflow/
- instance.json 适配器 → compat/instance/
"""

from enum import Enum


class DataVersion(Enum):
    """数据格式版本枚举。"""
    V2 = "2.0.0"
    V3 = "3.0.0"


# 当前工作格式——所有新数据按此版本写入
CURRENT = DataVersion.V3
