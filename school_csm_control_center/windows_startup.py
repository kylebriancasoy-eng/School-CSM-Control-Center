"""Per-user Windows startup registration for the compiled Control Center."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
from typing import Any


RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE_NAME = "MoSSLab.SchoolCSMControlCenter"
STARTUP_ARGUMENTS = ("--background", "--auto-start-server")


class StartupRegistrationError(RuntimeError):
    """Raised when the per-user Windows startup entry cannot be changed."""


class WindowsStartupRegistration:
    """Small injectable facade used by the desktop/tray lifecycle."""

    def __init__(
        self,
        project_root: str | Path | None = None,
        *,
        registry: Any | None = None,
        executable: str | Path | None = None,
    ) -> None:
        # Retain the install root for diagnostics and API compatibility. The
        # authoritative compiled target remains sys.executable so updates at
        # the stable install path cannot leave a stale launcher command.
        self.project_root = Path(project_root) if project_root is not None else None
        self.registry = registry
        self.executable = executable

    def enable(self) -> bool:
        return enable(registry=self.registry, executable=self.executable)

    def disable(self) -> bool:
        return disable(registry=self.registry)

    def is_enabled(self) -> bool:
        return is_enabled(registry=self.registry, executable=self.executable)

    def set_enabled(self, enabled: bool) -> bool:
        return set_enabled(
            bool(enabled),
            registry=self.registry,
            executable=self.executable,
        )


def compiled_startup_command(*, executable: str | Path | None = None) -> str:
    """Return the exact background-start command for the compiled executable.

    ``executable`` is injectable for focused tests and installer diagnostics.
    Ordinary source-tree runs are intentionally refused so an end-user startup
    entry can never depend on a separately installed Python runtime or source
    checkout.
    """

    if executable is None:
        if not bool(getattr(sys, "frozen", False)):
            raise StartupRegistrationError(
                "Start with Windows is available only in the installed, compiled application."
            )
        executable_path = Path(sys.executable)
    else:
        executable_path = Path(executable)
    if executable_path.suffix.casefold() != ".exe":
        raise StartupRegistrationError(
            "The Windows startup target must be the compiled School CSM Control Center executable."
        )
    return subprocess.list2cmdline([str(executable_path), *STARTUP_ARGUMENTS])


def enable(
    *,
    registry: Any | None = None,
    executable: str | Path | None = None,
) -> bool:
    """Create or replace the current user's exact startup command."""

    command = compiled_startup_command(executable=executable)
    api = _registry_api(registry)
    try:
        with api.CreateKeyEx(
            api.HKEY_CURRENT_USER,
            RUN_KEY_PATH,
            0,
            api.KEY_SET_VALUE,
        ) as key:
            api.SetValueEx(key, RUN_VALUE_NAME, 0, api.REG_SZ, command)
    except OSError as exc:
        raise StartupRegistrationError(
            f"Windows could not enable background startup: {exc}"
        ) from exc
    return True


def disable(*, registry: Any | None = None) -> bool:
    """Remove the current user's startup command; already absent is success."""

    api = _registry_api(registry)
    try:
        with api.OpenKey(
            api.HKEY_CURRENT_USER,
            RUN_KEY_PATH,
            0,
            api.KEY_SET_VALUE,
        ) as key:
            try:
                api.DeleteValue(key, RUN_VALUE_NAME)
            except FileNotFoundError:
                pass
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise StartupRegistrationError(
            f"Windows could not disable background startup: {exc}"
        ) from exc
    return False


def is_enabled(
    *,
    registry: Any | None = None,
    executable: str | Path | None = None,
) -> bool:
    """Return whether the exact compiled background-start command is present."""

    try:
        expected = compiled_startup_command(executable=executable)
    except StartupRegistrationError:
        return False
    api = _registry_api(registry)
    try:
        with api.OpenKey(
            api.HKEY_CURRENT_USER,
            RUN_KEY_PATH,
            0,
            api.KEY_READ,
        ) as key:
            value, value_type = api.QueryValueEx(key, RUN_VALUE_NAME)
    except (FileNotFoundError, OSError):
        return False
    return value_type == api.REG_SZ and os.path.normcase(str(value).strip()) == os.path.normcase(
        expected
    )


def set_enabled(
    enabled: bool,
    *,
    registry: Any | None = None,
    executable: str | Path | None = None,
) -> bool:
    """Idempotently enable or disable startup and return the resulting state."""

    if bool(enabled):
        return enable(registry=registry, executable=executable)
    return disable(registry=registry)


def _registry_api(registry: Any | None) -> Any:
    if registry is not None:
        return registry
    if os.name != "nt":
        raise StartupRegistrationError(
            "Start with Windows is available only on Windows."
        )
    try:
        import winreg
    except ImportError as exc:  # pragma: no cover - defensive Windows boundary
        raise StartupRegistrationError(
            "Windows startup registration is unavailable in this runtime."
        ) from exc
    return winreg
