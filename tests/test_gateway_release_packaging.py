from __future__ import annotations

import hashlib
import base64
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from school_csm_control_center.internet_gateway.tunnel import (
    CLOUDFLARED_VERSION,
    CLOUDFLARED_WINDOWS_AMD64_SHA256,
)
from school_csm_control_center.internet_gateway.config import (
    load_gateway_provider_config,
)
from scripts import fetch_cloudflared


class _DownloadResponse:
    def __init__(self, body: bytes, *, declared_length: str | None = None) -> None:
        self._body = body
        self._offset = 0
        self.headers = {
            "Content-Length": declared_length
            if declared_length is not None
            else str(len(body))
        }

    def __enter__(self):
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, size: int) -> bytes:
        chunk = self._body[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk


class GatewayReleasePackagingTests(unittest.TestCase):
    @staticmethod
    def _provider_document() -> dict:
        return {
            "schema_version": "1.0",
            "provider_name": "Division Gateway",
            "registration_service_url": "https://register.example.gov.ph",
            "managed_domain": "csm.example.gov.ph",
            "authorization_signing_keys": [
                {
                    "key_id": "provider-2026",
                    "public_key_base64": base64.b64encode(b"k" * 32).decode("ascii"),
                }
            ],
            "trusted_proxy_addresses": ["127.0.0.1", "::1"],
        }

    def test_durable_program_data_provider_config_and_sidecar_precedence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            install_root = root / "installed-app"
            install_root.mkdir()
            durable = (
                root
                / "program-data"
                / "MoSSLab"
                / "School CSM Control Center"
                / "Configuration"
                / "internet_gateway_provider.json"
            )
            durable.parent.mkdir(parents=True)
            durable.write_text(
                json.dumps(self._provider_document()), encoding="utf-8"
            )
            with patch.dict(os.environ, {"PROGRAMDATA": str(root / "program-data")}):
                loaded = load_gateway_provider_config(install_root, required=True)
                self.assertEqual(loaded.managed_domain, "csm.example.gov.ph")

                sidecar_document = self._provider_document()
                sidecar_document["managed_domain"] = "replacement.example.gov.ph"
                (install_root / "internet_gateway_provider.json").write_text(
                    json.dumps(sidecar_document), encoding="utf-8"
                )
                loaded = load_gateway_provider_config(install_root, required=True)
                self.assertEqual(loaded.managed_domain, "replacement.example.gov.ph")

    def test_build_fetch_pin_matches_runtime_integrity_pin(self) -> None:
        self.assertEqual(fetch_cloudflared.VERSION, CLOUDFLARED_VERSION)
        self.assertEqual(
            fetch_cloudflared.SHA256,
            CLOUDFLARED_WINDOWS_AMD64_SHA256,
        )
        self.assertEqual(
            fetch_cloudflared.DOWNLOAD_URL,
            "https://github.com/cloudflare/cloudflared/releases/download/"
            f"{CLOUDFLARED_VERSION}/cloudflared-windows-amd64.exe",
        )

    def test_verified_existing_component_is_reused_without_network(self) -> None:
        body = b"verified-component"
        expected = hashlib.sha256(body).hexdigest()
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "cloudflared.exe"
            destination.write_bytes(body)
            with patch.object(fetch_cloudflared, "urlopen") as download:
                result = fetch_cloudflared.fetch(
                    destination,
                    url="https://example.invalid/cloudflared.exe",
                    expected_sha256=expected,
                )
            self.assertEqual(result, destination.resolve())
            download.assert_not_called()

    def test_download_is_verified_before_atomic_replacement(self) -> None:
        original = b"previous-invalid-component"
        body = b"new-verified-component"
        expected = hashlib.sha256(body).hexdigest()
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "cloudflared.exe"
            destination.write_bytes(original)
            response = _DownloadResponse(body)
            with patch.object(fetch_cloudflared, "urlopen", return_value=response):
                fetch_cloudflared.fetch(
                    destination,
                    url="https://example.invalid/cloudflared.exe",
                    expected_sha256=expected,
                )
            self.assertEqual(destination.read_bytes(), body)
            self.assertEqual(list(destination.parent.glob(".cloudflared-*.download")), [])

    def test_bad_download_does_not_replace_existing_component(self) -> None:
        original = b"previous-component"
        body = b"tampered-download"
        expected = hashlib.sha256(b"expected-download").hexdigest()
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "cloudflared.exe"
            destination.write_bytes(original)
            response = _DownloadResponse(body)
            with patch.object(fetch_cloudflared, "urlopen", return_value=response):
                with self.assertRaisesRegex(RuntimeError, "failed SHA-256 verification"):
                    fetch_cloudflared.fetch(
                        destination,
                        url="https://example.invalid/cloudflared.exe",
                        expected_sha256=expected,
                    )
            self.assertEqual(destination.read_bytes(), original)
            self.assertEqual(list(destination.parent.glob(".cloudflared-*.download")), [])

    def test_declared_oversize_download_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "cloudflared.exe"
            response = _DownloadResponse(
                b"unused",
                declared_length=str(fetch_cloudflared.MAX_BYTES + 1),
            )
            with patch.object(fetch_cloudflared, "urlopen", return_value=response):
                with self.assertRaisesRegex(RuntimeError, "exceeds the build size limit"):
                    fetch_cloudflared.fetch(
                        destination,
                        url="https://example.invalid/cloudflared.exe",
                    )
            self.assertFalse(destination.exists())
            self.assertEqual(list(destination.parent.glob(".cloudflared-*.download")), [])


if __name__ == "__main__":
    unittest.main()
