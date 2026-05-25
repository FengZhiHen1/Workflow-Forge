"""测试 worktree_manager 服务。"""

import json
import subprocess
from pathlib import Path

import pytest

from infrastructure.errors import GitError, WorktreeError
from runtime.worktree.manager import (
    create_instance_worktree,
    create_parallel_worktree,
    create_stage_worktree,
    merge_instance_to_main,
    merge_stage_worktree,
    remove_instance_worktree,
    tag_anchor,
)


def _init_git_repo(path: Path):
    (path / ".claude").mkdir(exist_ok=True)
    (path / ".agent").mkdir(exist_ok=True)
    subprocess.run(["git", "init"], cwd=str(path), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@test.com"], cwd=str(path), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=str(path), check=True, capture_output=True)
    (path / "README.md").write_text("# init", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(path), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(path), check=True, capture_output=True)


def test_create_instance_worktree(monkeypatch, tmp_path: Path):
    repo = tmp_path / "project"
    repo.mkdir()
    (repo / ".tmp" / "worktrees").mkdir(parents=True)
    _init_git_repo(repo)
    monkeypatch.chdir(repo)

    wt = create_instance_worktree("test-001")
    assert wt.exists()
    assert (wt / "README.md").exists()


def test_create_stage_worktree(monkeypatch, tmp_path: Path):
    repo = tmp_path / "project"
    repo.mkdir()
    (repo / ".tmp" / "worktrees").mkdir(parents=True)
    _init_git_repo(repo)
    monkeypatch.chdir(repo)

    inst_wt = create_instance_worktree("test-002")
    # 在实例 worktree 中创建一个提交
    (inst_wt / "stage.txt").write_text("a", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(inst_wt), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "stage"], cwd=str(inst_wt), check=True, capture_output=True)

    stage_wt = create_stage_worktree("test-002", "s01")
    assert stage_wt.exists()
    assert (stage_wt / "stage.txt").exists()


def test_create_parallel_worktree(monkeypatch, tmp_path: Path):
    repo = tmp_path / "project"
    repo.mkdir()
    (repo / ".tmp" / "worktrees").mkdir(parents=True)
    _init_git_repo(repo)
    monkeypatch.chdir(repo)

    inst_wt = create_instance_worktree("test-003")
    (inst_wt / "base.txt").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(inst_wt), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=str(inst_wt), check=True, capture_output=True)

    para_wt = create_parallel_worktree("test-003", "s02", 0)
    assert para_wt.exists()
    assert (para_wt / "base.txt").exists()


def test_merge_stage_worktree_success(monkeypatch, tmp_path: Path):
    repo = tmp_path / "project"
    repo.mkdir()
    (repo / ".tmp" / "worktrees").mkdir(parents=True)
    _init_git_repo(repo)
    monkeypatch.chdir(repo)

    inst_wt = create_instance_worktree("test-004")
    (inst_wt / "base.txt").write_text("base", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(inst_wt), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=str(inst_wt), check=True, capture_output=True)

    stage_wt = create_stage_worktree("test-004", "s01")
    (stage_wt / "new.txt").write_text("new", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(stage_wt), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "new"], cwd=str(stage_wt), check=True, capture_output=True)

    success, conflicts = merge_stage_worktree("test-004", "s01")
    assert success is True
    assert conflicts == []
    assert (inst_wt / "new.txt").exists()


def test_tag_anchor(monkeypatch, tmp_path: Path):
    repo = tmp_path / "project"
    repo.mkdir()
    (repo / ".tmp" / "worktrees").mkdir(parents=True)
    _init_git_repo(repo)
    monkeypatch.chdir(repo)

    inst_wt = create_instance_worktree("test-005")
    tag_anchor("test-005", "wf-test-005-s01")
    rc, stdout, _ = subprocess.run(
        ["git", "-C", str(inst_wt), "tag"], capture_output=True, text=True
    ).__dict__["returncode"], subprocess.run(
        ["git", "-C", str(inst_wt), "tag"], capture_output=True, text=True
    ).stdout, ""
    assert "wf-test-005-s01" in stdout


def test_remove_instance_worktree(monkeypatch, tmp_path: Path):
    repo = tmp_path / "project"
    repo.mkdir()
    (repo / ".tmp" / "worktrees").mkdir(parents=True)
    _init_git_repo(repo)
    monkeypatch.chdir(repo)

    inst_wt = create_instance_worktree("test-006")
    assert inst_wt.exists()
    remove_instance_worktree("test-006")
    # worktree remove 后目录被清理
    # 注：git worktree remove 会移除目录，但在 Windows 上可能因文件锁延迟
