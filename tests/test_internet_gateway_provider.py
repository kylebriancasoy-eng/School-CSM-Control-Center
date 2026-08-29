from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import tempfile
from threading import Event
import unittest
from unittest.mock import patch

from school_csm_control_center.internet_gateway.client import (
    GatewayClientError,
    GatewayProviderClient,
    probe_public_gateway,
    verify_authorization_envelope,
)
from school_csm_control_center.internet_gateway.config import (
    GatewayProviderConfigurationError,
    load_gateway_provider_config,
    parse_gateway_provider_config,
)
from school_csm_control_center.internet_gateway.tunnel import CloudflaredTunnel


class _Response:
    status = 200

    def __init__(self, payload: dict) -> None:
        self.body = json.dumps(payload).encode("utf-8")

    def getheader(self, _name: str):
        return str(len(self.body))

    def read(self, size: int) -> bytes:
        return self.body[:size]


class _Connection:
    def __init__(self, payload: dict) -> None:
        self.response = _Response(payload)
        self.request_args = None
        self.closed = False

    def request(self, *args, **kwargs) -> None:
        self.request_args = (args, kwargs)

    def getresponse(self):
        return self.response

    def close(self) -> None:
        self.closed = True


class _Process:
    pid = 7123
    stdout = None

    def __init__(self) -> None:
        self.returncode = None
        self.finished = Event()

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if self.returncode is None and timeout is None:
            self.finished.wait(5)
        elif self.returncode is None:
            self.finished.wait(timeout)
        return self.returncode

    def terminate(self):
        self.returncode = 0
        self.finished.set()

    def kill(self):
        self.returncode = -1
        self.finished.set()


def _config_document(public_key: bytes = b"k" * 32) -> dict:
    return {
        "schema_version": "1.0",
        "provider_name": "Division Gateway",
        "registration_service_url": "https://register.csm.example.gov.ph/base",
        "managed_domain": "csm.example.gov.ph",
        "authorization_signing_keys": [
            {
                "key_id": "provider-2026",
                "public_key_base64": base64.b64encode(public_key).decode("ascii"),
            }
        ],
        "trusted_proxy_addresses": ["127.0.0.1", "::1"],
    }


class InternetGatewayProviderTests(unittest.TestCase):
    def test_example_file_cannot_enable_gateway(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "internet_gateway_provider.example.json").write_text(
                json.dumps(_config_document()), encoding="utf-8"
            )
            self.assertIsNone(load_gateway_provider_config(root))

    def test_provider_config_is_https_and_school_host_is_deterministic(self) -> None:
        config = parse_gateway_provider_config(_config_document())
        self.assertEqual(config.public_host_for("123456"), "123456.csm.example.gov.ph")
        unsafe = _config_document()
        unsafe["registration_service_url"] = "http://register.example.gov.ph"
        with self.assertRaises(GatewayProviderConfigurationError):
            parse_gateway_provider_config(unsafe)

    def test_provider_config_requires_an_explicit_trusted_proxy_list(self) -> None:
        missing = _config_document()
        missing.pop("trusted_proxy_addresses")
        with self.assertRaisesRegex(
            GatewayProviderConfigurationError,
            "At least one trusted proxy address is required",
        ):
            parse_gateway_provider_config(missing)

    def test_client_uses_bounded_https_path_and_bearer_header(self) -> None:
        connection = _Connection({"ok": True})
        client = GatewayProviderClient(
            parse_gateway_provider_config(_config_document()),
            connection_factory=lambda parsed, timeout: connection,
        )
        result = client.tunnel_credential(
            "123456", "installation-1", "secret-value", origin_port=8080
        )
        self.assertTrue(result["ok"])
        args, kwargs = connection.request_args
        self.assertEqual(args[:2], ("POST", "/base/v1/installations/installation-1/tunnel-credential"))
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer secret-value")
        self.assertNotIn("secret-value", args[1])
        self.assertTrue(connection.closed)

    def test_registration_and_public_health_probes_are_bounded_and_sanitized(self) -> None:
        registration = _Connection(
            {"ok": True, "service": "school-csm-gateway-registration"}
        )
        client = GatewayProviderClient(
            parse_gateway_provider_config(_config_document()),
            connection_factory=lambda parsed, timeout: registration,
        )
        self.assertTrue(client.health_check())
        args, _kwargs = registration.request_args
        self.assertEqual(args[:2], ("GET", "/base/healthz"))

        public = _Connection(
            {"ok": True, "service": "school-csm-control-center"}
        )
        result = probe_public_gateway(
            "123456.csm.example.gov.ph",
            resolver=lambda host, port: [(host, port)],
            connection_factory=lambda host, timeout: public,
        )
        self.assertEqual(
            result,
            {"public_dns": True, "public_https": True, "host_routing": True},
        )
        request_args, request_kwargs = public.request_args
        self.assertEqual(request_args[:2], ("GET", "/healthz"))
        self.assertNotIn("Authorization", request_kwargs["headers"])
        self.assertNotIn("123456", repr(result))

        wrong_route = _Connection({"ok": True, "service": "another-service"})
        rejected = probe_public_gateway(
            "123456.csm.example.gov.ph",
            resolver=lambda host, port: [(host, port)],
            connection_factory=lambda host, timeout: wrong_route,
        )
        self.assertTrue(rejected["public_dns"])
        self.assertTrue(rejected["public_https"])
        self.assertFalse(rejected["host_routing"])
        with self.assertRaises(GatewayClientError):
            probe_public_gateway("https://123456.csm.example.gov.ph")

    def test_signed_authorization_binds_school_installation_host_and_expiry(self) -> None:
        try:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
            from cryptography.hazmat.primitives import serialization
        except ImportError:
            self.skipTest("cryptography is installed by the release dependency set")
        private_key = Ed25519PrivateKey.generate()
        public_bytes = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        config = parse_gateway_provider_config(_config_document(public_bytes))
        now = datetime.now(timezone.utc).replace(microsecond=0)
        payload = {
            "school_id": "123456",
            "installation_id": "11111111-1111-4111-8111-111111111111",
            "registration_id": "registration-1",
            "authorization_state": "active",
            "public_host": "123456.csm.example.gov.ph",
            "tunnel_id": "tunnel-1",
            "transaction_id": "transaction-1",
            "issued_at": (now - timedelta(minutes=1)).isoformat(),
            "expires_at": (now + timedelta(hours=1)).isoformat(),
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        envelope = {
            "key_id": "provider-2026",
            "payload": payload,
            "signature": base64.b64encode(private_key.sign(canonical)).decode("ascii"),
        }
        authorization = verify_authorization_envelope(
            envelope,
            config,
            expected_school_id="123456",
            expected_installation_id="11111111-1111-4111-8111-111111111111",
            now=now,
        )
        self.assertTrue(authorization.active)
        with self.assertRaises(GatewayClientError):
            verify_authorization_envelope(
                envelope,
                config,
                expected_school_id="654321",
                expected_installation_id="11111111-1111-4111-8111-111111111111",
                now=now,
            )

    def test_tunnel_token_is_environment_only_and_component_is_verified(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            executable = Path(temporary) / "cloudflared.exe"
            executable.write_bytes(b"verified tunnel component")
            expected = hashlib.sha256(executable.read_bytes()).hexdigest()
            calls = []

            def factory(arguments, **kwargs):
                kwargs["env"] = dict(kwargs["env"])
                calls.append((arguments, kwargs))
                return _Process()

            tunnel = CloudflaredTunnel(temporary, executable=executable, process_factory=factory)
            with patch(
                "school_csm_control_center.internet_gateway.tunnel.CLOUDFLARED_WINDOWS_AMD64_SHA256",
                expected,
            ):
                snapshot = tunnel.start("private-tunnel-token")
            self.assertEqual(snapshot.state, "connected")
            arguments, kwargs = calls[0]
            self.assertNotIn("private-tunnel-token", arguments)
            self.assertEqual(kwargs["env"]["TUNNEL_TOKEN"], "private-tunnel-token")
            tunnel.stop()


if __name__ == "__main__":
    unittest.main()
