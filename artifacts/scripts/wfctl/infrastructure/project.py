"""项目根发现（向上查找 .claude/ 目录）。"""

from pathlib import Path


def find_root(cwd: Path | None = None) -> Path:
    """从 cwd 向上查找包含 .claude/ 或 .agent/ 的目录作为项目根。"""
    if cwd is None:
        cwd = Path.cwd()
    current = cwd.resolve()
    for parent in [current] + list(current.parents):
        if (parent / ".claude").exists() or (parent / ".agent").exists():
            return parent
    raise RuntimeError(f"Project root not found from {cwd}: no .claude/ or .agent/ directory")
