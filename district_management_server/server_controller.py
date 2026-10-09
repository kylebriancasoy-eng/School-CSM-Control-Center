"""Loopback registration-service and outbound-tunnel lifecycle controller."""

from __future__ import annotations

from dataclasses import dataclass
import importlib
from pathlib import Path
from threading import RLock, Thread
import time
from typing import Any, Callable
from urllib.request import Request, urlopen

from .configuration import (
    DistrictServerConfig,
    install_service_environment,
    load_server_config,
    prepare_service_environment,
)
from .credentials import DistrictCredentialStore
from .paths import DistrictRuntimePaths
from .tunnel import CloudflaredTunnel


@dataclass(frozen=True)
class DistrictServerSnapshot:
    state: str
    detail: str
    portal_url: str = ""
    local_port: int | None = None


class DistrictServerController:
    """Start Uvicorn on loopback, verify health, then start cloudflared."""

    def __init__(
        self,
        paths: DistrictRuntimePaths,
        install_root: str | Path,
        *,
        credential_store: DistrictCredentialStore | None = None,
        tunnel: CloudflaredTunnel | None = None,
        status_callback: Callable[[DistrictServerSnapshot], None] | None = None,
        app_loader: Callable[[], Any] | None = None,
        uvicorn_factory: Callable[[Any, DistrictServerConfig], Any] | None = None,
        health_probe: Callable[[str], bool] | None = None,
    ) -> None:
        self.paths = paths
        self.install_root = Path(install_root).resolve()
        self.credential_store = credential_store or DistrictCredentialStore()
        self.tunnel = tunnel or CloudflaredTunnel(self.install_root)
        self._status_callback = status_callback
        self._app_loader = app_loader or self._load_application
        self._uvicorn_factory = uvicorn_factory or self._create_uvicorn_server
        self._health_probe = health_probe or self._probe_health
        self._lock = RLock()
        self._server: Any | None = None
        self._thread: Thread | None = None
        self._config: DistrictServerConfig | None = None
        self._snapshot = DistrictServerSnapshot("stopped", "District server is stopped.")

    def snapshot(self) -> DistrictServerSnapshot:
        with self._lock:
            server = self._server
            thread = self._thread
            snapshot = self._snapshot
        if server is not None and thread is not None and not thread.is_alive():
            self._set_snapshot("error", "The local district service stopped unexpectedly.")
        tunnel_snapshot = self.tunnel.snapshot()
        if snapshot.state == "running" and tunnel_snapshot.state == "error":
            self._set_snapshot("error", tunnel_snapshot.detail)
        with self._lock:
            return self._snapshot

    def start(self) -> DistrictServerSnapshot:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return self._snapshot
        self._set_snapshot("starting", "Preparing the district registration service…")
        config = load_server_config(self.paths.configuration_file)
        values = prepare_service_environment(
            config,
            self.paths,
            self.install_root,
            credential_store=self.credential_store,
        )
        install_service_environment(values)
        secrets = self.credential_store.load()
        app = self._app_loader()
        server = self._uvicorn_factory(app, config)
        thread = Thread(
            target=server.run,
            name="SchoolCSMDistrictRegistrationService",
            daemon=True,
        )
        with self._lock:
            self._server = server
            self._thread = thread
            self._config = config
        thread.start()
        try:
            deadline = time.monotonic() + 20.0
            while time.monotonic() < deadline:
                if not thread.is_alive():
                    raise RuntimeError("The local district service stopped during startup.")
                if self._health_probe(config.local_health_url):
                    break
                time.sleep(0.15)
            else:
                raise RuntimeError("The local district service did not become healthy.")
            self.tunnel.start(secrets.management_tunnel_token)
        except Exception:
            self._stop_server_only(server, thread)
            with self._lock:
                self._server = None
                self._thread = None
                self._config = None
            self._set_snapshot("error", "District server startup did not complete.")
            raise
        self._set_snapshot(
            "running",
            "District portal and automatic school provisioning are available.",
            config=config,
        )
        return self.snapshot()

    def stop(self) -> DistrictServerSnapshot:
        self.tunnel.stop()
        with self._lock:
            server = self._server
            thread = self._thread
            self._server = None
            self._thread = None
            self._config = None
        if server is not None and thread is not None:
            self._stop_server_only(server, thread)
        self._set_snapshot("stopped", "District server is stopped.")
        return self.snapshot()

    def restart(self) -> DistrictServerSnapshot:
        self.stop()
        return self.start()

    def _set_snapshot(
        self,
        state: str,
        detail: str,
        *,
        config: DistrictServerConfig | None = None,
    ) -> None:
        effective = config
        if effective is None:
            with self._lock:
                effective = self._config
        snapshot = DistrictServerSnapshot(
            str(state),
            str(detail),
            effective.portal_url if effective else "",
            effective.listen_port if effective else None,
        )
        with self._lock:
            self._snapshot = snapshot
        if self._status_callback is not None:
            try:
                self._status_callback(snapshot)
            except Exception:
                pass

    @staticmethod
    def _load_application() -> Any:
        # Environment is installed before this first import. The module-level
        # application therefore receives the protected production settings.
        module = importlib.import_module("registration_service.app")
        return module.app

    @staticmethod
    def _create_uvicorn_server(app: Any, config: DistrictServerConfig) -> Any:
        import uvicorn

        settings = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=config.listen_port,
            access_log=False,
            log_config=None,
            server_header=False,
            proxy_headers=True,
            forwarded_allow_ips="127.0.0.1",
            workers=1,
        )
        return uvicorn.Server(settings)

    @staticmethod
    def _probe_health(url: str) -> bool:
        try:
            # The public service intentionally rejects requests that are not
            # HTTPS.  Uvicorn trusts forwarded headers only from loopback, so
            # the host's private readiness probe must model the Cloudflare
            # connection instead of being rejected by the HTTPS boundary.
            request = Request(
                url,
                headers={
                    "User-Agent": "School-CSM-District-Host/1",
                    "X-Forwarded-Proto": "https",
                },
            )
            with urlopen(request, timeout=1.0) as response:
                return int(response.status) == 200
        except Exception:
            return False

    @staticmethod
    def _stop_server_only(server: Any, thread: Thread) -> None:
        try:
            server.should_exit = True
        except Exception:
            pass
        thread.join(timeout=10)
        if thread.is_alive():
            try:
                server.force_exit = True
            except Exception:
                pass
            thread.join(timeout=3)
