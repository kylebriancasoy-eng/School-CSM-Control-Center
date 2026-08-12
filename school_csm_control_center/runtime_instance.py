"""Single-instance protection for the desktop Control Center."""

from __future__ import annotations

from pathlib import Path
import sys

from school_csm_control_center.storage.file_safety import InterProcessFileLock


class AlreadyRunningError(RuntimeError):
    pass


def activate_existing_window(window_title: str) -> bool:
    """Restore the already-running Windows instance, including from the tray."""

    if sys.platform != "win32" or not str(window_title or "").strip():
        return False
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        find_window = user32.FindWindowW
        find_window.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
        find_window.restype = wintypes.HWND
        show_window = user32.ShowWindow
        show_window.argtypes = [wintypes.HWND, ctypes.c_int]
        show_window.restype = wintypes.BOOL
        set_foreground = user32.SetForegroundWindow
        set_foreground.argtypes = [wintypes.HWND]
        set_foreground.restype = wintypes.BOOL
        handle = find_window(None, str(window_title).strip())
        if not handle:
            return False
        # SW_RESTORE shows a hidden/minimized frameless window while retaining
        # its normal placement. SetForegroundWindow may be denied by Windows'
        # focus-stealing policy, but the restored window is still actionable.
        show_window(handle, 9)
        set_foreground(handle)
        return True
    except (AttributeError, OSError, TypeError, ValueError):
        return False


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
