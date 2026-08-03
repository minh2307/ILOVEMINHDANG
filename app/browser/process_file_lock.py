from __future__ import annotations

import fcntl
import os
import time
from pathlib import Path


class Timeout(TimeoutError):
    pass


class FileLock:
    """Small POSIX fallback matching the subset of `filelock.FileLock` we use."""

    def __init__(self, lock_file: str, timeout: float = -1):
        self.lock_file = Path(lock_file)
        self.timeout = timeout
        self._fd: int | None = None

    def acquire(self):
        self.lock_file.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.lock_file, os.O_CREAT | os.O_RDWR, 0o600)
        deadline = None if self.timeout < 0 else time.monotonic() + self.timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self._fd = fd
                return self
            except BlockingIOError:
                if deadline is not None and time.monotonic() >= deadline:
                    os.close(fd)
                    raise Timeout(f"Lock unavailable: {self.lock_file}")
                time.sleep(0.05)

    def release(self) -> None:
        if self._fd is None:
            return
        fcntl.flock(self._fd, fcntl.LOCK_UN)
        os.close(self._fd)
        self._fd = None
