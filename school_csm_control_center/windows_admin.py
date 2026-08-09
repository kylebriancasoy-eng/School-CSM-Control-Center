"""Windows elevation and helper-script support for School CSM Control Center."""

from __future__ import annotations

from dataclasses import dataclass
import base64
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
    """Configure the required local-subnet rules from compiled application code.

    The local web server must not depend on this result. The script is a best-
    effort network-access step; failure must leave laptop-only server access
    available for diagnosis and repair. No source-tree ``.cmd`` file is needed,
    so the same operation works from the signed, compiled end-user release.
    """
    if not is_windows():
        return CommandResult(True, "Not required", "Windows Firewall setup is not required on this operating system.", 0)

    del project_root  # Retained for compatibility with existing callers.
    try:
        preferred = _validated_port(preferred_port)
        fallback = _validated_port(fallback_port)
    except (TypeError, ValueError) as exc:
        return CommandResult(False, "Invalid", str(exc), 2)

    inner_script = _firewall_powershell(preferred, fallback, bool(captive_portal))
    inner_encoded = _encoded_powershell(inner_script)
    if is_administrator():
        command = [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-WindowStyle",
            "Hidden",
            "-EncodedCommand",
            inner_encoded,
        ]
    else:
        # The outer process exists only to show the standard UAC consent prompt,
        # wait for the elevated child, and return its verified exit status.
        outer_script = (
            "$ErrorActionPreference = 'Stop'\n"
            "$arguments = @('-NoProfile','-NonInteractive','-ExecutionPolicy',"
            f"'Bypass','-WindowStyle','Hidden','-EncodedCommand','{inner_encoded}')\n"
            "try {\n"
            "  $process = Start-Process -FilePath 'powershell.exe' "
            "-ArgumentList $arguments -Verb RunAs -WindowStyle Hidden -Wait -PassThru\n"
            "  exit $process.ExitCode\n"
            "} catch {\n"
            "  Write-Error 'Administrator authorization was declined or unavailable.'\n"
            "  exit 1223\n"
            "}\n"
        )
        command = [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-WindowStyle",
            "Hidden",
            "-EncodedCommand",
            _encoded_powershell(outer_script),
        ]
    creation_flags = hidden_process_creation_flags()
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
            creationflags=creation_flags,
            errors="replace",
        )
    except subprocess.TimeoutExpired:
        return CommandResult(False, "Timed out", "Windows Firewall setup did not finish within 90 seconds.", 124)
    except OSError as exc:
        return CommandResult(False, "Error", f"Windows Firewall setup could not be started: {exc}", 1)

    if completed.returncode == 0:
        return CommandResult(
            True,
            "Configured",
            "Windows Firewall access was limited to the active Private local subnet and required survey ports.",
            0,
        )

    output = "\n".join(part.strip() for part in (completed.stdout, completed.stderr) if part and part.strip())
    return CommandResult(False, "Failed", output or "Windows rejected the firewall configuration request.", completed.returncode)


def _validated_port(value: int) -> int:
    port = int(value)
    if not 1 <= port <= 65535:
        raise ValueError("A firewall port must be between 1 and 65535.")
    return port


def _encoded_powershell(script: str) -> str:
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def _firewall_powershell(
    preferred_port: int,
    fallback_port: int,
    captive_portal: bool,
) -> str:
    netsh = '& "$env:SystemRoot\\System32\\netsh.exe"'
    lines = ["$ErrorActionPreference = 'Stop'"]
    for protocol, port in (("TCP", 80), ("TCP", 8080), ("TCP", 53), ("UDP", 53)):
        name = f"School CSM Control Center {protocol} {port}"
        lines.append(
            f'{netsh} advfirewall firewall delete rule name="{name}" | Out-Null'
        )
    required_rules = [("TCP", port) for port in dict.fromkeys((preferred_port, fallback_port))]
    if captive_portal and 80 not in {port for _, port in required_rules}:
        required_rules.append(("TCP", 80))
    for protocol, port in required_rules:
        name = f"School CSM Control Center {protocol} {port}"
        lines.extend(
            [
                f'{netsh} advfirewall firewall delete rule name="{name}" | Out-Null',
                (
                    f'{netsh} advfirewall firewall add rule name="{name}" dir=in '
                    f'action=allow protocol={protocol} localport={port} '
                    "profile=private remoteip=localsubnet | Out-Null"
                ),
                "if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }",
            ]
        )
    if captive_portal:
        name = "School CSM Control Center UDP 53"
        lines.extend(
            [
                f'{netsh} advfirewall firewall delete rule name="{name}" | Out-Null',
                (
                    f'{netsh} advfirewall firewall add rule name="{name}" dir=in '
                    "action=allow protocol=UDP localport=53 profile=private "
                    "remoteip=localsubnet | Out-Null"
                ),
                # DNS interception is optional because Windows Internet
                # Connection Sharing may reserve UDP/53.
                "$null = $LASTEXITCODE",
            ]
        )
    lines.append("exit 0")
    return "\n".join(lines) + "\n"
