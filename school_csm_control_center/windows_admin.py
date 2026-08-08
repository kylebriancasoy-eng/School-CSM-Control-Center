"""Windows elevation and helper-script support for School CSM Control Center."""

from __future__ import annotations

from dataclasses import dataclass
import ctypes
import os
from pathlib import Path
import subprocess
import sys
from typing import Sequence


@dataclass(frozen=True)
class CommandResult:
    ok: bool
    status: str
    detail: str
    returncode: int


def is_windows() -> bool:
    return os.name == "nt"




def hidden_process_creation_flags() -> int:
    """Return Windows flags that prevent helper commands from flashing a console."""
    if not is_windows():
        return 0
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))

def is_administrator() -> bool:
    """Return whether the current process has elevated Windows privileges."""
    if not is_windows():
        return True
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_as_administrator(script_path: str | Path, args: Sequence[str] = ()) -> bool:
    """Relaunch the current Python entry point through Windows UAC.

    Returns True when an elevated child process was launched. The caller should
    then exit the unelevated process. Returns False when elevation is not needed.
    """
    if not is_windows() or is_administrator():
        return False

    script = str(Path(script_path).resolve())
    parameters = subprocess.list2cmdline([script, *[str(value) for value in args]])
    working_directory = str(Path(script).parent)
    result = ctypes.windll.shell32.ShellExecuteW(
        None,
        "runas",
        sys.executable,
        parameters,
        working_directory,
        1,
    )
    if int(result) <= 32:
        raise PermissionError("Administrator authorization was declined or Windows could not elevate the application.")
    return True


def run_firewall_script(
    project_root: str | Path,
    preferred_port: int,
    fallback_port: int = 8080,
    *,
    captive_portal: bool = False,
) -> CommandResult:
    """Run the proven firewall helper script and return only its exit status.

    The local web server must not depend on this result. The script is a best-
    effort network-access step; failure must leave laptop-only server access
    available for diagnosis and repair.
    """
    if not is_windows():
        return CommandResult(True, "Not required", "Windows Firewall setup is not required on this operating system.", 0)

    script = Path(project_root) / "ALLOW_SCHOOL_CSM_FIREWALL.cmd"
    if not script.is_file():
        return CommandResult(False, "Missing", f"Firewall helper was not found: {script.name}", 2)

    creation_flags = hidden_process_creation_flags()
    try:
        completed = subprocess.run(
            [
                "cmd.exe",
                "/d",
                "/c",
                str(script),
                str(int(preferred_port)),
                str(int(fallback_port)),
                "1" if captive_portal else "0",
            ],
            cwd=str(Path(project_root)),
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
            creationflags=creation_flags,
            errors="replace",
        )
    except subprocess.TimeoutExpired:
        return CommandResult(False, "Timed out", "The firewall helper did not finish within 90 seconds.", 124)
    except OSError as exc:
        return CommandResult(False, "Error", f"The firewall helper could not be started: {exc}", 1)

    if completed.returncode == 0:
        return CommandResult(
            True,
            "Configured",
            "Windows Firewall access was limited to the active Private local subnet and required survey ports.",
            0,
        )

    output = "\n".join(part.strip() for part in (completed.stdout, completed.stderr) if part and part.strip())
    return CommandResult(False, "Failed", output or "Windows rejected the firewall configuration request.", completed.returncode)
