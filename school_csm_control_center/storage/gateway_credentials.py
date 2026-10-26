"""Windows Credential Manager targets for device-specific Gateway secrets.

These targets are intentionally separate from the optional operator-owned
OpenAI API key.  They are machine/user-bound deployment material and are never
part of a School CSM backup or Server and Data migration package.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from school_csm_control_center.storage.windows_credential_store import (
    WindowsCredentialManager,
)


GATEWAY_TUNNEL_CREDENTIAL_TARGET = (
    "MoSSLab.SchoolCSMControlCenter.InternetGateway.TunnelCredential"
)
GATEWAY_INSTALLATION_SECRET_TARGET = (
    "MoSSLab.SchoolCSMControlCenter.InternetGateway.InstallationSecret"
)
GATEWAY_DEVICE_PRIVATE_KEY_TARGET = (
    "MoSSLab.SchoolCSMControlCenter.InternetGateway.DevicePrivateKey"
)
GATEWAY_DEFERRED_TRANSFER_RECEIPT_TARGET = (
    "MoSSLab.SchoolCSMControlCenter.InternetGateway.DeferredTransferReceipt"
)
GATEWAY_DIRECTORY_REQUEST_TARGET = (
    "MoSSLab.SchoolCSMControlCenter.InternetGateway.DirectoryRequestReceipt"
)
GATEWAY_CREDENTIAL_TARGETS = (
    GATEWAY_TUNNEL_CREDENTIAL_TARGET,
    GATEWAY_INSTALLATION_SECRET_TARGET,
    GATEWAY_DEVICE_PRIVATE_KEY_TARGET,
    GATEWAY_DEFERRED_TRANSFER_RECEIPT_TARGET,
    GATEWAY_DIRECTORY_REQUEST_TARGET,
)
GATEWAY_PILOT_MONITOR_TARGET = "MoSSLab.SchoolCSMControlCenter.InternetGateway.PilotMonitoring"
GATEWAY_CREDENTIAL_TARGETS += (GATEWAY_PILOT_MONITOR_TARGET,)


class GatewayCredentialStore:
    """Purpose-specific lifecycle for local Internet Gateway credentials."""

    tunnel_target = GATEWAY_TUNNEL_CREDENTIAL_TARGET
    installation_secret_target = GATEWAY_INSTALLATION_SECRET_TARGET
    device_private_key_target = GATEWAY_DEVICE_PRIVATE_KEY_TARGET
    deferred_transfer_receipt_target = GATEWAY_DEFERRED_TRANSFER_RECEIPT_TARGET
    directory_request_target = GATEWAY_DIRECTORY_REQUEST_TARGET

    def __init__(self, manager: Any | None = None) -> None:
        self._manager = manager

    def save_tunnel_credential(self, secret: str) -> None:
        self._save(self.tunnel_target, secret, "Tunnel credential")

    def load_tunnel_credential(self) -> str | None:
        return self._load(self.tunnel_target)

    def save_pilot_monitoring(self, receipt: Mapping[str, Any]) -> None:
        self._save(GATEWAY_PILOT_MONITOR_TARGET, json.dumps(dict(receipt)), "District pilot monitoring")

    def load_pilot_monitoring(self) -> dict[str, Any] | None:
        value = self._load(GATEWAY_PILOT_MONITOR_TARGET)
        return json.loads(value) if value else None

    def delete_tunnel_credential(self) -> bool:
        return bool(self._backend().delete(self.tunnel_target))

    def save_installation_secret(self, secret: str) -> None:
        self._save(self.installation_secret_target, secret, "Installation secret")

    def load_installation_secret(self) -> str | None:
        return self._load(self.installation_secret_target)

    def delete_installation_secret(self) -> bool:
        return bool(self._backend().delete(self.installation_secret_target))

    def save_device_private_key(self, secret: str) -> None:
        self._save(self.device_private_key_target, secret, "Device private key")

    def load_device_private_key(self) -> str | None:
        return self._load(self.device_private_key_target)

    def delete_device_private_key(self) -> bool:
        return bool(self._backend().delete(self.device_private_key_target))

    @staticmethod
    def deferred_transfer_receipt_digest(receipt: Mapping[str, Any]) -> str:
        serialized = json.dumps(
            dict(receipt),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def save_deferred_transfer_receipt(self, receipt: Mapping[str, Any]) -> str:
        """Protect a minimal one-time transfer receipt in Credential Manager."""

        if not isinstance(receipt, Mapping) or not receipt:
            raise ValueError("Deferred transfer receipt cannot be empty.")
        serialized = json.dumps(
            dict(receipt),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        if len(serialized.encode("utf-16-le")) > 2400:
            raise ValueError("Deferred transfer receipt exceeds the protected storage limit.")
        self._save(
            self.deferred_transfer_receipt_target,
            serialized,
            "Deferred transfer receipt",
        )
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def load_deferred_transfer_receipt(self) -> dict[str, Any] | None:
        serialized = self._load(self.deferred_transfer_receipt_target)
        if serialized is None:
            return None
        try:
            value = json.loads(serialized)
        except (TypeError, ValueError) as exc:
            raise ValueError("Deferred transfer receipt is invalid.") from exc
        if not isinstance(value, dict):
            raise ValueError("Deferred transfer receipt is invalid.")
        return value

    def delete_deferred_transfer_receipt(self) -> bool:
        return bool(self._backend().delete(self.deferred_transfer_receipt_target))

    def save_directory_request_receipt(
        self, receipt: Mapping[str, Any], *, retain_request_id: str | None = None,
    ) -> None:
        """Keep the durable receipt recoverable while a replacement is staged.

        Credential Manager replaces one value atomically. Retaining the receipt
        selected by the existing JSON file therefore covers a crash between the
        protected write and the subsequent JSON replacement. Only two receipts
        are retained, under the existing lifecycle-managed credential target.
        """

        previous = self.load_directory_request_receipt(retain_request_id)
        receipts = [dict(receipt)]
        if previous and previous.get("request_id") != receipt.get("request_id"):
            receipts.append(previous)
        serialized = json.dumps(
            {"schema_version": "1.0", "receipts": receipts},
            sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False,
        )
        if len(serialized.encode("utf-16-le")) > 2400:
            raise ValueError("Directory receipt exceeds the protected storage limit.")
        self._save(self.directory_request_target, serialized, "Directory request receipt")

    def load_directory_request_receipt(self, request_id: str | None = None) -> dict[str, Any] | None:
        serialized = self._load(self.directory_request_target)
        if serialized is None:
            return None
        value = json.loads(serialized)
        if not isinstance(value, dict):
            raise ValueError("Directory request receipt is invalid.")
        # Read the single-receipt format from earlier installations unchanged.
        if "receipts" not in value:
            receipts = [value]
        else:
            receipts = value.get("receipts")
            if value.get("schema_version") != "1.0" or not isinstance(receipts, list) or not 1 <= len(receipts) <= 2:
                raise ValueError("Directory request receipt is invalid.")
        if any(not isinstance(receipt, dict) for receipt in receipts):
            raise ValueError("Directory request receipt is invalid.")
        for receipt in receipts:
            if request_id is None or receipt.get("request_id") == request_id:
                return dict(receipt)
        return None

    def exists(self) -> dict[str, bool]:
        """Return presence flags without exposing any secret value."""

        return {
            "tunnel_credential": self.load_tunnel_credential() is not None,
            "installation_secret": self.load_installation_secret() is not None,
            "device_private_key": self.load_device_private_key() is not None,
            "deferred_transfer_receipt": (
                self._load(self.deferred_transfer_receipt_target) is not None
            ),
            "directory_request_receipt": self._load(self.directory_request_target) is not None,
            "pilot_monitoring": self._load(GATEWAY_PILOT_MONITOR_TARGET) is not None,
        }

    def delete_all(self) -> dict[str, bool]:
        """Explicitly remove device credentials and report each target result."""

        backend = self._backend()
        return {
            "tunnel_credential": bool(backend.delete(self.tunnel_target)),
            "installation_secret": bool(backend.delete(self.installation_secret_target)),
            "device_private_key": bool(backend.delete(self.device_private_key_target)),
            "deferred_transfer_receipt": bool(
                backend.delete(self.deferred_transfer_receipt_target)
            ),
            "directory_request_receipt": bool(backend.delete(self.directory_request_target)),
            "pilot_monitoring": bool(backend.delete(GATEWAY_PILOT_MONITOR_TARGET)),
        }

    def _save(self, target: str, secret: str, label: str) -> None:
        value = str(secret or "").strip()
        if not value:
            raise ValueError(f"{label} cannot be empty.")
        self._backend().write(target, value)

    def _load(self, target: str) -> str | None:
        value = self._backend().read(target)
        text = str(value or "").strip()
        return text or None

    def _backend(self) -> Any:
        if self._manager is None:
            self._manager = WindowsCredentialManager()
        return self._manager
