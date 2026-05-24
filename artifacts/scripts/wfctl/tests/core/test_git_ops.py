"""测试 git_ops 薄封装层 — 验证命令构造与返回码传播。"""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.git_ops import (
    git_add_all,
    git_branch,
    git_checkout,
    git_commit,
    git_commit_file,
    git_fetch,
    git_merge,
    git_merge_abort,
    git_merge_base,
    git_rev_parse,
    git_status_porcelain,
    git_tag,
    git_tag_delete,
    git_tag_exists,
    git_worktree_add,
    git_worktree_list,
    git_worktree_prune,
    git_worktree_remove,
)


@pytest.fixture
def repo():
    return Path("/fake/repo")


# ─── git 命令构造验证（通过公开包装函数间接测试 _git）─────────────────


def test_git_command_construction(repo):
    """验证 git 命令正确构造：-C + repo + 子命令。"""
    with patch("core.git_ops.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="M file.txt\n", stderr="")
        rc, stdout, stderr = git_status_porcelain(repo)
        assert rc == 0
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "git"
        assert cmd[1] == "-C"
        assert cmd[2] == str(repo)
        assert cmd[3] == "status"
        assert cmd[4] == "--porcelain"


def test_git_error_propagation(repo):
    """验证 git 错误（非零返回码 + stderr）通过包装函数正确传播。"""
    with patch("core.git_ops.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="fatal: not a git repository")
        rc, stdout, stderr = git_status_porcelain(repo)
        assert rc == 1
        assert stderr == "fatal: not a git repository"


# ─── git_worktree_add ─────────────────────────────────────────────────


def test_worktree_add_basic(repo):
    path = Path("/tmp/wt")
    with patch("core.git_ops._git") as mock_git:
        mock_git.return_value = (0, "", "")
        rc, stdout, stderr = git_worktree_add(repo, path, "HEAD")
        assert rc == 0
        mock_git.assert_called_once_with(
            repo, "worktree", "add", str(path), "HEAD"
        )


def test_worktree_add_with_branch(repo):
    path = Path("/tmp/wt-branch")
    with patch("core.git_ops._git") as mock_git:
        mock_git.return_value = (0, "", "")
        rc, stdout, stderr = git_worktree_add(repo, path, "HEAD", branch="feature-x")
        assert rc == 0
        mock_git.assert_called_once_with(
            repo, "worktree", "add", "-b", "feature-x", str(path), "HEAD"
        )


# ─── git_worktree_remove ──────────────────────────────────────────────


def test_worktree_remove_basic(repo):
    path = Path("/tmp/wt")
    with patch("core.git_ops._git") as mock_git:
        mock_git.return_value = (0, "", "")
        rc, stdout, stderr = git_worktree_remove(repo, path)
        assert rc == 0
        mock_git.assert_called_once_with(
            repo, "worktree", "remove", str(path)
        )


def test_worktree_remove_force(repo):
    path = Path("/tmp/wt")
    with patch("core.git_ops._git") as mock_git:
        mock_git.return_value = (0, "", "")
        rc, stdout, stderr = git_worktree_remove(repo, path, force=True)
        assert rc == 0
        mock_git.assert_called_once_with(
            repo, "worktree", "remove", str(path), "--force"
        )


# ─── git_worktree_list ────────────────────────────────────────────────


def test_worktree_list(repo):
    with patch("core.git_ops._git") as mock_git:
        mock_git.return_value = (0, "worktree /path\nHEAD abc123\n", "")
        rc, stdout, stderr = git_worktree_list(repo)
        assert rc == 0
        mock_git.assert_called_once_with(repo, "worktree", "list", "--porcelain")


# ─── git_fetch ────────────────────────────────────────────────────────


def test_fetch(repo):
    source = Path("/source/repo")
    with patch("core.git_ops._git") as mock_git:
        mock_git.return_value = (0, "", "")
        rc, stdout, stderr = git_fetch(repo, source, "refs/heads/main")
        assert rc == 0
        mock_git.assert_called_once_with(
            repo, "fetch", str(source), "refs/heads/main"
        )


# ─── git_merge ────────────────────────────────────────────────────────


def test_merge_default_no_ff(repo):
    with patch("core.git_ops._git") as mock_git:
        mock_git.return_value = (0, "Merge made", "")
        rc, stdout, stderr = git_merge(repo, "feature-branch")
        assert rc == 0
        mock_git.assert_called_once_with(repo, "merge", "feature-branch", "--no-ff")


def test_merge_allow_ff(repo):
    with patch("core.git_ops._git") as mock_git:
        mock_git.return_value = (0, "Fast-forward", "")
        rc, stdout, stderr = git_merge(repo, "feature-branch", no_ff=False)
        assert rc == 0
        mock_git.assert_called_once_with(repo, "merge", "feature-branch")


# ─── git_checkout ─────────────────────────────────────────────────────


def test_checkout(repo):
    with patch("core.git_ops._git") as mock_git:
        mock_git.return_value = (0, "", "")
        rc, stdout, stderr = git_checkout(repo, "main")
        assert rc == 0
        mock_git.assert_called_once_with(repo, "checkout", "main")


# ─── git_tag / git_tag_delete / git_tag_exists ────────────────────────


def test_tag(repo):
    with patch("core.git_ops._git") as mock_git:
        mock_git.return_value = (0, "", "")
        rc, stdout, stderr = git_tag(repo, "v1.0", "HEAD")
        assert rc == 0
        mock_git.assert_called_once_with(repo, "tag", "v1.0", "HEAD")


def test_tag_delete(repo):
    with patch("core.git_ops._git") as mock_git:
        mock_git.return_value = (0, "", "")
        rc, stdout, stderr = git_tag_delete(repo, "v1.0")
        assert rc == 0
        mock_git.assert_called_once_with(repo, "tag", "-d", "v1.0")


def test_tag_exists_found(repo):
    with patch("core.git_ops._git") as mock_git:
        mock_git.return_value = (0, "v1.0\n", "")
        assert git_tag_exists(repo, "v1.0") is True


def test_tag_exists_not_found(repo):
    with patch("core.git_ops._git") as mock_git:
        mock_git.return_value = (0, "\n", "")
        assert git_tag_exists(repo, "v1.0") is False


def test_tag_exists_git_error(repo):
    with patch("core.git_ops._git") as mock_git:
        mock_git.return_value = (128, "", "fatal: not a git repo")
        assert git_tag_exists(repo, "v1.0") is False


# ─── git_status_porcelain ─────────────────────────────────────────────


def test_status_porcelain(repo):
    with patch("core.git_ops._git") as mock_git:
        mock_git.return_value = (0, "M file.txt\n", "")
        rc, stdout, stderr = git_status_porcelain(repo)
        assert rc == 0
        mock_git.assert_called_once_with(repo, "status", "--porcelain")


# ─── git_merge_base ───────────────────────────────────────────────────


def test_merge_base(repo):
    with patch("core.git_ops._git") as mock_git:
        mock_git.return_value = (0, "abc123def456\n", "")
        rc, stdout, stderr = git_merge_base(repo, "main", "feature")
        assert rc == 0
        mock_git.assert_called_once_with(repo, "merge-base", "main", "feature")


# ─── git_worktree_prune ───────────────────────────────────────────────


def test_worktree_prune(repo):
    with patch("core.git_ops._git") as mock_git:
        mock_git.return_value = (0, "", "")
        rc, stdout, stderr = git_worktree_prune(repo)
        assert rc == 0
        mock_git.assert_called_once_with(repo, "worktree", "prune")


# ─── git_rev_parse ────────────────────────────────────────────────────


def test_rev_parse(repo):
    with patch("core.git_ops._git") as mock_git:
        mock_git.return_value = (0, "abc123\n", "")
        rc, stdout, stderr = git_rev_parse(repo, "HEAD")
        assert rc == 0
        mock_git.assert_called_once_with(repo, "rev-parse", "HEAD")


# ─── git_add_all ──────────────────────────────────────────────────────


def test_add_all(repo):
    with patch("core.git_ops._git") as mock_git:
        mock_git.return_value = (0, "", "")
        rc, stdout, stderr = git_add_all(repo)
        assert rc == 0
        mock_git.assert_called_once_with(repo, "add", "-A")


# ─── git_commit ───────────────────────────────────────────────────────


def test_commit(repo):
    with patch("core.git_ops._git") as mock_git:
        mock_git.return_value = (0, "", "")
        rc, stdout, stderr = git_commit(repo, "test commit")
        assert rc == 0
        mock_git.assert_called_once_with(repo, "commit", "-m", "test commit")


# ─── git_commit_file ──────────────────────────────────────────────────


def test_commit_file(repo):
    msg_file = Path("/tmp/msg.txt")
    with patch("core.git_ops._git") as mock_git:
        mock_git.return_value = (0, "", "")
        rc, stdout, stderr = git_commit_file(repo, msg_file)
        assert rc == 0
        mock_git.assert_called_once_with(repo, "commit", "-F", str(msg_file))


# ─── git_merge_abort ──────────────────────────────────────────────────


def test_merge_abort(repo):
    with patch("core.git_ops._git") as mock_git:
        mock_git.return_value = (0, "", "")
        rc, stdout, stderr = git_merge_abort(repo)
        assert rc == 0
        mock_git.assert_called_once_with(repo, "merge", "--abort")


# ─── git_branch ───────────────────────────────────────────────────────


def test_branch_default_ref(repo):
    with patch("core.git_ops._git") as mock_git:
        mock_git.return_value = (0, "", "")
        rc, stdout, stderr = git_branch(repo, "feature-x")
        assert rc == 0
        mock_git.assert_called_once_with(repo, "branch", "feature-x", "HEAD")


def test_branch_explicit_ref(repo):
    with patch("core.git_ops._git") as mock_git:
        mock_git.return_value = (0, "", "")
        rc, stdout, stderr = git_branch(repo, "feature-x", "abc123")
        assert rc == 0
        mock_git.assert_called_once_with(repo, "branch", "feature-x", "abc123")


# ─── 集成：真实 subprocess 调用验证 ──────────────────────────────────


def test_git_invocation_in_real_repo(tmp_path: Path):
    """在真实 git 仓库中验证 _git 可正常执行。"""
    import subprocess as sp

    repo = tmp_path / "repo"
    repo.mkdir()
    sp.run(["git", "init"], cwd=str(repo), check=True, capture_output=True)
    sp.run(["git", "config", "user.email", "t@t.com"], cwd=str(repo), check=True, capture_output=True)
    sp.run(["git", "config", "user.name", "T"], cwd=str(repo), check=True, capture_output=True)
    (repo / "f.txt").write_text("hello", encoding="utf-8")

    rc, stdout, stderr = git_status_porcelain(repo)
    assert rc == 0
    assert "f.txt" in stdout
