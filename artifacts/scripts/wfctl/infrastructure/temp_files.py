"""临时文件 / 自动生成文件 / 非法文件名判断 —— 单一真相源。

所有需要过滤的模块（validator、handler、dashboard）统一从此导入。
"""

import unicodedata
from pathlib import Path

_CACHE_DIRS = frozenset({"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"})
_WFCTL_TEMP_FILES = frozenset({".wfctl_identity.json", ".wfctl_commit_msg"})
_PUA_RANGES = (
    (0xE000, 0xF8FF),   # Private Use Area
    (0xF0000, 0xFFFFD),  # Supplementary PUA-A
    (0x100000, 0x10FFFD),  # Supplementary PUA-B
)


def _contains_pua(text: str) -> bool:
    """检测文本是否包含 Unicode 私用区 (PUA) 字符。"""
    for ch in text:
        cp = ord(ch)
        for lo, hi in _PUA_RANGES:
            if lo <= cp <= hi:
                return True
    return False


def is_temp_file(path: str) -> bool:
    """判断文件是否应跳过（不在保护区校验和看板展示中出现）。

    覆盖：
    - Python 缓存目录：__pycache__/, .pytest_cache/, .mypy_cache/, .ruff_cache/
    - Python 字节码：*.pyc, *.pyo
    - wfctl 元数据：.wfctl_identity.json, .wfctl_commit_msg
    - 自动生成 .gitignore（worktree 创建时写入）
    - 目录条目：路径以 / 或 \\ 结尾
    - 包含 PUA 私用区字符的非法文件名（如 Windows 上 * → U+F02A）
    """
    # 目录（git status --porcelain 中 untracked 目录以 / 结尾）
    if path.endswith("/") or path.endswith("\\"):
        return True

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

    # PUA 字符：Windows 非法字符（如 *）被内核层替换为 PUA 码点
    if _contains_pua(path):
        return True

    return False
