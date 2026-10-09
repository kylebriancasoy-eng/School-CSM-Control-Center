"""Per-user sign-in startup for the compiled district host."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
from typing import Any


RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE_NAME = "MoSSLab.SchoolCSMDistrictManagementServer"
STARTUP_ARGUMENTS = ("--background",)


class StartupRegistrationError(RuntimeError):
    pass


def compiled_startup_command(*, executable: str | Path | None = None) -> str:
    if executable is None:
        if not bool(getattr(sys, "frozen", False)):
            raise StartupRegistrationError(
                "Start with Windows is available only in the compiled district application."
            )
        target = Path(sys.executable)
    else:
        target = Path(executable)
    if target.suffix.casefold() != ".exe":
        raise StartupRegistrationError("The district startup target must be an EXE.")
    return subprocess.list2cmdline([str(target), *STARTUP_ARGUMENTS])


def set_enabled(
    enabled: bool,
    *,
    registry: Any | None = None,
    executable: str | Path | None = None,
) -> bool:
    api = _registry(registry)
    if enabled:
        command = compiled_startup_command(executable=executable)
        try:
            with api.CreateKeyEx(
                api.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, api.KEY_SET_VALUE
            ) as key:
                api.SetValueEx(key, RUN_VALUE_NAME, 0, api.REG_SZ, command)
        except OSError as exc:
            raise StartupRegistrationError(
                "Windows could not enable district background startup."
            ) from exc
        return True
    try:
        with api.OpenKey(
            api.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, api.KEY_SET_VALUE
        ) as key:
            try:
                api.DeleteValue(key, RUN_VALUE_NAME)
            except FileNotFoundError:
                pass
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise StartupRegistrationError(
            "Windows could not disable district background startup."
        ) from exc
    return False


def is_enabled(
    *,
    registry: Any | None = None,
    executable: str | Path | None = None,
) -> bool:
    try:
        expected = compiled_startup_command(executable=executable)
        api = _registry(registry)
        with api.OpenKey(api.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, api.KEY_READ) as key:
            value, kind = api.QueryValueEx(key, RUN_VALUE_NAME)
        return kind == api.REG_SZ and os.path.normcase(str(value)) == os.path.normcase(expected)
    except (FileNotFoundError, OSError, StartupRegistrationError):
        return False


def _registry(registry: Any | None) -> Any:
    if registry is not None:
        return registry
    if os.name != "nt":
        raise StartupRegistrationError("Windows startup registration is unavailable.")
    import winreg

    return winreg
