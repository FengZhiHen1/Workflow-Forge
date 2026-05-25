"""测试原子写入。"""

import json
from pathlib import Path

from infrastructure.io import atomic_write_json, atomic_write_text


def test_atomic_write_json(tmp_path: Path):
    path = tmp_path / "test.json"
    atomic_write_json(path, {"key": "value"})
    assert path.exists()
    assert json.loads(path.read_text(encoding="utf-8")) == {"key": "value"}


def test_atomic_write_text(tmp_path: Path):
    path = tmp_path / "test.txt"
    atomic_write_text(path, "hello")
    assert path.read_text(encoding="utf-8") == "hello"
