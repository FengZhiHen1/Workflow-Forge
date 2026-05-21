"""测试异常体系。"""

import pytest

from core.errors import (
    GitError,
    InputError,
    SchemaError,
    StateError,
    ValidationError,
    WfctlError,
    WorktreeError,
)


def test_wfctl_error_base():
    e = WfctlError("base error", code="TEST")
    assert e.code == "TEST"
    assert e.exit_code == 1


def test_input_error_exit_code():
    e = InputError("bad arg")
    assert e.exit_code == 2


def test_state_error():
    e = StateError("corrupted", code="STATE_CORRUPTED")
    assert e.code == "STATE_CORRUPTED"


def test_worktree_error():
    e = WorktreeError("fail", code="WORKTREE_CREATE_FAILED")
    assert e.code == "WORKTREE_CREATE_FAILED"


def test_schema_error():
    e = SchemaError("parse fail", code="SCHEMA_PARSE_ERROR")
    assert e.code == "SCHEMA_PARSE_ERROR"


def test_validation_error():
    e = ValidationError("access denied", code="ACCESS_VIOLATION")
    assert e.code == "ACCESS_VIOLATION"


def test_git_error():
    e = GitError("git fail", code="GIT_COMMAND_FAILED")
    assert e.code == "GIT_COMMAND_FAILED"
