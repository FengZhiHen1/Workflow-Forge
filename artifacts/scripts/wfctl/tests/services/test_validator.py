"""测试 validator 服务。"""

import pytest

from infrastructure.errors import ValidationError
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


def test_pycache_under_claude_is_filtered(tmp_path):
    """__pycache__ 在 .claude/ 下也不触发 ACCESS_VIOLATION（自动生成文件过滤）。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    assert validate_modified_files(
        repo, [".claude/scripts/wfctl/__pycache__/foo.cpython-312.pyc"], "s01"
    ) is None


def test_pytest_cache_is_filtered(tmp_path):
    """.pytest_cache 在任何路径下都不触发 ACCESS_VIOLATION。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    assert validate_modified_files(
        repo, [".claude/.pytest_cache/v/cache/lastfailed"], "s01"
    ) is None


def test_pyc_file_is_filtered(tmp_path):
    """*.pyc 文件在任何路径下都不触发 ACCESS_VIOLATION。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    assert validate_modified_files(
        repo, [".agent/__pycache__/module.pyc"], "s01"
    ) is None


def test_legit_claude_still_blocked(tmp_path):
    """非自动生成文件修改 .claude/ 仍触发 ACCESS_VIOLATION。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    with pytest.raises(ValidationError) as exc_info:
        validate_modified_files(repo, [".claude/settings.json"], "s01")
    assert exc_info.value.code == "ACCESS_VIOLATION"


def test_directory_entry_is_filtered(tmp_path):
    """git status 中 untracked 目录（以 / 结尾）被过滤。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    assert validate_modified_files(
        repo, [".claude/docs/some-dir/"], "s01"
    ) is None


def test_pua_filename_is_filtered(tmp_path):
    """包含 PUA 私用区字符 (U+F02A) 的文件名被过滤。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    assert validate_modified_files(
        repo, [".claude/file.txt"], "s01"
    ) is None
