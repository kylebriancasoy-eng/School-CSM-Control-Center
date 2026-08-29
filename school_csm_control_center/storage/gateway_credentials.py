"""Windows Credential Manager targets for device-specific Gateway secrets.

These targets are intentionally separate from the optional operator-owned
OpenAI API key.  They are machine/user-bound deployment material and are never
part of a School CSM backup or Server and Data migration package.
"""

from __future__ import annotations

from typing import Any

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
GATEWAY_CREDENTIAL_TARGETS = (
    GATEWAY_TUNNEL_CREDENTIAL_TARGET,
    GATEWAY_INSTALLATION_SECRET_TARGET,
    GATEWAY_DEVICE_PRIVATE_KEY_TARGET,
)


class GatewayCredentialStore:
    """Purpose-specific lifecycle for local Internet Gateway credentials."""

    tunnel_target = GATEWAY_TUNNEL_CREDENTIAL_TARGET
    installation_secret_target = GATEWAY_INSTALLATION_SECRET_TARGET
    device_private_key_target = GATEWAY_DEVICE_PRIVATE_KEY_TARGET

    def __init__(self, manager: Any | None = None) -> None:
        self._manager = manager

    def save_tunnel_credential(self, secret: str) -> None:
        self._save(self.tunnel_target, secret, "Tunnel credential")

    def load_tunnel_credential(self) -> str | None:
        return self._load(self.tunnel_target)

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

    def exists(self) -> dict[str, bool]:
        """Return presence flags without exposing any secret value."""

        return {
            "tunnel_credential": self.load_tunnel_credential() is not None,
            "installation_secret": self.load_installation_secret() is not None,
            "device_private_key": self.load_device_private_key() is not None,
        }

    def delete_all(self) -> dict[str, bool]:
        """Explicitly remove device credentials and report each target result."""

        backend = self._backend()
        return {
            "tunnel_credential": bool(backend.delete(self.tunnel_target)),
            "installation_secret": bool(backend.delete(self.installation_secret_target)),
            "device_private_key": bool(backend.delete(self.device_private_key_target)),
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
