"""测试项目根发现。"""

from pathlib import Path

from core.project import find_root


def test_find_root_from_claude(tmp_path: Path):
    (tmp_path / ".claude").mkdir()
    sub = tmp_path / "a" / "b"
    sub.mkdir(parents=True)
    assert find_root(sub) == tmp_path


def test_find_root_from_agent(tmp_path: Path):
    (tmp_path / ".agent").mkdir()
    sub = tmp_path / "x"
    sub.mkdir()
    assert find_root(sub) == tmp_path


def test_find_root_not_found(tmp_path: Path, monkeypatch):
    isolated = tmp_path / "no_project_here"
    isolated.mkdir()
    # 确保向上查找时不命中任何 .claude/.agent
    original_exists = Path.exists
    def fake_exists(self):
        return False
    monkeypatch.setattr(Path, "exists", fake_exists)
    with pytest.raises(RuntimeError):
        find_root(isolated)


import pytest
