"""Synthetic-only regression coverage for administrator-token reloads.

No native credential backend, runtime database, listening server, external
network request, clipboard, or desktop window is used by these tests.
"""

from __future__ import annotations

from contextlib import ExitStack
import base64
import hashlib
import importlib
import importlib.util
import os
from pathlib import Path
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from district_management_server.credentials import (
    AUTHORIZATION_PRIVATE_KEY_TARGET,
    CLOUDFLARE_API_TOKEN_TARGET,
    DISTRICT_ADMIN_DIGEST_TARGET,
    HANDOFF_ENCRYPTION_KEY_TARGET,
    MANAGEMENT_TUNNEL_TOKEN_TARGET,
    DistrictCredentialStore,
)
from district_management_server.paths import DistrictRuntimePaths
from district_management_server.server_controller import DistrictServerController
from registration_service.district_auth import SESSION_COOKIE


SERVICE_DEPS_AVAILABLE = all(
    importlib.util.find_spec(name) is not None
    for name in ("fastapi", "webauthn", "httpx")
)


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class _MemoryCredentials:
    def __init__(self, token: str) -> None:
        self.values = {
            AUTHORIZATION_PRIVATE_KEY_TARGET: base64.b64encode(b"a" * 32).decode("ascii"),
            HANDOFF_ENCRYPTION_KEY_TARGET: base64.b64encode(b"b" * 32).decode("ascii"),
            DISTRICT_ADMIN_DIGEST_TARGET: _digest(token),
            CLOUDFLARE_API_TOKEN_TARGET: "synthetic-cloudflare-api-token",
            MANAGEMENT_TUNNEL_TOKEN_TARGET: "synthetic-management-tunnel-token",
        }

    def read(self, target: str):
        return self.values.get(target)

    def write(self, target: str, value: str) -> None:
        self.values[target] = value

    def delete(self, target: str) -> bool:
        return self.values.pop(target, None) is not None


class _FakeServer:
    def __init__(self) -> None:
        self.should_exit = False
        self.force_exit = False
        self.started = Event()

    def run(self) -> None:
        self.started.set()
        while not self.should_exit and not self.force_exit:
            Event().wait(0.001)


class _FakeTunnel:
    def start(self, _token: str) -> None:
        pass

    def stop(self) -> None:
        pass

    def snapshot(self):
        return SimpleNamespace(state="stopped", detail="Synthetic tunnel.")


@unittest.skipUnless(
    SERVICE_DEPS_AVAILABLE,
    "The independently deployed registration-service test dependencies are not installed.",
)
class DistrictTokenRestartTests(unittest.TestCase):
    OLD_TOKEN = "synthetic-original-district-administrator-token"
    NEW_TOKEN = "synthetic-replacement-district-administrator-token"

    def setUp(self) -> None:
        self.resources = ExitStack()
        self.addCleanup(self.resources.close)
        self.resources.enter_context(
            patch.dict(
                os.environ,
                {"SCHOOL_CSM_DISTRICT_ADMIN_TOKEN_SHA256": _digest(self.OLD_TOKEN)},
                clear=False,
            )
        )
        # The real application factory is safe to import: service/database
        # construction is lazy. Explicit guards keep these tests auth-only.
        module = importlib.import_module("registration_service.app")
        self.resources.enter_context(
            patch.object(
                module,
                "build_service",
                side_effect=AssertionError("Token tests must not access a runtime database."),
            )
        )
        self.cached_app = module.create_app()
        self.cached_module = SimpleNamespace(
            app=self.cached_app,
            create_app=module.create_app,
        )
        self.resources.enter_context(
            patch(
                "district_management_server.server_controller.importlib",
                SimpleNamespace(import_module=Mock(return_value=self.cached_module)),
            )
        )

    def test_loader_builds_fresh_auth_state_even_when_module_is_cached(self) -> None:
        original = DistrictServerController._load_application()
        original_session = original.state.district_admin_sessions.login(self.OLD_TOKEN)
        self.assertIsNotNone(original_session)

        os.environ["SCHOOL_CSM_DISTRICT_ADMIN_TOKEN_SHA256"] = _digest(self.NEW_TOKEN)
        replacement = DistrictServerController._load_application()

        self.assertIsNot(original, self.cached_app)
        self.assertIsNot(replacement, original)
        sessions = replacement.state.district_admin_sessions
        self.assertIsNotNone(sessions.login(self.NEW_TOKEN))
        self.assertIsNone(sessions.login(self.OLD_TOKEN))
        self.assertIsNone(sessions.get(original_session.session_id))

    def test_stop_rotate_start_accepts_new_token_and_rejects_old_token_and_cookie(self) -> None:
        from fastapi.testclient import TestClient

        isolated_root = Path(__file__).resolve().parent / (
            f"unused-district-token-state-{uuid4().hex}"
        )
        paths = DistrictRuntimePaths(isolated_root)
        store = DistrictCredentialStore(_MemoryCredentials(self.OLD_TOKEN))
        config = SimpleNamespace(
            listen_port=8090,
            local_health_url="http://127.0.0.1:8090/healthz",
            portal_url="https://testserver/district",
        )
        applications = []
        servers = []

        def server_factory(app, _config):
            applications.append(app)
            server = _FakeServer()
            servers.append(server)
            return server

        def prepare_environment(_config, _paths, _root, *, credential_store):
            return {
                "SCHOOL_CSM_DISTRICT_ADMIN_TOKEN_SHA256": (
                    credential_store.load().district_admin_token_sha256
                ),
            }

        controller = DistrictServerController(
            paths,
            isolated_root / "app",
            credential_store=store,
            tunnel=_FakeTunnel(),
            uvicorn_factory=server_factory,
            health_probe=lambda _url: servers[-1].started.wait(timeout=0.1),
        )
        self.addCleanup(controller.stop)
        with (
            patch(
                "district_management_server.server_controller.load_server_config",
                return_value=config,
            ),
            patch(
                "district_management_server.server_controller.prepare_service_environment",
                side_effect=prepare_environment,
            ),
        ):
            self.assertEqual(controller.start().state, "running")
            with TestClient(applications[-1], base_url="https://testserver") as client:
                login = client.post("/v1/admin/session", json={"access_token": self.OLD_TOKEN})
                self.assertEqual(login.status_code, 200, login.text)
                old_cookie = login.cookies.get(SESSION_COOKIE)
                self.assertIsNotNone(old_cookie)
                self.assertEqual(client.get("/v1/admin/session").status_code, 200)

            self.assertEqual(controller.stop().state, "stopped")
            with patch(
                "district_management_server.credentials.secrets.token_urlsafe",
                return_value=self.NEW_TOKEN,
            ):
                self.assertEqual(store.rotate_administrator_token(), self.NEW_TOKEN)
            self.assertEqual(controller.start().state, "running")

            with TestClient(applications[-1], base_url="https://testserver") as client:
                old_session = client.get(
                    "/v1/admin/session",
                    headers={"Cookie": f"{SESSION_COOKIE}={old_cookie}"},
                )
                self.assertEqual(old_session.status_code, 401, old_session.text)
                old_login = client.post(
                    "/v1/admin/session", json={"access_token": self.OLD_TOKEN}
                )
                self.assertEqual(old_login.status_code, 401, old_login.text)
                new_login = client.post(
                    "/v1/admin/session", json={"access_token": self.NEW_TOKEN}
                )
                self.assertEqual(new_login.status_code, 200, new_login.text)
                self.assertEqual(client.get("/v1/admin/session").status_code, 200)

            self.assertIsNot(applications[0], applications[1])
            self.assertEqual(controller.stop().state, "stopped")
        self.assertFalse(isolated_root.exists())


if __name__ == "__main__":
    unittest.main()
