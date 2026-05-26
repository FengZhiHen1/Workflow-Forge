"""临时文件 / 自动生成文件判断 —— 单一真相源。

所有需要过滤临时文件的模块（validator、handler、dashboard）统一从此导入。
"""

from pathlib import Path

_CACHE_DIRS = frozenset({"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"})
_WFCTL_TEMP_FILES = frozenset({".wfctl_identity.json", ".wfctl_commit_msg"})


def is_temp_file(path: str) -> bool:
    """判断文件是否为临时文件/自动生成文件，应在保护区校验和看板展示中跳过。

    覆盖：
    - Python 缓存目录：__pycache__/, .pytest_cache/, .mypy_cache/, .ruff_cache/
    - Python 字节码：*.pyc, *.pyo
    - wfctl 元数据：.wfctl_identity.json, .wfctl_commit_msg
    - 自动生成 .gitignore（worktree 创建时写入）
    """
    parts_lower = [p.lower() for p in Path(path).parts]
    if any(p in _CACHE_DIRS for p in parts_lower):
        return True
    filename = Path(path).name.lower()
    if filename in _WFCTL_TEMP_FILES:
        return True
    if filename == ".gitignore":
        return True
    if filename.endswith(".pyc") or filename.endswith(".pyo"):
        return True
    return False
