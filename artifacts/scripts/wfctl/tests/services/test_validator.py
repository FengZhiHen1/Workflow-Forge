"""测试 validator 服务。"""

import pytest

from core.errors import ValidationError
from services.validator import validate_modified_files


def test_validate_agent_protection(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    with pytest.raises(ValidationError) as exc_info:
        validate_modified_files(repo, [".agent/instances/x.json"], "s01")
    assert exc_info.value.code == "ACCESS_VIOLATION"


def test_validate_claude_protection(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    with pytest.raises(ValidationError) as exc_info:
        validate_modified_files(repo, [".claude/workflows/test.yaml"], "s01")
    assert exc_info.value.code == "ACCESS_VIOLATION"


def test_validate_git_protection(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    with pytest.raises(ValidationError) as exc_info:
        validate_modified_files(repo, [".git/config"], "s01")
    assert exc_info.value.code == "ACCESS_VIOLATION"


def test_validate_worktree_escape(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    with pytest.raises(ValidationError) as exc_info:
        validate_modified_files(repo, ["../outside.txt"], "s01")
    assert exc_info.value.code == "ACCESS_VIOLATION"


def test_validate_ok(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "src").mkdir()
    (repo / "src" / "main.py").write_text("x", encoding="utf-8")
    # 正常路径不应抛异常，返回 None 表示校验通过
    assert validate_modified_files(repo, ["src/main.py"], "s01") is None
