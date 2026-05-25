"""原子写入（tmp + os.replace）。"""

import json
import os
from pathlib import Path


def atomic_write_json(path: Path, data: dict) -> None:
    """写入临时文件后 os.replace，保证写入原子性。"""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(str(tmp), str(path))


def atomic_write_text(path: Path, text: str) -> None:
    """原子写入文本。"""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(str(tmp), str(path))
