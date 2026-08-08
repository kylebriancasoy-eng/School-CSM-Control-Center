"""Cross-process locks, verified recovery copies, and durable JSON replacement."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from threading import RLock
import time
from typing import Any, Mapping


class FileLockTimeoutError(TimeoutError):
    pass


@dataclass
class _SharedLockState:
    thread_lock: RLock = field(default_factory=RLock)
    depth: int = 0
    handle: Any = None


_REGISTRY_LOCK = RLock()
_LOCK_STATES: dict[str, _SharedLockState] = {}


def lock_path_for(data_path: str | Path) -> Path:
    path = Path(data_path)
    return path.parent / ".locks" / f"{path.name}.lock"


class InterProcessFileLock:
    """A process-wide re-entrant lock backed by a one-byte operating-system lock."""

    def __init__(self, path: str | Path, *, timeout: float = 15.0) -> None:
        self.path = Path(path).expanduser().resolve()
        self.timeout = max(0.0, float(timeout))
        key = os.path.normcase(str(self.path))
        with _REGISTRY_LOCK:
            self._state = _LOCK_STATES.setdefault(key, _SharedLockState())
        self._local_depth = 0

    def acquire(self, timeout: float | None = None) -> bool:
        wait = self.timeout if timeout is None else max(0.0, float(timeout))
        deadline = time.monotonic() + wait
        if not self._state.thread_lock.acquire(timeout=wait):
            return False
        acquired_this_call = False
        try:
            if self._state.depth == 0:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                handle = self.path.open("a+b")
                if handle.seek(0, os.SEEK_END) == 0:
                    handle.write(b"\0")
                    handle.flush()
                while True:
                    try:
                        _lock_handle(handle)
                        break
                    except (BlockingIOError, OSError):
                        if time.monotonic() >= deadline:
                            handle.close()
                            return False
                        time.sleep(min(0.05, max(0.001, deadline - time.monotonic())))
                self._state.handle = handle
            self._state.depth += 1
            self._local_depth += 1
            acquired_this_call = True
            return True
        finally:
            if not acquired_this_call:
                self._state.thread_lock.release()

    def release(self) -> None:
        if self._local_depth <= 0:
            return
        try:
            self._state.depth -= 1
            self._local_depth -= 1
            if self._state.depth == 0:
                handle = self._state.handle
                self._state.handle = None
                if handle is not None:
                    try:
                        _unlock_handle(handle)
                    finally:
                        handle.close()
        finally:
            self._state.thread_lock.release()

    def __enter__(self) -> "InterProcessFileLock":
        if not self.acquire():
            raise FileLockTimeoutError(f"Timed out waiting for file lock: {self.path}")
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.release()


class InterProcessRLock(InterProcessFileLock):
    """Cross-process lock for a mutable data file.

    Store callers pass the data-file path.  The operating-system lock belongs
    in a sibling ``.locks`` directory so acquiring a lock never creates or
    modifies the JSON/MOSSJSON file itself.
    """

    def __init__(self, data_path: str | Path, *, timeout: float = 15.0) -> None:
        super().__init__(lock_path_for(data_path), timeout=timeout)


def preserve_corrupt_file(path: str | Path) -> Path | None:
    """Copy an unreadable file to a content-addressed recovery location."""

    source = Path(path)
    if not source.is_file():
        return None
    try:
        digest = _sha256(source)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        recovery_dir = source.parent / "recovery"
        recovery_dir.mkdir(parents=True, exist_ok=True)
        existing = next(recovery_dir.glob(f"{source.name}.corrupt-*-{digest[:12]}.bak"), None)
        if existing is not None:
            return existing
        destination = recovery_dir / f"{source.name}.corrupt-{timestamp}-{digest[:12]}.bak"
        _atomic_copy(source, destination)
        return destination if _sha256(destination) == digest else None
    except OSError:
        return None


def atomic_write_json(
    path: str | Path,
    document: Mapping[str, Any],
    *,
    indent: int = 2,
    allow_nan: bool = True,
) -> None:
    """Write JSON durably and preserve one verified last-known-good copy."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file():
        # The caller has already parsed the current document successfully.
        # Preserve it before replacement so a later disk fault is recoverable.
        backup = target.parent / "backups" / f"{target.name}.last-good.bak"
        _atomic_copy(target, backup)

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(
                dict(document),
                temporary,
                ensure_ascii=False,
                indent=indent,
                allow_nan=allow_nan,
            )
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        # Parse before replace to prevent a serialization defect becoming live.
        json.loads(temporary_path.read_text(encoding="utf-8"))
        os.replace(temporary_path, target)
    finally:
        if temporary_path is not None and temporary_path.exists():
            try:
                temporary_path.unlink()
            except OSError:
                pass


def _atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            with source.open("rb") as source_handle:
                shutil.copyfileobj(source_handle, temporary, length=1024 * 1024)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, destination)
    finally:
        if temporary_path is not None and temporary_path.exists():
            try:
                temporary_path.unlink()
            except OSError:
                pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _lock_handle(handle: Any) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_handle(handle: Any) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
