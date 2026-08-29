"""Lifecycle wrapper for the optional, pinned Cloudflare Tunnel component."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import subprocess
from threading import RLock, Thread
from typing import Any, Callable

from school_csm_control_center.windows_admin import hidden_process_creation_flags


CLOUDFLARED_VERSION = "2026.5.2"
CLOUDFLARED_WINDOWS_AMD64_SHA256 = (
    "20b9638f685333d623798e733effbad2487093f15ba592f6c7752360ff3b7ab7"
)
CLOUDFLARED_RELATIVE_PATH = Path("vendor") / "cloudflared" / "cloudflared.exe"


class TunnelComponentError(RuntimeError):
    pass


@dataclass(frozen=True)
class TunnelSnapshot:
    state: str
    detail: str
    pid: int | None = None
    version: str = CLOUDFLARED_VERSION


class CloudflaredTunnel:
    """Run a remotely managed named tunnel without exposing its token in argv."""

    def __init__(
        self,
        install_root: str | Path,
        *,
        executable: str | Path | None = None,
        process_factory: Callable[..., Any] | None = None,
        status_callback: Callable[[TunnelSnapshot], None] | None = None,
    ) -> None:
        self.install_root = Path(install_root).expanduser().resolve()
        self.executable = Path(executable) if executable is not None else (
            self.install_root / CLOUDFLARED_RELATIVE_PATH
        )
        self._process_factory = process_factory or subprocess.Popen
        self._status_callback = status_callback
        self._lock = RLock()
        self._process: Any | None = None
        self._snapshot = TunnelSnapshot("stopped", "Internet Gateway is stopped.")

    def snapshot(self) -> TunnelSnapshot:
        with self._lock:
            process = self._process
            snapshot = self._snapshot
        if process is not None and process.poll() is not None and snapshot.state in {"starting", "connected"}:
            self._set_snapshot("error", "The Internet Gateway tunnel process stopped unexpectedly.")
        with self._lock:
            return self._snapshot

    def verify_component(self) -> Path:
        executable = self.executable.expanduser().resolve()
        if not executable.is_file():
            raise TunnelComponentError(
                "The Internet Gateway tunnel component is not installed. Run Setup and choose Repair."
            )
        digest = hashlib.sha256()
        try:
            with executable.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
        except OSError as exc:
            raise TunnelComponentError("The Internet Gateway tunnel component cannot be read.") from exc
        if digest.hexdigest().casefold() != CLOUDFLARED_WINDOWS_AMD64_SHA256:
            raise TunnelComponentError(
                "The Internet Gateway tunnel component failed its integrity check. Run Setup and choose Repair."
            )
        return executable

    def start(self, tunnel_token: str) -> TunnelSnapshot:
        token = str(tunnel_token or "").strip()
        if not token or any(character in token for character in "\r\n\0"):
            raise TunnelComponentError("The saved Internet Gateway credential is unavailable or invalid.")
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                return self._snapshot
        executable = self.verify_component()
        self._set_snapshot("starting", "Connecting the secure outbound Internet Gateway tunnel…")
        environment = dict(os.environ)
        environment["TUNNEL_TOKEN"] = token
        arguments = [
            str(executable),
            "tunnel",
            "--no-autoupdate",
            "--loglevel",
            "info",
            "run",
        ]
        startupinfo = None
        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE
        try:
            process = self._process_factory(
                arguments,
                cwd=str(executable.parent),
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                startupinfo=startupinfo,
                creationflags=hidden_process_creation_flags(),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            self._set_snapshot("error", "The Internet Gateway tunnel could not be started.")
            raise TunnelComponentError("The Internet Gateway tunnel could not be started.") from exc
        finally:
            # Drop the only local copy added to the child environment as soon as
            # CreateProcess returns.  The token is never put in command-line or logs.
            environment.pop("TUNNEL_TOKEN", None)
        with self._lock:
            self._process = process
        self._set_snapshot("connected", "The outbound Internet Gateway tunnel is running.", process.pid)
        Thread(target=self._monitor, args=(process,), name="SchoolCSMGatewayTunnel", daemon=True).start()
        return self.snapshot()

    def stop(self) -> TunnelSnapshot:
        with self._lock:
            process = self._process
            self._process = None
        if process is not None and process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=8)
            except Exception:
                try:
                    process.kill()
                    process.wait(timeout=3)
                except Exception:
                    pass
        self._set_snapshot("stopped", "Internet Gateway is stopped.")
        return self.snapshot()

    def _monitor(self, process: Any) -> None:
        # Consume output to avoid a filled pipe.  Output is intentionally not
        # persisted: upstream diagnostics can contain network identifiers.
        stream = getattr(process, "stdout", None)
        if stream is not None:
            try:
                for _line in iter(stream.readline, ""):
                    if process.poll() is not None:
                        break
            except Exception:
                pass
        try:
            return_code = process.wait()
        except Exception:
            return
        with self._lock:
            current = self._process
            if current is process:
                self._process = None
        if current is process:
            self._set_snapshot(
                "error" if int(return_code or 0) else "stopped",
                "The Internet Gateway tunnel stopped unexpectedly."
                if int(return_code or 0)
                else "Internet Gateway is stopped.",
            )

    def _set_snapshot(self, state: str, detail: str, pid: int | None = None) -> None:
        snapshot = TunnelSnapshot(str(state), str(detail), pid)
        with self._lock:
            self._snapshot = snapshot
        if self._status_callback is not None:
            try:
                self._status_callback(snapshot)
            except Exception:
                pass
