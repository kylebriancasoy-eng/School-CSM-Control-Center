"""Single-instance protection for the desktop Control Center."""

from __future__ import annotations

from pathlib import Path

from school_csm_control_center.storage.file_safety import InterProcessFileLock


class AlreadyRunningError(RuntimeError):
    pass


class SingleInstanceGuard:
    """Hold an operating-system file lock for the lifetime of the process."""

    def __init__(self, lock_path: str | Path) -> None:
        self.path = Path(lock_path)
        self._lock = InterProcessFileLock(self.path, timeout=0.0)
        self._held = False

    def acquire(self) -> None:
        if self._held:
            return
        if not self._lock.acquire(timeout=0.0):
            raise AlreadyRunningError(
                "School CSM Control Center is already open for this Windows account."
            )
        self._held = True

    def release(self) -> None:
        if self._held:
            self._lock.release()
            self._held = False

    def __enter__(self) -> "SingleInstanceGuard":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.release()
