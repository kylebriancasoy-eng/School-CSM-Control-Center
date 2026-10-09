"""End-to-end frozen-runtime verification for district release builds."""

from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path
import socket
from tempfile import TemporaryDirectory
from threading import Thread
import time
from urllib.request import Request, urlopen


class RuntimeSelfTestError(RuntimeError):
    pass


def run_runtime_self_test(install_root: str | Path) -> None:
    """Start the bundled service and verify real HTTP/static responses.

    This mode uses disposable keys, a temporary SQLite database, and no
    Cloudflare network call. It exercises the imports that are intentionally
    loaded only after first-run setup in the normal tray application.
    """

    root = Path(install_root).resolve()
    worker_source = root / "cloudflare_worker" / "src" / "index.js"
    district_worker = root / "district_gateway_worker" / "src" / "index.js"
    if not worker_source.is_file() or not district_worker.is_file():
        raise RuntimeSelfTestError("A required bundled Worker asset is missing.")

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    private_key = Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    handoff_key = os.urandom(32)
    administrator_token = "district-runtime-self-test-token"

    with TemporaryDirectory(prefix="school-csm-district-self-test-") as temporary:
        temporary_root = Path(temporary)
        port = _available_loopback_port()
        values = {
            "SCHOOL_CSM_REGISTRATION_DATABASE": str(temporary_root / "registration.sqlite3"),
            "SCHOOL_CSM_MANAGED_DOMAIN": "self-test.invalid",
            "SCHOOL_CSM_WEBAUTHN_ORIGIN": "https://management.self-test.invalid",
            "SCHOOL_CSM_WEBAUTHN_RP_ID": "management.self-test.invalid",
            "SCHOOL_CSM_AUTHORIZATION_KEY_ID": "district-runtime-self-test",
            "SCHOOL_CSM_AUTHORIZATION_PRIVATE_KEY_BASE64": base64.b64encode(
                private_key
            ).decode("ascii"),
            "SCHOOL_CSM_HANDOFF_ENCRYPTION_KEY_BASE64": base64.b64encode(
                handoff_key
            ).decode("ascii"),
            "SCHOOL_CSM_DISTRICT_ADMIN_TOKEN_SHA256": hashlib.sha256(
                administrator_token.encode("utf-8")
            ).hexdigest(),
            "SCHOOL_CSM_CLOUDFLARE_MODE": "workers_vpc",
            "CLOUDFLARE_ACCOUNT_ID": "0" * 32,
            "CLOUDFLARE_API_TOKEN": "runtime-self-test-cloudflare-token",
            "SCHOOL_CSM_TUNNEL_ORIGIN": "http://127.0.0.1:8080",
            "SCHOOL_CSM_WORKER_SOURCE": str(worker_source),
            "SCHOOL_CSM_WORKER_COMPATIBILITY_DATE": "2026-09-30",
        }
        previous = {name: os.environ.get(name) for name in values}
        os.environ.update(values)
        server = None
        thread = None
        try:
            from registration_service.app import build_service, create_app
            import uvicorn

            service = build_service()
            application = create_app(
                service,
                district_admin_token_sha256=values[
                    "SCHOOL_CSM_DISTRICT_ADMIN_TOKEN_SHA256"
                ],
            )
            configuration = uvicorn.Config(
                application,
                host="127.0.0.1",
                port=port,
                access_log=False,
                log_config=None,
                server_header=False,
                proxy_headers=True,
                forwarded_allow_ips="127.0.0.1",
                workers=1,
            )
            server = uvicorn.Server(configuration)
            thread = Thread(target=server.run, name="DistrictRuntimeSelfTest", daemon=True)
            thread.start()
            _wait_for_response(port, "/healthz", expected_marker=b'"ok":true')
            _wait_for_response(port, "/district", expected_marker=b"District")
            _wait_for_response(
                port, "/assets/district.js", expected_marker=b"district"
            )
            if not (temporary_root / "registration.sqlite3").is_file():
                raise RuntimeSelfTestError("The bundled registration database could not start.")
        finally:
            if server is not None:
                server.should_exit = True
            if thread is not None:
                thread.join(timeout=10)
                if thread.is_alive() and server is not None:
                    server.force_exit = True
                    thread.join(timeout=3)
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value


def _available_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_for_response(port: int, path: str, *, expected_marker: bytes) -> None:
    deadline = time.monotonic() + 20
    url = f"http://127.0.0.1:{port}{path}"
    last_error = "no response"
    while time.monotonic() < deadline:
        try:
            request = Request(
                url,
                headers={
                    "User-Agent": "School-CSM-District-Runtime-Self-Test/1",
                    "X-Forwarded-Proto": "https",
                    "X-Forwarded-Host": "management.self-test.invalid",
                },
            )
            with urlopen(request, timeout=1.5) as response:
                body = response.read(1024 * 1024 + 1)
                if int(response.status) != 200:
                    last_error = f"HTTP {response.status}"
                elif len(body) > 1024 * 1024:
                    raise RuntimeSelfTestError("A self-test response exceeded the size limit.")
                elif expected_marker.lower() not in body.lower():
                    raise RuntimeSelfTestError(
                        f"The bundled response for {path} did not match its contract."
                    )
                else:
                    return
        except RuntimeSelfTestError:
            raise
        except Exception as exc:
            detail = str(exc).strip()
            last_error = f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__
        time.sleep(0.1)
    raise RuntimeSelfTestError(
        f"The bundled service did not answer {path}: {last_error}."
    )
