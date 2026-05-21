"""跨平台文件锁。"""

import os
import time
from pathlib import Path


class FileLock:
    """跨平台互斥锁。

    实现：在目标文件旁创建 .lock 文件，写入 "pid:timestamp"。
    获取前检查 pid 是否存活，死则抢锁。不依赖 fcntl.flock。
    """

    def __init__(self, path: Path):
        self.lock_path = path.with_suffix(path.suffix + ".lock")
        self._acquired = False

    def acquire(self, timeout: float = 10.0) -> bool:
        """尝试获取锁，超时返回 False。"""
        start = time.time()
        while True:
            if self._try_acquire():
                return True
            if time.time() - start > timeout:
                return False
            time.sleep(0.1)

    def release(self) -> None:
        """释放锁。"""
        if self._acquired and self.lock_path.exists():
            try:
                self.lock_path.unlink()
            except OSError:
                pass
        self._acquired = False

    def _try_acquire(self) -> bool:
        if self.lock_path.exists():
            try:
                content = self.lock_path.read_text(encoding="utf-8").strip()
                pid_str, _ = content.split(":", 1)
                pid = int(pid_str)
                if self._pid_alive(pid):
                    return False
            except (ValueError, OSError):
                pass
            # 死锁或损坏，尝试删除
            try:
                self.lock_path.unlink()
            except OSError:
                return False
        try:
            self.lock_path.write_text(f"{os.getpid()}:{time.time()}", encoding="utf-8")
            self._acquired = True
            return True
        except OSError:
            return False

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        """检查进程是否存活。"""
        if os.name == "nt":
            import ctypes

            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(1, False, pid)
            if handle:
                kernel32.CloseHandle(handle)
                return True
            return False
        else:
            try:
                os.kill(pid, 0)
                return True
            except OSError:
                return False

    def __enter__(self):
        if not self.acquire():
            raise RuntimeError(f"Could not acquire lock: {self.lock_path}")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()
        return False
