"""测试跨平台文件锁。"""

import os
import time
from pathlib import Path

import pytest

from infrastructure.lock import FileLock


def test_lock_acquire_release(tmp_path: Path):
    target = tmp_path / "state.json"
    lock = FileLock(target)
    assert lock.acquire(timeout=1.0)
    assert lock._acquired
    lock.release()
    assert not lock._acquired
    assert not lock.lock_path.exists()


def test_lock_context_manager(tmp_path: Path):
    target = tmp_path / "state.json"
    lock = FileLock(target)
    with lock:
        assert lock._acquired
        assert lock.lock_path.exists()
    assert not lock._acquired


def test_lock_timeout(tmp_path: Path):
    target = tmp_path / "state.json"
    lock1 = FileLock(target)
    lock2 = FileLock(target)
    assert lock1.acquire(timeout=1.0)
    # lock2 应超时
    assert not lock2.acquire(timeout=0.2)
    lock1.release()


def test_lock_dead_pid_steal(tmp_path: Path):
    """模拟死进程持有的锁应被抢夺。"""
    target = tmp_path / "state.json"
    lock_path = target.with_suffix(target.suffix + ".lock")
    # 写入一个不存在的 PID
    lock_path.write_text("99999999:1234567890", encoding="utf-8")

    lock = FileLock(target)
    assert lock.acquire(timeout=1.0)
    assert lock._acquired
    lock.release()


def test_lock_concurrent_exclusive(tmp_path: Path):
    """两个锁对象竞争同一文件。"""
    target = tmp_path / "state.json"
    lock_a = FileLock(target)
    lock_b = FileLock(target)

    assert lock_a.acquire(timeout=1.0)
    assert not lock_b.acquire(timeout=0.2)

    lock_a.release()
    assert lock_b.acquire(timeout=1.0)
    lock_b.release()
