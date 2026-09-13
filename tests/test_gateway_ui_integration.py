from __future__ import annotations

import os
import inspect
from pathlib import Path
import socket
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QWidget

from school_csm_control_center.internet_gateway.config import GatewayProviderConfig
from school_csm_control_center.internet_gateway.controller import InternetGatewayController
from school_csm_control_center.storage.survey_store import SurveyStore
from school_csm_control_center.ui.internet_gateway_overlays import (
    InternetGatewaySetupOverlay,
    InternetServerTransferOverlay,
    MigrationPackageExportOverlay,
    RecoveryBackupExportOverlay,
    RegistrationDetailsOverlay,
)
from school_csm_control_center.ui.internet_gateway_panel import InternetGatewayPanel
from school_csm_control_center.ui.main_window import SchoolCSMControlCenterWindow
from school_csm_control_center.ui.school_information_board import SchoolInformationBoard
from school_csm_control_center.ui.system_tray import ControlCenterSystemTray
from school_csm_control_center.web_server.controller import SurveyServerController


class _StateStore:
    def __init__(self, state: dict | None = None) -> None:
        self.state = state or {
            "gateway_status": "not_configured",
            "authorization_status": "UNREGISTERED",
            "registration": {},
        }
        self.installation_id = "11111111-1111-4111-8111-111111111111"
        self.saved = None
        self.updates = []

    def load(self):
        return dict(self.state)

    def ensure_installation_identity(self):
        return self.installation_id

    def save_registration(self, **values):
        self.saved = dict(values)
        self.state = {
            "installation_id": values["installation_id"],
            "gateway_status": values["gateway_status"],
            "authorization_status": values["authorization_status"],
            "registration": {
                "school_id": values["school_id"],
                "registration_id": values["registration_id"],
                "public_hostname": values["public_hostname"],
                "tunnel_id": values["tunnel_id"],
                "provisioning_state": values.get("provisioning_state", ""),
            },
        }

    def update_status(self, **values):
        self.updates.append(dict(values))
        if "gateway_status" in values:
            self.state["gateway_status"] = values["gateway_status"]
        if "authorization_status" in values:
            self.state["authorization_status"] = values["authorization_status"]
        if "checked_at" in values:
            self.state["last_authorization_check_at"] = values["checked_at"]


class _Credentials:
    def __init__(self) -> None:
        self.tunnel = None
        self.installation = None

    def save_tunnel_credential(self, value):
        self.tunnel = value

    def save_installation_secret(self, value):
        self.installation = value

    def load_tunnel_credential(self):
        return self.tunnel

    def load_installation_secret(self):
        return self.installation

    def delete_tunnel_credential(self):
        self.tunnel = None

    def delete_installation_secret(self):
        self.installation = None

    def delete_all(self):
        self.tunnel = None
        self.installation = None

    def exists(self):
        return {
            "tunnel_credential": bool(self.tunnel),
            "installation_secret": bool(self.installation),
        }


class _Client:
    def __init__(self, config) -> None:
        self.config = config
        self.redeemed = None
        self.tunnel_request = None
        self.transfer_intent_payload = None
        self.health_checks = 0
        self.acknowledged = None
        self.acknowledgement_failures = 0

    def redeem_handoff(self, code, installation_id):
        self.redeemed = (code, installation_id)
        return {
            "ok": True,
            "registration_id": "registration-1",
            "school_id": "123456",
            "installation_id": installation_id,
            "public_host": "123456.csm.example.gov.ph",
            "tunnel_id": "tunnel-1",
            "tunnel_token": "tunnel-secret",
            "installation_secret": "installation-secret",
            "acknowledgement_token": "a" * 43,
        }

    def acknowledge_handoff(self, code, installation_id, acknowledgement_token):
        if self.acknowledgement_failures:
            self.acknowledgement_failures -= 1
            raise RuntimeError("simulated lost acknowledgement response")
        self.acknowledged = (code, installation_id, acknowledgement_token)
        return {"ok": True, "acknowledged": True}

    def tunnel_credential(self, school_id, installation_id, secret, *, origin_port):
        self.tunnel_request = (school_id, installation_id, secret, origin_port)
        return {"tunnel_token": "refreshed-token"}

    def create_transfer_intent(self, payload):
        self.transfer_intent_payload = dict(payload)
        return {"intent_id": "intent-123", "expires_at": "2026-08-14T12:00:00Z"}

    def health_check(self):
        self.health_checks += 1
        return True

    def authorization_status(self, school_id, installation_id, secret):
        return {"state": "active", "school_id": school_id, "installation_id": installation_id}


class _Tunnel:
    def __init__(self) -> None:
        self.received = None

    def start(self, token):
        self.received = token
        return {"state": "connected", "detail": "Connected"}

    def stop(self):
        return {"state": "stopped"}


def _config() -> GatewayProviderConfig:
    return GatewayProviderConfig(
        service_url="https://register.example.gov.ph/base",
        managed_domain="csm.example.gov.ph",
        signing_public_keys={"key-1": b"x" * 32},
        trusted_proxy_addresses=("127.0.0.1", "::1"),
    )


class GatewayControllerIntegrationTests(unittest.TestCase):
    def test_direct_workers_vpc_configuration_uses_only_the_tunnel_credential(self) -> None:
        local = {
            "school_id": "123456",
            "school_name": "Test School",
            "preferred_port": 8080,
            "server_running": False,
            "public_access_key": "survey-key",
            "scanner_remote_enabled": True,
        }
        store = _StateStore()
        credentials = _Credentials()
        tunnel = _Tunnel()
        controller = InternetGatewayController(
            Path.cwd(),
            local_settings_provider=lambda: dict(local),
            state_store=store,
            credentials=credentials,
            tunnel=tunnel,
            public_health_probe=lambda _host: {},
        )

        state = controller.configure_direct_worker_vpc(
            public_host="https://123456.school-account.workers.dev/",
            tunnel_id="22222222-2222-4222-8222-222222222222",
            tunnel_token="t" * 64,
            beta_acknowledged=True,
        )

        self.assertEqual(credentials.tunnel, "t" * 64)
        self.assertIsNone(credentials.installation)
        self.assertEqual(store.saved["provisioning_state"], "direct_worker_vpc")
        self.assertEqual(state["deployment_mode"], "direct_worker_vpc")
        self.assertEqual(state["trusted_proxy_ips"], ["127.0.0.1", "::1"])
        self.assertFalse(controller.web_server_settings()["internet_gateway_enabled"])

        checked = controller.check_authorization()
        self.assertEqual(checked["authorization_state"], "active")
        self.assertTrue(controller.authorization_verified_this_session)
        self.assertTrue(controller.web_server_settings()["internet_gateway_enabled"])

        local.update(
            server_running=True,
            server_port=8080,
            tunnel_origin_url="http://127.0.0.1:8080",
            survey_status="online",
        )
        controller.refresh_local_settings()
        self.assertTrue(controller.connect_or_reconnect())
        self.assertEqual(tunnel.received, "t" * 64)

    def test_direct_configuration_restores_the_previous_token_on_state_failure(self) -> None:
        class _FailingStateStore(_StateStore):
            def save_registration(self, **_values):
                raise OSError("simulated durable state failure")

        credentials = _Credentials()
        credentials.tunnel = "previous-token"
        controller = InternetGatewayController(
            Path.cwd(),
            local_settings_provider=lambda: {
                "school_id": "123456",
                "preferred_port": 8080,
                "server_running": False,
            },
            state_store=_FailingStateStore(),
            credentials=credentials,
            tunnel=_Tunnel(),
            public_health_probe=lambda _host: {},
        )

        with self.assertRaisesRegex(OSError, "durable state failure"):
            controller.configure_direct_worker_vpc(
                public_host="123456.school-account.workers.dev",
                tunnel_id="22222222-2222-4222-8222-222222222222",
                tunnel_token="n" * 64,
                beta_acknowledged=True,
            )
        self.assertEqual(credentials.tunnel, "previous-token")

    def test_direct_hostname_change_preserves_tunnel_credential(self) -> None:
        local = {
            "school_id": "123456",
            "preferred_port": 8080,
            "server_running": False,
        }
        store = _StateStore(
            {
                "installation_id": "11111111-1111-4111-8111-111111111111",
                "gateway_status": "disconnected",
                "authorization_status": "ACTIVE",
                "registration": {
                    "school_id": "123456",
                    "registration_id": "direct-worker-vpc-22222222-2222-4222-8222-222222222222",
                    "public_hostname": "123456.old-district.workers.dev",
                    "tunnel_id": "22222222-2222-4222-8222-222222222222",
                    "provisioning_state": "direct_worker_vpc",
                },
            }
        )
        credentials = _Credentials()
        credentials.tunnel = "existing-token"
        controller = InternetGatewayController(
            Path.cwd(),
            local_settings_provider=lambda: dict(local),
            state_store=store,
            credentials=credentials,
            tunnel=_Tunnel(),
            public_health_probe=lambda _host: {},
        )

        state = controller.update_direct_worker_hostname(
            public_host="123456.motiong-district-csm-survey.workers.dev",
            tunnel_id="22222222-2222-4222-8222-222222222222",
            beta_acknowledged=True,
        )

        self.assertEqual(credentials.tunnel, "existing-token")
        self.assertEqual(
            store.saved["public_hostname"],
            "123456.motiong-district-csm-survey.workers.dev",
        )
        self.assertEqual(
            state["public_host"],
            "123456.motiong-district-csm-survey.workers.dev",
        )
        self.assertFalse(controller.authorization_verified_this_session)

    def test_direct_configuration_rejects_wrong_school_host_and_port(self) -> None:
        local = {
            "school_id": "123456",
            "preferred_port": 8090,
            "server_running": False,
        }
        controller = InternetGatewayController(
            Path.cwd(),
            local_settings_provider=lambda: dict(local),
            state_store=_StateStore(),
            credentials=_Credentials(),
            tunnel=_Tunnel(),
            public_health_probe=lambda _host: {},
        )
        with self.assertRaisesRegex(ValueError, "begin with"):
            controller.configure_direct_worker_vpc(
                public_host="999999.school-account.workers.dev",
                tunnel_id="22222222-2222-4222-8222-222222222222",
                tunnel_token="n" * 64,
                beta_acknowledged=True,
            )
        with self.assertRaisesRegex(ValueError, "port.*8080"):
            controller.configure_direct_worker_vpc(
                public_host="123456.school-account.workers.dev",
                tunnel_id="22222222-2222-4222-8222-222222222222",
                tunnel_token="n" * 64,
                beta_acknowledged=True,
            )

    def test_local_only_default_does_not_enable_public_routing(self) -> None:
        probes = []
        store = _StateStore()
        controller = InternetGatewayController(
            Path.cwd(),
            local_settings_provider=lambda: {},
            state_store=store,
            public_health_probe=lambda host: probes.append(host),
        )
        self.assertFalse(controller.provider_available)
        self.assertFalse(controller.configured)
        self.assertFalse(controller.web_server_settings()["internet_gateway_enabled"])
        diagnostics = controller.run_diagnostics()
        self.assertEqual(probes, [])
        self.assertNotIn("public_dns", diagnostics)
        self.assertNotIn("registration_service_https", diagnostics)

    def test_diagnostics_actively_check_provider_authorization_and_public_route(self) -> None:
        state = {
            "installation_id": "11111111-1111-4111-8111-111111111111",
            "gateway_status": "connected",
            "authorization_status": "ACTIVE",
            "registration": {
                "school_id": "123456",
                "registration_id": "registration-1",
                "public_hostname": "123456.csm.example.gov.ph",
                "tunnel_id": "tunnel-1",
            },
        }
        store = _StateStore(state)
        credentials = _Credentials()
        credentials.installation = "installation-secret"
        credentials.tunnel = "tunnel-secret"
        client = _Client(_config())
        probes = []
        controller = InternetGatewayController(
            Path.cwd(),
            local_settings_provider=lambda: {
                "school_id": "123456",
                "server_running": True,
                "survey_status": "online",
                "scanner_remote_enabled": True,
            },
            state_store=store,
            client=client,
            credentials=credentials,
            provider_config=_config(),
            public_health_probe=lambda host: (
                probes.append(host)
                or {
                    "public_dns": True,
                    "public_https": True,
                    "host_routing": True,
                }
            ),
        )
        diagnostics = controller.run_diagnostics()
        self.assertEqual(client.health_checks, 1)
        self.assertEqual(probes, ["123456.csm.example.gov.ph"])
        self.assertTrue(diagnostics["registration_service_https"])
        self.assertTrue(diagnostics["authorization_checked_now"])
        self.assertTrue(diagnostics["public_dns"])
        self.assertTrue(diagnostics["public_https"])
        self.assertTrue(diagnostics["host_routing"])
        self.assertNotIn("provider_error", diagnostics)

    def test_exact_store_shape_and_browser_completion_redemption(self) -> None:
        store = _StateStore()
        credentials = _Credentials()
        client = _Client(_config())
        settings = {
            "school_id": "123456",
            "school_name": "Test School",
            "public_access_key": "access-key",
            "server_running": False,
        }
        controller = InternetGatewayController(
            Path.cwd(),
            local_settings_provider=lambda: settings,
            state_store=store,
            client=client,
            credentials=credentials,
            provider_config=_config(),
        )
        url = controller.management_url()
        parsed = urlparse(url)
        self.assertEqual(parsed.scheme, "https")
        self.assertEqual(parsed.path, "/base/manage")
        query = parse_qs(parsed.query)
        self.assertEqual(query["school_id"], ["123456"])
        self.assertEqual(query["installation_id"], [store.installation_id])
        self.assertNotIn("activation_code", query)
        controller.redeem_completion_code("ABCD-EFGH")
        self.assertEqual(client.redeemed, ("ABCD-EFGH", store.installation_id))
        self.assertEqual(
            client.acknowledged,
            ("ABCD-EFGH", store.installation_id, "a" * 43),
        )
        self.assertEqual(credentials.tunnel, "tunnel-secret")
        self.assertEqual(credentials.installation, "installation-secret")
        self.assertEqual(store.saved["authorization_status"], "ACTIVE")
        self.assertEqual(
            store.saved["public_hostname"], "123456.csm.example.gov.ph"
        )
        state = controller.state()
        self.assertEqual(state["authorization_state"], "active")
        self.assertEqual(state["registration_state"], "registered")
        self.assertEqual(state["public_host"], "123456.csm.example.gov.ph")
        self.assertFalse(controller.authorization_verified_this_session)

    def test_completion_acknowledgement_retries_after_a_lost_response(self) -> None:
        store = _StateStore()
        credentials = _Credentials()
        client = _Client(_config())
        client.acknowledgement_failures = 1
        controller = InternetGatewayController(
            Path.cwd(),
            local_settings_provider=lambda: {
                "school_id": "123456",
                "school_name": "Test School",
            },
            state_store=store,
            client=client,
            credentials=credentials,
            provider_config=_config(),
        )

        controller.redeem_completion_code("ABCD-EFGH")

        self.assertEqual(credentials.tunnel, "tunnel-secret")
        self.assertEqual(credentials.installation, "installation-secret")
        self.assertIsNotNone(client.acknowledged)

    def test_completion_is_not_acknowledged_before_local_persistence(self) -> None:
        class _FailingStateStore(_StateStore):
            def save_registration(self, **_values):
                raise OSError("simulated durable state failure")

        store = _FailingStateStore()
        credentials = _Credentials()
        credentials.tunnel = "previous-tunnel"
        credentials.installation = "previous-installation-secret"
        client = _Client(_config())
        controller = InternetGatewayController(
            Path.cwd(),
            local_settings_provider=lambda: {
                "school_id": "123456",
                "school_name": "Test School",
            },
            state_store=store,
            client=client,
            credentials=credentials,
            provider_config=_config(),
        )

        with self.assertRaisesRegex(OSError, "durable state failure"):
            controller.redeem_completion_code("ABCD-EFGH")

        self.assertIsNone(client.acknowledged)
        self.assertEqual(credentials.tunnel, "previous-tunnel")
        self.assertEqual(
            credentials.installation, "previous-installation-secret"
        )

    def test_missing_registration_store_restores_credentials_before_acknowledgement(self) -> None:
        class _ReadOnlyStateStore:
            installation_id = "11111111-1111-4111-8111-111111111111"

            def ensure_installation_identity(self):
                return self.installation_id

            def load(self):
                return {
                    "installation_id": self.installation_id,
                    "gateway_status": "not_configured",
                    "authorization_status": "UNREGISTERED",
                }

        credentials = _Credentials()
        credentials.tunnel = "previous-tunnel"
        credentials.installation = "previous-installation-secret"
        client = _Client(_config())
        controller = InternetGatewayController(
            Path.cwd(),
            local_settings_provider=lambda: {
                "school_id": "123456",
                "school_name": "Test School",
            },
            state_store=_ReadOnlyStateStore(),
            client=client,
            credentials=credentials,
            provider_config=_config(),
        )

        with self.assertRaisesRegex(RuntimeError, "storage is unavailable"):
            controller.redeem_completion_code("ABCD-EFGH")

        self.assertIsNone(client.acknowledged)
        self.assertEqual(credentials.tunnel, "previous-tunnel")
        self.assertEqual(
            credentials.installation, "previous-installation-secret"
        )

    def test_failed_acknowledgement_keeps_durably_saved_credentials_for_retry(self) -> None:
        store = _StateStore()
        credentials = _Credentials()
        client = _Client(_config())
        client.acknowledgement_failures = 2
        controller = InternetGatewayController(
            Path.cwd(),
            local_settings_provider=lambda: {
                "school_id": "123456",
                "school_name": "Test School",
            },
            state_store=store,
            client=client,
            credentials=credentials,
            provider_config=_config(),
        )

        with self.assertRaisesRegex(RuntimeError, "saved securely"):
            controller.redeem_completion_code("ABCD-EFGH")

        self.assertEqual(credentials.tunnel, "tunnel-secret")
        self.assertEqual(credentials.installation, "installation-secret")
        self.assertEqual(store.saved["authorization_status"], "ACTIVE")

    def test_tunnel_uses_running_loopback_origin_port_and_credential_manager(self) -> None:
        store = _StateStore(
            {
                "installation_id": "11111111-1111-4111-8111-111111111111",
                "gateway_status": "disconnected",
                "authorization_status": "ACTIVE",
                "registration": {
                    "school_id": "123456",
                    "registration_id": "registration-1",
                    "public_hostname": "123456.csm.example.gov.ph",
                    "tunnel_id": "tunnel-1",
                },
            }
        )
        credentials = _Credentials()
        credentials.installation = "installation-secret"
        credentials.tunnel = "old-token"
        client = _Client(_config())
        tunnel = _Tunnel()
        controller = InternetGatewayController(
            Path.cwd(),
            local_settings_provider=lambda: {
                "school_id": "123456",
                "server_running": True,
                "server_port": 8123,
            },
            state_store=store,
            client=client,
            tunnel=tunnel,
            credentials=credentials,
            provider_config=_config(),
        )
        self.assertFalse(controller.web_server_settings()["internet_gateway_enabled"])
        with self.assertRaisesRegex(RuntimeError, "Verify this device"):
            controller.connect_or_reconnect()
        controller.check_authorization()
        self.assertTrue(controller.authorization_verified_this_session)
        self.assertTrue(controller.web_server_settings()["internet_gateway_enabled"])
        self.assertTrue(controller.connect_or_reconnect())
        self.assertEqual(client.tunnel_request[-1], 8123)
        self.assertEqual(tunnel.received, "refreshed-token")

    def test_cached_active_state_requires_a_fresh_provider_check_each_session(self) -> None:
        store = _StateStore(
            {
                "installation_id": "11111111-1111-4111-8111-111111111111",
                "gateway_status": "disconnected",
                "authorization_status": "ACTIVE",
                "registration": {
                    "school_id": "123456",
                    "registration_id": "registration-1",
                    "public_hostname": "123456.csm.example.gov.ph",
                    "tunnel_id": "tunnel-1",
                },
            }
        )
        credentials = _Credentials()
        credentials.installation = "installation-secret"
        credentials.tunnel = "tunnel-secret"
        client = _Client(_config())
        controller = InternetGatewayController(
            Path.cwd(),
            local_settings_provider=lambda: {
                "school_id": "123456",
                "server_running": True,
                "server_port": 8123,
            },
            state_store=store,
            client=client,
            tunnel=_Tunnel(),
            credentials=credentials,
            provider_config=_config(),
        )

        self.assertEqual(controller.state()["authorization_state"], "active")
        self.assertFalse(controller.authorization_verified_this_session)
        self.assertFalse(
            controller.web_server_settings()["internet_gateway_enabled"]
        )
        with self.assertRaisesRegex(RuntimeError, "Verify this device"):
            controller.connect_or_reconnect()

        controller.check_authorization()
        self.assertTrue(controller.authorization_verified_this_session)
        self.assertTrue(
            controller.web_server_settings()["internet_gateway_enabled"]
        )

        def unavailable(*_args, **_kwargs):
            raise RuntimeError("provider unavailable")

        client.authorization_status = unavailable
        controller.check_authorization()
        self.assertFalse(controller.authorization_verified_this_session)
        self.assertFalse(
            controller.web_server_settings()["internet_gateway_enabled"]
        )
        with self.assertRaises(RuntimeError):
            controller.connect_or_reconnect()

    def test_authorization_loss_disconnects_and_requests_listener_narrowing(self) -> None:
        calls = []

        class _Server:
            @staticmethod
            def wildcard_listener_active():
                return True

            @staticmethod
            def stop_async():
                calls.append("stop-server")
                return True

        class _Gateway:
            @staticmethod
            def disconnect():
                calls.append("disconnect-gateway")

        class _Toast:
            @staticmethod
            def show_message(*_args, **_kwargs):
                calls.append("toast")

        class _Harness:
            _narrow_gateway_listener_after_authorization_loss = (
                SchoolCSMControlCenterWindow._narrow_gateway_listener_after_authorization_loss
            )

        harness = _Harness()
        harness.server_controller = _Server()
        harness.gateway_controller = _Gateway()
        harness.toast = _Toast()
        harness._gateway_listener_narrowing_pending = False

        harness._narrow_gateway_listener_after_authorization_loss()

        self.assertEqual(calls, ["disconnect-gateway", "stop-server"])
        self.assertTrue(harness._gateway_listener_narrowing_pending)

    def test_transfer_uses_server_issued_intent_fragment(self) -> None:
        store = _StateStore()
        client = _Client(_config())
        controller = InternetGatewayController(
            Path.cwd(),
            local_settings_provider=lambda: {
                "school_id": "123456",
                "school_name": "Test School",
            },
            state_store=store,
            client=client,
            provider_config=_config(),
        )
        validation = {
            "verified": True,
            "transaction_id": "transaction-1",
            "manifest_sha256": "a" * 64,
            "active_tree_sha256": "b" * 64,
            "record_counts": {"responses": 27},
        }
        result = controller.create_transfer_intent(
            {"transfer_mode": "server_and_data", "data_validation": validation}
        )
        parsed = urlparse(result["management_url"])
        self.assertEqual(parsed.query, "")
        self.assertEqual(parsed.fragment, "intent=intent-123")
        self.assertEqual(
            client.transfer_intent_payload,
            {
                "school_id": "123456",
                "destination_installation_id": store.installation_id,
                "transfer_mode": "server_and_data",
                "data_validation": validation,
            },
        )
        with self.assertRaises(ValueError):
            controller.management_url(mode="transfer", data_verified=True)
        with self.assertRaises(ValueError):
            controller.create_transfer_intent(
                {
                    "transfer_mode": "server_and_data",
                    "data_validation": {"verified": True},
                }
            )
        passkeys = urlparse(controller.passkey_management_url())
        self.assertEqual(
            parse_qs(passkeys.query),
            {"mode": ["passkeys"], "school_id": ["123456"]},
        )

    def test_backup_transfer_intent_accepts_only_exact_eight_key_receipt(self) -> None:
        store = _StateStore()
        client = _Client(_config())
        controller = InternetGatewayController(
            Path.cwd(),
            local_settings_provider=lambda: {
                "school_id": "123456",
                "school_name": "Test School",
            },
            state_store=store,
            client=client,
            provider_config=_config(),
        )
        receipt = {
            "verified": True,
            "source_kind": "portable_backup",
            "backup_id": "44444444-4444-4444-8444-444444444444",
            "backup_manifest_sha256": "c" * 64,
            "transaction_id": "33333333-3333-4333-8333-333333333333",
            "manifest_sha256": "a" * 64,
            "active_tree_sha256": "b" * 64,
            "record_counts": {"responses": 27},
        }
        controller.create_transfer_intent(
            {"transfer_mode": "backup_assisted", "data_validation": receipt}
        )
        self.assertEqual(
            client.transfer_intent_payload["data_validation"], receipt
        )
        self.assertEqual(len(client.transfer_intent_payload["data_validation"]), 8)
        with self.assertRaises(ValueError):
            controller.create_transfer_intent(
                {
                    "transfer_mode": "server_and_data",
                    "data_validation": receipt,
                }
            )
        incomplete = dict(receipt)
        incomplete.pop("backup_manifest_sha256")
        with self.assertRaises(ValueError):
            controller.create_transfer_intent(
                {
                    "transfer_mode": "backup_assisted",
                    "data_validation": incomplete,
                }
            )

    def test_migration_receipt_is_built_only_after_commit(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest_path = root / "manifest.json"
            manifest_path.write_text(
                '{"manifest_sha256":"' + ("a" * 64) + '"}', encoding="utf-8"
            )
            staged = SimpleNamespace(
                manifest_path=manifest_path,
                record_counts={"responses": 3},
            )

            class _Migration:
                received_key = None
                committed = False

                def stage_import(self, _path, *, key, **_values):
                    self.received_key = key
                    return staged

                def commit_import(self, _staged):
                    self.committed = True
                    return SimpleNamespace(
                        transaction_id="transaction-1",
                        active_tree_sha256="b" * 64,
                        committed_at="2026-08-14T11:00:00Z",
                    )

            migration = _Migration()
            controller = InternetGatewayController(
                root,
                local_settings_provider=lambda: {
                    "school_id": "123456",
                    "server_running": False,
                },
                state_store=_StateStore(),
                migration=migration,
            )
            result = controller.prepare_migration_import(
                {
                    "package_path": str(root / "transfer.mossmig"),
                    "migration_key": "01" * 32,
                    "replace_existing": True,
                }
            )
            self.assertTrue(migration.committed)
            self.assertEqual(migration.received_key, b"\x01" * 32)
            self.assertEqual(result["data_validation"]["verified"], True)
            self.assertEqual(
                result["data_validation"]["active_tree_sha256"], "b" * 64
            )

    def test_active_source_creates_destination_bound_package_with_one_time_key(self) -> None:
        captured = {}

        class _Migration:
            @staticmethod
            def create_package(destination, **values):
                captured.update(destination=destination, **values)
                return {
                    "transaction_id": "transaction-1",
                    "manifest_sha256": "a" * 64,
                    "record_counts": {"responses": 9},
                }

        store = _StateStore(
            {
                "installation_id": "11111111-1111-4111-8111-111111111111",
                "gateway_status": "disconnected",
                "authorization_status": "ACTIVE",
                "registration": {
                    "school_id": "123456",
                    "registration_id": "registration-1",
                    "public_hostname": "123456.csm.example.gov.ph",
                    "tunnel_id": "tunnel-1",
                },
            }
        )
        controller = InternetGatewayController(
            Path.cwd(),
            local_settings_provider=lambda: {
                "school_id": "123456",
                "server_running": False,
            },
            state_store=store,
            migration=_Migration(),
        )
        result = controller.create_migration_package(
            {
                "destination_installation_id": "22222222-2222-4222-8222-222222222222",
                "destination_path": "replacement-package",
            }
        )
        self.assertEqual(len(captured["key"]), 32)
        self.assertEqual(result["migration_key"], captured["key"].hex())
        self.assertTrue(str(captured["destination"]).endswith(".mossmig"))
        self.assertEqual(
            captured["destination_installation_id"],
            "22222222-2222-4222-8222-222222222222",
        )
        self.assertNotIn("migration_key", controller.state())

    def test_migration_export_keeps_redirect_evidence_for_service_validation(self) -> None:
        source = inspect.getsource(InternetGatewayController.create_migration_package)
        self.assertIn("Path(destination_text).expanduser().absolute()", source)
        self.assertNotIn("Path(destination_text).expanduser().resolve()", source)

    def test_local_only_startup_makes_no_provider_or_tunnel_call(self) -> None:
        calls = []

        class _NoNetworkClient:
            config = _config()

            def authorization_status(self, *_args, **_kwargs):
                calls.append("provider")
                raise AssertionError("Local-Only must not call the provider")

        class _NoTunnel:
            def start(self, *_args, **_kwargs):
                calls.append("tunnel")
                raise AssertionError("Local-Only must not start a tunnel")

        controller = InternetGatewayController(
            Path.cwd(),
            local_settings_provider=lambda: {"school_id": "123456"},
            state_store=_StateStore(),
            client=_NoNetworkClient(),
            tunnel=_NoTunnel(),
            provider_config=_config(),
        )
        controller.refresh_local_settings()
        controller.check_authorization()
        with self.assertRaises(RuntimeError):
            controller.create_recovery_backup(
                {"destination_path": "local-only.mossbak"}
            )
        self.assertFalse(controller.web_server_settings()["internet_gateway_enabled"])
        self.assertEqual(calls, [])

    def test_main_window_routes_each_data_source_to_its_only_controller_path(self) -> None:
        calls = []

        class _Gateway:
            @staticmethod
            def prepare_migration_import_async(payload):
                calls.append(("migration", dict(payload)))
                return True

            @staticmethod
            def restore_recovery_backup_async(payload):
                calls.append(("backup", dict(payload)))
                return True

        class _Overlay:
            @staticmethod
            def set_progress(*_args, **_kwargs):
                return None

        class _Harness:
            _start_gateway_data_activation = (
                SchoolCSMControlCenterWindow._start_gateway_data_activation
            )

        harness = _Harness()
        harness.gateway_controller = _Gateway()
        harness.gateway_transfer_overlay = _Overlay()
        harness._pending_gateway_transfer_mode = "server_and_data"
        harness._start_gateway_data_activation({"package_path": "source.mossmig"})
        harness._pending_gateway_transfer_mode = "backup_assisted"
        harness._start_gateway_data_activation({"package_path": "recovery.mossbak"})
        self.assertEqual(
            calls,
            [
                ("migration", {"package_path": "source.mossmig"}),
                ("backup", {"package_path": "recovery.mossbak"}),
            ],
        )

    def test_recovery_backup_lifecycle_pauses_and_restores_server_and_tunnel(self) -> None:
        calls = []

        class _Server:
            running = True

            @staticmethod
            def stop_async():
                calls.append("stop-server")
                return True

            @staticmethod
            def tunnel_origin_url():
                return "http://127.0.0.1:8080"

        class _Gateway:
            connected = True

            @staticmethod
            def disconnect():
                calls.append("stop-tunnel")

            @staticmethod
            def connect_or_reconnect_async():
                calls.append("restart-tunnel")
                return True

        class _Overlay:
            @staticmethod
            def set_progress(*_args, **_kwargs):
                return None

        class _Tray:
            @staticmethod
            def set_server_state(*_args):
                return None

            @staticmethod
            def show_status_message(*_args, **_kwargs):
                return None

        class _Harness:
            _prepare_gateway_backup_export = (
                SchoolCSMControlCenterWindow._prepare_gateway_backup_export
            )
            _restore_server_after_gateway_backup = (
                SchoolCSMControlCenterWindow._restore_server_after_gateway_backup
            )
            _tray_server_status_changed = (
                SchoolCSMControlCenterWindow._tray_server_status_changed
            )

        harness = _Harness()
        harness.server_controller = _Server()
        harness.gateway_controller = _Gateway()
        harness.gateway_backup_export_overlay = _Overlay()
        harness.gateway_package_export_overlay = _Overlay()
        harness.gateway_transfer_overlay = _Overlay()
        harness.system_tray = _Tray()
        harness._pending_gateway_backup_export = None
        harness._pending_gateway_package_export = None
        harness._pending_gateway_migration = None
        harness._pending_gateway_transfer_mode = ""
        harness._gateway_connect_pending = False
        harness._gateway_listener_narrowing_pending = False
        harness._gateway_auto_reconnect_suppressed = False
        harness._request_background_gateway_reconnect = lambda: calls.append(
            "background-reconnect"
        )

        def restart_server():
            calls.append("restart-server")
            _Server.running = True

        harness.start_saved_server_unattended = restart_server
        harness._prepare_gateway_backup_export(
            {"destination_path": "recovery.mossbak"}
        )
        self.assertEqual(calls, ["stop-tunnel", "stop-server"])
        self.assertEqual(
            harness._pending_gateway_backup_export,
            {"destination_path": "recovery.mossbak"},
        )
        _Server.running = False
        with patch.object(QTimer, "singleShot", side_effect=lambda _delay, fn: fn()):
            harness._restore_server_after_gateway_backup()
            harness._tray_server_status_changed("online", "Running")
        self.assertEqual(calls[-2:], ["restart-server", "restart-tunnel"])

    def test_background_gateway_waits_for_local_health_and_authorization(self) -> None:
        calls = []

        class _Server:
            running = False

            @staticmethod
            def tunnel_origin_url():
                return "http://127.0.0.1:8080" if _Server.running else ""

        class _Gateway:
            provider_available = True
            configured = True
            operation_in_progress = False

            @staticmethod
            def state():
                return {
                    "authorization_state": "active",
                    "authorized_this_device": True,
                    "gateway_state": "disconnected",
                    "retirement": None,
                }

            @staticmethod
            def check_authorization_async():
                calls.append("authorization")
                return True

            @staticmethod
            def connect_or_reconnect_async():
                calls.append("connect")
                return True

        class _Harness:
            _background_gateway_reconnect_candidate = (
                SchoolCSMControlCenterWindow._background_gateway_reconnect_candidate
            )
            _request_background_gateway_reconnect = (
                SchoolCSMControlCenterWindow._request_background_gateway_reconnect
            )

        harness = _Harness()
        harness._background_startup_enabled = True
        harness._gateway_auto_reconnect_suppressed = False
        harness._gateway_auto_reconnect_pending = False
        harness._gateway_authorization_verified_this_session = False
        harness.server_controller = _Server()
        harness.gateway_controller = _Gateway()

        harness._request_background_gateway_reconnect()
        self.assertEqual(calls, [])
        _Server.running = True
        harness._request_background_gateway_reconnect()
        self.assertEqual(calls, ["authorization"])
        harness._gateway_authorization_verified_this_session = True
        harness._request_background_gateway_reconnect()
        self.assertEqual(calls, ["authorization", "connect"])

        calls.clear()
        harness._background_startup_enabled = False
        harness._request_background_gateway_reconnect()
        self.assertEqual(calls, [])
        harness._background_startup_enabled = True
        harness.gateway_controller.provider_available = False
        harness._request_background_gateway_reconnect()
        self.assertEqual(calls, [])
        harness.gateway_controller.direct_mode = True
        harness._request_background_gateway_reconnect()
        self.assertEqual(calls, ["connect"])


class GatewayDesktopUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_setup_overlay_collects_only_completion_code(self) -> None:
        parent = QWidget()
        overlay = InternetGatewaySetupOverlay(parent)
        self.assertFalse(hasattr(overlay, "activation_code"))
        self.assertTrue(hasattr(overlay, "completion_code"))
        overlay.configure(
            school_id="123456",
            school_name="Test School",
            provider_available=True,
        )
        opened = []
        redeemed = []
        overlay.browser_authorization_requested.connect(opened.append)
        overlay.completion_code_requested.connect(redeemed.append)
        overlay.register_button.click()
        overlay.completion_code.setText("ABCD-EFGH")
        overlay.redeem_button.click()
        self.assertEqual(opened, ["registration"])
        self.assertEqual(redeemed, ["ABCD-EFGH"])
        parent.deleteLater()

    def test_setup_overlay_collects_direct_connector_without_retaining_token(self) -> None:
        parent = QWidget()
        overlay = InternetGatewaySetupOverlay(parent)
        overlay.configure(
            school_id="123456",
            school_name="Test School",
            provider_available=False,
            deployment_mode="direct_worker_vpc",
            public_host="123456.school-account.workers.dev",
            tunnel_id="22222222-2222-4222-8222-222222222222",
        )
        requests = []
        overlay.direct_configuration_requested.connect(requests.append)
        overlay.direct_tunnel_token.setText("t" * 64)
        overlay.direct_beta_acknowledgement.setChecked(True)
        overlay.direct_save_button.click()

        self.assertEqual(len(requests), 1)
        self.assertEqual(
            requests[0]["public_host"],
            "123456.school-account.workers.dev",
        )
        self.assertEqual(requests[0]["tunnel_token"], "t" * 64)
        self.assertTrue(requests[0]["beta_acknowledged"])
        self.assertEqual(overlay.direct_tunnel_token.text(), "")
        parent.deleteLater()

    def test_setup_overlay_allows_hostname_only_district_migration(self) -> None:
        parent = QWidget()
        overlay = InternetGatewaySetupOverlay(parent)
        overlay.configure(
            school_id="123456",
            school_name="Test School",
            provider_available=False,
            deployment_mode="direct_worker_vpc",
            public_host="123456.old-district.workers.dev",
            tunnel_id="22222222-2222-4222-8222-222222222222",
        )
        requests = []
        overlay.direct_configuration_requested.connect(requests.append)
        overlay.direct_public_host.setText(
            "123456.motiong-district-csm-survey.workers.dev"
        )
        overlay.direct_beta_acknowledgement.setChecked(True)
        self.assertTrue(overlay.direct_save_button.isEnabled())
        overlay.direct_save_button.click()

        self.assertEqual(len(requests), 1)
        self.assertTrue(requests[0]["preserve_tunnel_credential"])
        self.assertEqual(requests[0]["tunnel_token"], "")
        self.assertEqual(
            requests[0]["public_host"],
            "123456.motiong-district-csm-survey.workers.dev",
        )
        parent.deleteLater()

    def test_setup_overlay_keeps_direct_connector_fields_separate_when_short(self) -> None:
        parent = QWidget()
        parent.resize(900, 560)
        parent.show()
        overlay = InternetGatewaySetupOverlay(parent)
        overlay.configure(
            school_id="123456",
            school_name="Test School",
            provider_available=False,
        )
        overlay.open_overlay()
        self.app.processEvents()

        self.assertIsNotNone(overlay.body_scroll)
        self.assertGreater(overlay.body_scroll.verticalScrollBar().maximum(), 0)
        token_top = overlay.direct_tunnel_token.mapTo(overlay.body, QPoint()).y()
        token_bottom = token_top + overlay.direct_tunnel_token.height()
        acknowledgement_top = overlay.direct_beta_acknowledgement.mapTo(
            overlay.body, QPoint()
        ).y()
        self.assertGreaterEqual(acknowledgement_top, token_bottom)
        self.assertGreaterEqual(overlay.direct_tunnel_token.height(), 20)

        overlay.body_scroll.verticalScrollBar().setValue(
            overlay.body_scroll.verticalScrollBar().maximum()
        )
        self.app.processEvents()
        save_top = overlay.direct_save_button.mapTo(
            overlay.body_scroll.viewport(), QPoint()
        ).y()
        self.assertGreaterEqual(save_top, 0)
        self.assertLess(save_top, overlay.body_scroll.viewport().height())
        parent.deleteLater()

    def test_direct_gateway_setup_can_be_reopened_only_while_disconnected(self) -> None:
        panel = InternetGatewayPanel()
        direct_state = {
            "deployment_mode": "direct_worker_vpc",
            "registration_state": "registered",
            "authorization_state": "active",
            "gateway_state": "disconnected",
            "school_id": "123456",
            "public_host": "123456.school-account.workers.dev",
        }

        panel.apply_state(direct_state)
        self.assertTrue(panel.setup_button.isEnabled())

        panel.apply_state({**direct_state, "gateway_state": "connected"})
        self.assertFalse(panel.setup_button.isEnabled())

        panel.apply_state({**direct_state, "deployment_mode": "managed"})
        self.assertFalse(panel.setup_button.isEnabled())
        panel.deleteLater()

    def test_transfer_overlay_never_accepts_a_browser_verification_checkbox(self) -> None:
        parent = QWidget()
        overlay = InternetServerTransferOverlay(parent)
        overlay.configure(school_id="123456", school_name="Test School")
        browser_requests = []
        migration_requests = []
        overlay.browser_transfer_requested.connect(browser_requests.append)
        overlay.migration_import_requested.connect(migration_requests.append)

        overlay.server_only.setChecked(True)
        overlay.continue_button.click()
        self.assertEqual(browser_requests, [])
        overlay.server_only_confirmation.setChecked(True)
        overlay.continue_button.click()
        self.assertEqual(
            browser_requests,
            [
                {
                    "transfer_mode": "server_only",
                    "data_validation": {"warning_confirmed": True},
                }
            ],
        )

        overlay.server_and_data.setChecked(True)
        overlay._package_path = "transfer.mossmig"
        overlay.package_path.setText("transfer.mossmig")
        overlay.migration_key.setText("01" * 32)
        overlay.continue_button.click()
        self.assertEqual(len(migration_requests), 1)
        self.assertNotIn("data_verified", migration_requests[0])
        self.assertEqual(overlay.migration_key.text(), "")
        parent.deleteLater()

    def test_transfer_overlay_routes_mossmig_and_mossbak_to_separate_signals(self) -> None:
        parent = QWidget()
        overlay = InternetServerTransferOverlay(parent)
        overlay.configure(
            school_id="123456",
            school_name="Test School",
            installation_id="11111111-1111-4111-8111-111111111111",
        )
        migrations = []
        recoveries = []
        overlay.migration_import_requested.connect(migrations.append)
        overlay.recovery_restore_requested.connect(recoveries.append)

        overlay.backup_assisted.setChecked(True)
        self.assertIn(".mossbak", overlay.package_path.placeholderText())
        self.assertIn("Recovery-backup", overlay.migration_key.placeholderText())
        overlay._package_path = "portable.mossbak"
        overlay.package_path.setText(overlay._package_path)
        overlay.migration_key.setText("01" * 32)
        overlay.continue_button.click()
        self.assertEqual(len(recoveries), 1)
        self.assertEqual(recoveries[0]["transfer_mode"], "backup_assisted")
        self.assertEqual(migrations, [])

        overlay.server_and_data.setChecked(True)
        self.assertEqual(overlay.package_path.text(), "")
        self.assertIn(".mossmig", overlay.package_path.placeholderText())
        overlay._package_path = "wrong.mossbak"
        overlay.package_path.setText(overlay._package_path)
        overlay.migration_key.setText("02" * 32)
        overlay.continue_button.click()
        self.assertEqual(migrations, [])
        overlay._package_path = "destination.mossmig"
        overlay.package_path.setText(overlay._package_path)
        overlay.migration_key.setText("02" * 32)
        overlay.continue_button.click()
        self.assertEqual(len(migrations), 1)
        self.assertEqual(migrations[0]["transfer_mode"], "server_and_data")
        parent.deleteLater()

    def test_registration_details_exposes_browser_passkey_management(self) -> None:
        parent = QWidget()
        overlay = RegistrationDetailsOverlay(parent)
        requested = []
        overlay.manage_passkeys_requested.connect(lambda: requested.append(True))
        overlay.show_state(
            {
                "provider_available": True,
                "school_id": "123456",
                "registration_state": "registered",
                "authorization_state": "active",
            }
        )
        self.assertTrue(overlay.manage_passkeys_button.isEnabled())
        overlay.manage_passkeys_button.click()
        self.assertEqual(requested, [True])
        parent.deleteLater()

    def test_source_package_key_is_shown_once_and_cleared_on_close(self) -> None:
        parent = QWidget()
        parent.resize(1000, 800)
        overlay = MigrationPackageExportOverlay(parent)
        overlay.configure(
            school_id="123456",
            school_name="Test School",
            source_installation_id="11111111-1111-4111-8111-111111111111",
        )
        overlay.open_overlay()
        overlay.show_result(
            {
                "package_path": "C:/transfer.mossmig",
                "migration_key": "01" * 32,
                "record_counts": {"responses": 9},
            }
        )
        self.assertEqual(overlay.migration_key.text(), "01" * 32)
        self.assertTrue(overlay.copy_key.isEnabled())
        overlay.close_overlay()
        self.assertEqual(overlay.migration_key.text(), "")
        parent.deleteLater()

    def test_source_recovery_backup_key_is_shown_once_and_cleared_on_close(self) -> None:
        parent = QWidget()
        parent.resize(1000, 800)
        overlay = RecoveryBackupExportOverlay(parent)
        overlay.configure(
            school_id="123456",
            school_name="Test School",
            source_installation_id="11111111-1111-4111-8111-111111111111",
        )
        requested = []
        overlay.backup_requested.connect(requested.append)
        overlay.destination_path.setText("C:/recovery.mossbak")
        overlay.create_button.click()
        self.assertEqual(
            requested, [{"destination_path": "C:/recovery.mossbak"}]
        )
        overlay.open_overlay()
        overlay.show_result(
            {
                "package_path": "C:/recovery.mossbak",
                "backup_key": "03" * 32,
                "record_counts": {"responses": 9},
            }
        )
        self.assertEqual(len(overlay.backup_key.text()), 64)
        self.assertTrue(overlay.copy_key.isEnabled())
        overlay.close_overlay()
        self.assertEqual(overlay.backup_key.text(), "")
        parent.deleteLater()

    def test_registered_school_id_is_locked(self) -> None:
        with TemporaryDirectory() as temporary:
            board = SchoolInformationBoard(Path(temporary))
            board.set_registered_school_id("123456")
            self.assertTrue(board.school_id.isReadOnly())
            self.assertEqual(board.school_id.text(), "123456")
            board.school_id.setText("654321")
            with self.assertRaises(ValueError):
                board.save_settings()
            self.assertEqual(board.school_id.text(), "123456")
            board.deleteLater()

    def test_tray_gateway_actions_follow_status(self) -> None:
        parent = QWidget()
        tray = ControlCenterSystemTray(
            QIcon(), parent, available_override=True, show_immediately=False
        )
        tray.set_gateway_state(
            "disconnected",
            configured=True,
            authorized=True,
            survey_url="https://123456.csm.example.gov.ph/access/key",
            scanner_url="https://123456.csm.example.gov.ph/scanner",
        )
        self.assertTrue(tray.connect_gateway_action.isEnabled())
        tray.set_gateway_state(
            "connected",
            configured=True,
            authorized=True,
            survey_url="https://123456.csm.example.gov.ph/access/key",
            scanner_url="https://123456.csm.example.gov.ph/scanner",
            scanner_enabled=True,
        )
        self.assertTrue(tray.disconnect_gateway_action.isEnabled())
        self.assertTrue(tray.open_internet_survey_action.isEnabled())
        self.assertTrue(tray.open_internet_scanner_action.isEnabled())
        scanner_urls = []
        tray.open_internet_scanner_requested.connect(scanner_urls.append)
        tray.open_internet_scanner_action.trigger()
        self.assertEqual(
            scanner_urls, ["https://123456.csm.example.gov.ph/scanner"]
        )
        tray.set_server_state("online", "Healthy")
        self.assertIn("Local: Running", tray.icon.toolTip())
        self.assertIn("Gateway: Connected", tray.icon.toolTip())
        self.assertLessEqual(len(tray.icon.toolTip()), 127)
        tray.set_gateway_state(
            "connected",
            configured=True,
            authorized=True,
            survey_url="https://123456.csm.example.gov.ph/access/key",
            scanner_url="https://123456.csm.example.gov.ph/scanner",
            scanner_enabled=False,
        )
        self.assertFalse(tray.open_internet_scanner_action.isEnabled())
        parent.deleteLater()


class GatewayLoopbackBindingTests(unittest.TestCase):
    def test_gateway_listener_accepts_loopback_without_losing_lan_url(self) -> None:
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])
        probe.close()
        with TemporaryDirectory() as temporary, patch(
            "school_csm_control_center.web_server.controller.is_windows",
            return_value=False,
        ), patch(
            "school_csm_control_center.web_server.controller.local_ipv4_candidates",
            return_value=[("127.0.0.1 - Test adapter", "127.0.0.1")],
        ):
            root = Path(temporary)
            controller = SurveyServerController(SurveyStore(root), root)
            controller.set_runtime_settings_provider(
                lambda: {
                    "internet_gateway_enabled": True,
                    "internet_gateway_public_host": "123456.csm.example.gov.ph",
                    "internet_gateway_trusted_proxy_ips": ["127.0.0.1"],
                }
            )
            try:
                controller.start(port, port, configure_firewall=False)
                self.assertEqual(controller._server.server_address[0], "0.0.0.0")
                self.assertEqual(controller.tunnel_origin_url(), f"http://127.0.0.1:{port}")
                self.assertIn("127.0.0.1", controller.urls()[0])
            finally:
                controller.stop()

    def test_cached_authorization_does_not_create_a_wildcard_listener(self) -> None:
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])
        probe.close()
        with TemporaryDirectory() as temporary, patch(
            "school_csm_control_center.web_server.controller.is_windows",
            return_value=False,
        ), patch(
            "school_csm_control_center.web_server.controller.local_ipv4_candidates",
            return_value=[("127.0.0.1 - Test adapter", "127.0.0.1")],
        ):
            root = Path(temporary)
            controller = SurveyServerController(SurveyStore(root), root)
            controller.set_runtime_settings_provider(
                lambda: {
                    "internet_gateway_enabled": False,
                    "internet_gateway_public_host": "123456.csm.example.gov.ph",
                    "internet_gateway_trusted_proxy_ips": ["127.0.0.1"],
                }
            )
            try:
                controller.start(port, port, configure_firewall=False)
                self.assertEqual(controller._server.server_address[0], "127.0.0.1")
                self.assertFalse(controller.wildcard_listener_active())
                self.assertEqual(controller.tunnel_origin_url(), "")
            finally:
                controller.stop()


if __name__ == "__main__":
    unittest.main()
