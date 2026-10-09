from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest.mock import patch

from district_management_server.cloudflare_setup import (
    DistrictGatewayBootstrapper,
    ManagementGatewayResult,
)
from district_management_server.configuration import (
    ConfigurationError,
    load_server_config,
    prepare_service_environment,
    write_server_config,
)
from district_management_server.credentials import (
    ALL_TARGETS,
    DISTRICT_ADMIN_DIGEST_TARGET,
    DistrictCredentialStore,
    DistrictSecretMaterial,
)
from district_management_server.paths import resolve_runtime_paths
from district_management_server.recovery import (
    DistrictRecoveryError,
    create_recovery_backup,
    restore_recovery_backup,
)
from district_management_server.runtime_self_test import run_runtime_self_test
from district_management_server.server_controller import DistrictServerController
from district_management_server.setup import configure_district_server
from district_management_server.tunnel import (
    CLOUDFLARED_VERSION as DISTRICT_CLOUDFLARED_VERSION,
    CloudflaredTunnel,
)
from district_management_server.windows_startup import compiled_startup_command
from school_csm_control_center.internet_gateway.tunnel import CLOUDFLARED_VERSION
from school_csm_control_center.internet_gateway.config import (
    parse_gateway_provider_config,
)
from registration_service.core import RegistrationRepository


class _MemoryCredentials:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def read(self, target: str):
        return self.values.get(target)

    def write(self, target: str, secret: str) -> None:
        self.values[target] = secret

    def delete(self, target: str) -> bool:
        return self.values.pop(target, None) is not None


class _FakeBootstrapper:
    def __init__(self) -> None:
        self.revoked = False
        self.result = ManagementGatewayResult(
            tunnel_id="00000000-0000-0000-0000-000000000001",
            vpc_service_id="10000000-0000-0000-0000-000000000001",
            worker_name="management",
            public_host="management.motiong-district-csm-survey.workers.dev",
            tunnel_token="management-connector-token-value",
        )

    def provision(self, *, public_host: str, listen_port: int):
        if public_host != self.result.public_host or listen_port != 8090:
            raise AssertionError("unexpected bootstrap request")
        return self.result

    def revoke(self, result) -> None:
        self.revoked = result == self.result


def _document() -> dict:
    return {
        "schema_version": "1.0",
        "listen_port": 8090,
        "public_origin": "https://management.motiong-district-csm-survey.workers.dev",
        "managed_domain": "motiong-district-csm-survey.workers.dev",
        "webauthn_rp_id": "management.motiong-district-csm-survey.workers.dev",
        "authorization_key_id": "motiong-district-2026",
        "authorization_public_key_base64": base64.b64encode(b"p" * 32).decode("ascii"),
        "provisioning_mode": "workers_vpc",
        "cloudflare_account_id": "a" * 32,
        "cloudflare_zone_id": "",
        "tunnel_origin": "http://127.0.0.1:8080",
        "worker_source_path": "cloudflare_worker/src/index.js",
        "worker_compatibility_date": "2026-09-30",
        "management_tunnel_id": "00000000-0000-0000-0000-000000000001",
        "management_vpc_service_id": "10000000-0000-0000-0000-000000000001",
        "management_worker_name": "management",
    }


class DistrictConfigurationTests(unittest.TestCase):
    def test_district_and_school_hosts_pin_the_same_connector_version(self) -> None:
        self.assertEqual(DISTRICT_CLOUDFLARED_VERSION, CLOUDFLARED_VERSION)

    def test_program_data_layout_is_separate_from_school_data(self) -> None:
        paths = resolve_runtime_paths(environment={"PROGRAMDATA": r"C:\ProgramData"})
        self.assertIn("School CSM District Management Server", str(paths.root))
        self.assertNotIn("Control Center\\Data", str(paths.database_file))
        self.assertTrue(str(paths.provider_export_file).endswith("internet_gateway_provider.json"))

    def test_configuration_is_closed_and_contains_no_secret(self) -> None:
        with TemporaryDirectory() as temporary:
            target = Path(temporary) / "server.json"
            config = write_server_config(target, _document())
            self.assertEqual(config.listen_port, 8090)
            self.assertEqual(load_server_config(target).management_worker_name, "management")
            unsafe = _document()
            unsafe["cloudflare_api_token"] = "must-not-be-here"
            target.write_text(json.dumps(unsafe), encoding="utf-8")
            with self.assertRaisesRegex(ConfigurationError, "Secrets are not permitted"):
                load_server_config(target)

    def test_prepared_environment_uses_credential_store_and_bundled_worker(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = resolve_runtime_paths(root / "state")
            paths.ensure_directories()
            worker = root / "app" / "cloudflare_worker" / "src" / "index.js"
            worker.parent.mkdir(parents=True)
            worker.write_text("export default {};", encoding="utf-8")
            config = write_server_config(paths.configuration_file, _document())

            class Store:
                def load(self):
                    return DistrictSecretMaterial(
                        base64.b64encode(b"a" * 32).decode("ascii"),
                        base64.b64encode(b"b" * 32).decode("ascii"),
                        "c" * 64,
                        "cloudflare-api-token-value",
                        "management-tunnel-token-value",
                    )

            values = prepare_service_environment(
                config, paths, root / "app", credential_store=Store()
            )
            self.assertEqual(values["SCHOOL_CSM_CLOUDFLARE_MODE"], "workers_vpc")
            self.assertEqual(values["CLOUDFLARE_API_TOKEN"], "cloudflare-api-token-value")
            self.assertNotIn("management-tunnel-token-value", values.values())

    def test_tunnel_child_receives_only_token_and_safe_os_environment(self) -> None:
        captured = {}

        class Process:
            pid = 1729
            stdout = None

            def poll(self):
                return None

            def wait(self, timeout=None):
                return 0

            def terminate(self):
                pass

            def kill(self):
                pass

        def process_factory(_arguments, **kwargs):
            captured.update(kwargs)
            captured["env"] = dict(kwargs["env"])
            return Process()

        tunnel = CloudflaredTunnel(
            Path.cwd(),
            process_factory=process_factory,
        )
        executable = Path.cwd() / "cloudflared.exe"
        inherited = {
            "CLOUDFLARE_API_TOKEN": "must-not-reach-cloudflared",
            "SCHOOL_CSM_DISTRICT_DATA_ROOT": "must-not-reach-cloudflared",
            "SCHOOL_CSM_PRIVATE_SIGNING_KEY": "must-not-reach-cloudflared",
            "UNRELATED_SECRET": "must-not-reach-cloudflared",
            "SystemRoot": r"C:\Windows",
            "PATH": "/usr/bin",
            "TMPDIR": "/tmp",
        }
        with (
            patch.dict(os.environ, inherited, clear=True),
            patch.object(tunnel, "verify_component", return_value=executable),
        ):
            tunnel.start("district-tunnel-token")

        child_environment = captured["env"]
        self.assertEqual(child_environment["TUNNEL_TOKEN"], "district-tunnel-token")
        self.assertNotIn("CLOUDFLARE_API_TOKEN", child_environment)
        self.assertFalse(
            any(key.startswith("SCHOOL_CSM_") for key in child_environment)
        )
        self.assertNotIn("UNRELATED_SECRET", child_environment)
        if os.name == "nt":
            self.assertEqual(set(child_environment), {"SystemRoot", "TUNNEL_TOKEN"})
            self.assertEqual(child_environment["SystemRoot"], r"C:\Windows")
        else:
            self.assertEqual(
                set(child_environment), {"PATH", "TMPDIR", "TUNNEL_TOKEN"}
            )
            self.assertEqual(child_environment["PATH"], "/usr/bin")
            self.assertEqual(child_environment["TMPDIR"], "/tmp")


class DistrictCredentialTests(unittest.TestCase):
    def test_initial_provision_stores_only_admin_digest(self) -> None:
        backend = _MemoryCredentials()
        store = DistrictCredentialStore(backend)
        raw_token, public_key = store.provision_initial(
            cloudflare_api_token="cloudflare-api-token-value",
            management_tunnel_token="management-tunnel-token-value",
        )
        self.assertEqual(set(backend.values), set(ALL_TARGETS))
        self.assertNotIn(raw_token, backend.values.values())
        self.assertEqual(
            backend.values[DISTRICT_ADMIN_DIGEST_TARGET],
            hashlib.sha256(raw_token.encode("utf-8")).hexdigest(),
        )
        self.assertEqual(len(base64.b64decode(public_key)), 32)
        with self.assertRaisesRegex(Exception, "will not overwrite"):
            store.provision_initial(
                cloudflare_api_token="another-cloudflare-token",
                management_tunnel_token="another-management-token",
            )

    def test_administrator_token_rotation_changes_only_its_digest(self) -> None:
        backend = _MemoryCredentials()
        store = DistrictCredentialStore(backend)
        original_token, _public_key = store.provision_initial(
            cloudflare_api_token="cloudflare-api-token-value",
            management_tunnel_token="management-tunnel-token-value",
        )
        before = dict(backend.values)
        replacement = store.rotate_administrator_token()
        self.assertNotEqual(replacement, original_token)
        self.assertEqual(
            backend.values[DISTRICT_ADMIN_DIGEST_TARGET],
            hashlib.sha256(replacement.encode("utf-8")).hexdigest(),
        )
        self.assertEqual(
            {k: v for k, v in backend.values.items() if k != DISTRICT_ADMIN_DIGEST_TARGET},
            {k: v for k, v in before.items() if k != DISTRICT_ADMIN_DIGEST_TARGET},
        )


class DistrictRecoveryTests(unittest.TestCase):
    def _configured_source(self, root: Path):
        paths = resolve_runtime_paths(root / "source")
        paths.ensure_directories()
        backend = _MemoryCredentials()
        store = DistrictCredentialStore(backend)
        _raw_token, public_key = store.provision_initial(
            cloudflare_api_token="cloudflare-api-token-value",
            management_tunnel_token="management-tunnel-token-value",
        )
        document = _document()
        document["authorization_public_key_base64"] = public_key
        write_server_config(paths.configuration_file, document)
        repository = RegistrationRepository(paths.database_file)
        with repository.transaction() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO service_meta(key,value) VALUES(?,?)",
                ("recovery-test", "preserved"),
            )
        paths.provider_export_file.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "provider_name": "Schools District of Motiong School CSM Internet Gateway",
                    "registration_service_url": document["public_origin"],
                    "managed_domain": document["managed_domain"],
                    "authorization_signing_keys": [
                        {
                            "key_id": document["authorization_key_id"],
                            "public_key_base64": public_key,
                        }
                    ],
                    "trusted_proxy_addresses": ["127.0.0.1", "::1"],
                }
            ),
            encoding="utf-8",
        )
        return paths, store, backend

    def test_encrypted_backup_restores_matched_state_to_empty_host(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_paths, source_store, source_backend = self._configured_source(root)
            backup = create_recovery_backup(
                source_paths,
                root / "district-backup",
                "correct horse battery staple",
                credential_store=source_store,
            )
            self.assertEqual(backup.suffix, ".mossdistrict")
            self.assertNotIn(b"cloudflare-api-token-value", backup.read_bytes())

            restored_paths = resolve_runtime_paths(root / "restored")
            restored_backend = _MemoryCredentials()
            restore_recovery_backup(
                restored_paths,
                backup,
                "correct horse battery staple",
                credential_store=DistrictCredentialStore(restored_backend),
            )
            self.assertEqual(restored_backend.values, source_backend.values)
            self.assertEqual(
                load_server_config(restored_paths.configuration_file).public_origin,
                load_server_config(source_paths.configuration_file).public_origin,
            )
            connection = sqlite3.connect(restored_paths.database_file)
            try:
                value = connection.execute(
                    "SELECT value FROM service_meta WHERE key='recovery-test'"
                ).fetchone()[0]
            finally:
                connection.close()
            self.assertEqual(value, "preserved")

    def test_wrong_password_and_existing_state_fail_closed(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_paths, source_store, _backend = self._configured_source(root)
            backup = create_recovery_backup(
                source_paths,
                root / "district.mossdistrict",
                "correct horse battery staple",
                credential_store=source_store,
            )
            empty_paths = resolve_runtime_paths(root / "empty")
            with self.assertRaisesRegex(DistrictRecoveryError, "incorrect or"):
                restore_recovery_backup(
                    empty_paths,
                    backup,
                    "wrong password value",
                    credential_store=DistrictCredentialStore(_MemoryCredentials()),
                )
            existing_paths = resolve_runtime_paths(root / "existing")
            existing_paths.ensure_directories()
            existing_paths.configuration_file.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(DistrictRecoveryError, "empty district host"):
                restore_recovery_backup(
                    existing_paths,
                    backup,
                    "correct horse battery staple",
                    credential_store=DistrictCredentialStore(_MemoryCredentials()),
                )


class DistrictSetupTests(unittest.TestCase):
    def test_setup_writes_safe_config_and_school_provider_profile(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = resolve_runtime_paths(root / "state")
            app = root / "app"
            (app / "district_gateway_worker" / "src").mkdir(parents=True)
            (app / "district_gateway_worker" / "src" / "index.js").write_text(
                "export default {};", encoding="utf-8"
            )
            backend = _MemoryCredentials()
            result = configure_district_server(
                paths=paths,
                install_root=app,
                account_id="a" * 32,
                cloudflare_api_token="cloudflare-api-token-value",
                credential_store=DistrictCredentialStore(backend),
                bootstrapper=_FakeBootstrapper(),
            )
            self.assertTrue(paths.configuration_file.is_file())
            provider = parse_gateway_provider_config(
                json.loads(result.provider_file.read_text(encoding="utf-8"))
            )
            self.assertEqual(
                provider.service_url,
                "https://management.motiong-district-csm-survey.workers.dev",
            )
            self.assertEqual(provider.managed_domain, "motiong-district-csm-survey.workers.dev")
            self.assertNotIn(result.administrator_token, paths.configuration_file.read_text())

    def test_management_setup_refuses_to_replace_existing_worker(self) -> None:
        with TemporaryDirectory() as temporary:
            worker = Path(temporary) / "index.js"
            worker.write_text("export default {};", encoding="utf-8")

            class ExistingWorkerBootstrapper(DistrictGatewayBootstrapper):
                def __init__(self):
                    super().__init__(
                        account_id="a" * 32,
                        api_token="scoped-api-token-value",
                        worker_source=worker,
                        compatibility_date="2026-09-30",
                    )
                    self.tunnel_created = False

                def _request(self, method, path, payload=None):
                    if method == "GET" and path.endswith("/workers/scripts"):
                        return [{"id": "management"}]
                    if method == "POST" and path.endswith("/cfd_tunnel"):
                        self.tunnel_created = True
                    return {}

            bootstrapper = ExistingWorkerBootstrapper()
            with self.assertRaisesRegex(Exception, "already exists"):
                bootstrapper.provision(
                    public_host="management.motiong-district-csm-survey.workers.dev",
                    listen_port=8090,
                )
            self.assertFalse(bootstrapper.tunnel_created)


class _FakeServer:
    def __init__(self) -> None:
        self.should_exit = False
        self.force_exit = False
        self.started = Event()

    def run(self) -> None:
        self.started.set()
        self.started.wait(0.01)
        while not self.should_exit and not self.force_exit:
            Event().wait(0.01)


class _FakeTunnel:
    def __init__(self) -> None:
        self.started_with = ""
        self.state = "stopped"

    def start(self, token):
        self.started_with = token
        self.state = "connected"

    def stop(self):
        self.state = "stopped"

    def snapshot(self):
        return type("Snapshot", (), {"state": self.state, "detail": self.state})()


class DistrictControllerTests(unittest.TestCase):
    def test_health_probe_uses_trusted_forwarded_https(self) -> None:
        captured = {}

        class Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, _exc_type, _exc, _traceback):
                return False

        def open_request(request, *, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return Response()

        with patch(
            "district_management_server.server_controller.urlopen",
            side_effect=open_request,
        ):
            self.assertTrue(
                DistrictServerController._probe_health(
                    "http://127.0.0.1:8090/healthz"
                )
            )

        self.assertEqual(captured["timeout"], 1.0)
        self.assertEqual(
            captured["request"].get_header("X-forwarded-proto"), "https"
        )

    def test_controller_binds_service_to_loopback_then_starts_tunnel(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = resolve_runtime_paths(root / "state")
            paths.ensure_directories()
            app_root = root / "app"
            worker = app_root / "cloudflare_worker" / "src" / "index.js"
            worker.parent.mkdir(parents=True)
            worker.write_text("export default {};", encoding="utf-8")
            write_server_config(paths.configuration_file, _document())

            class Store:
                def load(self):
                    return DistrictSecretMaterial(
                        base64.b64encode(b"a" * 32).decode("ascii"),
                        base64.b64encode(b"b" * 32).decode("ascii"),
                        "c" * 64,
                        "cloudflare-api-token-value",
                        "management-tunnel-token-value",
                    )

            fake_server = _FakeServer()
            fake_tunnel = _FakeTunnel()
            received = {}

            def factory(_app, config):
                received["host_contract"] = (config.listen_port, config.local_health_url)
                return fake_server

            controller = DistrictServerController(
                paths,
                app_root,
                credential_store=Store(),
                tunnel=fake_tunnel,
                app_loader=lambda: object(),
                uvicorn_factory=factory,
                health_probe=lambda url: fake_server.started.is_set() and "127.0.0.1" in url,
            )
            with patch.dict(os.environ, {}, clear=False):
                snapshot = controller.start()
                self.assertEqual(snapshot.state, "running")
                self.assertEqual(fake_tunnel.started_with, "management-tunnel-token-value")
                self.assertEqual(received["host_contract"][0], 8090)
                self.assertIn("127.0.0.1", received["host_contract"][1])
                self.assertEqual(controller.stop().state, "stopped")

    def test_startup_command_targets_only_compiled_district_exe(self) -> None:
        command = compiled_startup_command(
            executable=Path(r"C:\Program Files\MoSSLab\District\District Server.exe")
        )
        self.assertIn("District Server.exe", command)
        self.assertIn("--background", command)

    def test_runtime_self_test_starts_real_registration_service(self) -> None:
        run_runtime_self_test(Path(__file__).resolve().parents[1])


if __name__ == "__main__":
    unittest.main()
