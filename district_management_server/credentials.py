"""Windows Credential Manager boundary for district-server secrets."""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
import hashlib
import os
import re
import secrets
from typing import Any, Protocol

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


AUTHORIZATION_PRIVATE_KEY_TARGET = (
    "MoSSLab.SchoolCSMDistrictManagementServer.AuthorizationPrivateKeyBase64"
)
HANDOFF_ENCRYPTION_KEY_TARGET = (
    "MoSSLab.SchoolCSMDistrictManagementServer.HandoffEncryptionKeyBase64"
)
DISTRICT_ADMIN_DIGEST_TARGET = (
    "MoSSLab.SchoolCSMDistrictManagementServer.DistrictAdminTokenSha256"
)
CLOUDFLARE_API_TOKEN_TARGET = (
    "MoSSLab.SchoolCSMDistrictManagementServer.CloudflareApiToken"
)
MANAGEMENT_TUNNEL_TOKEN_TARGET = (
    "MoSSLab.SchoolCSMDistrictManagementServer.ManagementTunnelToken"
)

ALL_TARGETS = (
    AUTHORIZATION_PRIVATE_KEY_TARGET,
    HANDOFF_ENCRYPTION_KEY_TARGET,
    DISTRICT_ADMIN_DIGEST_TARGET,
    CLOUDFLARE_API_TOKEN_TARGET,
    MANAGEMENT_TUNNEL_TOKEN_TARGET,
)


class CredentialError(RuntimeError):
    """Raised when protected configuration is missing or unreadable."""


class CredentialBackend(Protocol):
    def read(self, target: str) -> str | None: ...

    def write(self, target: str, secret: str) -> None: ...

    def delete(self, target: str) -> bool: ...


class WindowsCredentialManager:
    """Minimal generic-credential wrapper with no third-party dependency."""

    CRED_TYPE_GENERIC = 1
    CRED_PERSIST_LOCAL_MACHINE = 2
    ERROR_NOT_FOUND = 1168
    MAX_BLOB_BYTES = 2560

    def __init__(self) -> None:
        if os.name != "nt":
            raise CredentialError(
                "District server credentials are available only on Windows."
            )

    def write(self, target: str, secret: str) -> None:
        ctypes, credential_type, advapi32 = self._api()
        encoded = str(secret).encode("utf-16-le")
        if not encoded or len(encoded) > self.MAX_BLOB_BYTES:
            raise CredentialError("The protected credential has an invalid size.")
        blob = (ctypes.c_ubyte * len(encoded)).from_buffer_copy(encoded)
        credential = credential_type()
        credential.Flags = 0
        credential.Type = self.CRED_TYPE_GENERIC
        credential.TargetName = str(target)
        credential.Comment = "School CSM District Management Server protected credential"
        credential.CredentialBlobSize = len(encoded)
        credential.CredentialBlob = ctypes.cast(blob, ctypes.POINTER(ctypes.c_ubyte))
        credential.Persist = self.CRED_PERSIST_LOCAL_MACHINE
        credential.AttributeCount = 0
        credential.Attributes = None
        credential.TargetAlias = None
        credential.UserName = "School CSM District Management Server"
        try:
            if not advapi32.CredWriteW(ctypes.byref(credential), 0):
                raise ctypes.WinError(ctypes.get_last_error())
        except OSError:
            raise CredentialError(
                "Windows Credential Manager could not save the protected credential."
            ) from None
        finally:
            for index in range(len(blob)):
                blob[index] = 0

    def read(self, target: str) -> str | None:
        ctypes, credential_type, advapi32 = self._api()
        pointer = ctypes.POINTER(credential_type)()
        if not advapi32.CredReadW(
            str(target), self.CRED_TYPE_GENERIC, 0, ctypes.byref(pointer)
        ):
            error = ctypes.get_last_error()
            if error == self.ERROR_NOT_FOUND:
                return None
            raise CredentialError(
                "Windows Credential Manager could not read a protected credential."
            )
        try:
            credential = pointer.contents
            if not credential.CredentialBlob or credential.CredentialBlobSize <= 0:
                return None
            raw = ctypes.string_at(
                credential.CredentialBlob, credential.CredentialBlobSize
            )
            try:
                return raw.decode("utf-16-le")
            except UnicodeDecodeError:
                raise CredentialError("A protected credential is unreadable.") from None
        finally:
            advapi32.CredFree(pointer)

    def delete(self, target: str) -> bool:
        ctypes, _credential_type, advapi32 = self._api()
        if advapi32.CredDeleteW(str(target), self.CRED_TYPE_GENERIC, 0):
            return True
        error = ctypes.get_last_error()
        if error == self.ERROR_NOT_FOUND:
            return False
        raise CredentialError(
            "Windows Credential Manager could not remove a protected credential."
        )

    @staticmethod
    def _api() -> tuple[Any, Any, Any]:
        try:
            import ctypes
            from ctypes import wintypes

            class FILETIME(ctypes.Structure):
                _fields_ = (
                    ("dwLowDateTime", wintypes.DWORD),
                    ("dwHighDateTime", wintypes.DWORD),
                )

            class CREDENTIALW(ctypes.Structure):
                _fields_ = (
                    ("Flags", wintypes.DWORD),
                    ("Type", wintypes.DWORD),
                    ("TargetName", wintypes.LPWSTR),
                    ("Comment", wintypes.LPWSTR),
                    ("LastWritten", FILETIME),
                    ("CredentialBlobSize", wintypes.DWORD),
                    ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                    ("Persist", wintypes.DWORD),
                    ("AttributeCount", wintypes.DWORD),
                    ("Attributes", wintypes.LPVOID),
                    ("TargetAlias", wintypes.LPWSTR),
                    ("UserName", wintypes.LPWSTR),
                )

            advapi32 = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
            advapi32.CredWriteW.argtypes = (
                ctypes.POINTER(CREDENTIALW),
                wintypes.DWORD,
            )
            advapi32.CredWriteW.restype = wintypes.BOOL
            advapi32.CredReadW.argtypes = (
                wintypes.LPCWSTR,
                wintypes.DWORD,
                wintypes.DWORD,
                ctypes.POINTER(ctypes.POINTER(CREDENTIALW)),
            )
            advapi32.CredReadW.restype = wintypes.BOOL
            advapi32.CredDeleteW.argtypes = (
                wintypes.LPCWSTR,
                wintypes.DWORD,
                wintypes.DWORD,
            )
            advapi32.CredDeleteW.restype = wintypes.BOOL
            advapi32.CredFree.argtypes = (wintypes.LPVOID,)
            advapi32.CredFree.restype = None
            return ctypes, CREDENTIALW, advapi32
        except (ImportError, AttributeError, OSError):
            raise CredentialError("Windows Credential Manager is unavailable.") from None


@dataclass(frozen=True)
class DistrictSecretMaterial:
    """Validated secrets whose representation never prints their values."""

    authorization_private_key_base64: str = field(repr=False)
    handoff_encryption_key_base64: str = field(repr=False)
    district_admin_token_sha256: str = field(repr=False)
    cloudflare_api_token: str = field(repr=False)
    management_tunnel_token: str = field(repr=False)


class DistrictCredentialStore:
    def __init__(self, backend: CredentialBackend | None = None) -> None:
        self._backend = backend

    def load(self) -> DistrictSecretMaterial:
        private_key = self._required(AUTHORIZATION_PRIVATE_KEY_TARGET)
        handoff_key = self._required(HANDOFF_ENCRYPTION_KEY_TARGET)
        digest = self._required(DISTRICT_ADMIN_DIGEST_TARGET).casefold()
        api_token = self._required(CLOUDFLARE_API_TOKEN_TARGET)
        tunnel_token = self._required(MANAGEMENT_TUNNEL_TOKEN_TARGET)
        self._validate_base64_key(private_key, "authorization signing")
        self._validate_base64_key(handoff_key, "credential handoff")
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise CredentialError(
                "The protected district administrator token digest is invalid."
            )
        if not 20 <= len(api_token) <= 2048 or any(
            character in api_token for character in "\r\n\0"
        ):
            raise CredentialError("The protected Cloudflare API token is invalid.")
        if not 20 <= len(tunnel_token) <= 4096 or any(
            character in tunnel_token for character in "\r\n\0"
        ):
            raise CredentialError("The protected management tunnel token is invalid.")
        return DistrictSecretMaterial(
            private_key, handoff_key, digest, api_token, tunnel_token
        )

    def save(self, target: str, value: str) -> None:
        if target not in ALL_TARGETS:
            raise CredentialError("The protected credential target is not allowed.")
        self._manager().write(target, str(value))

    def has_any(self) -> bool:
        manager = self._manager()
        return any(manager.read(target) is not None for target in ALL_TARGETS)

    def provision_initial(
        self,
        *,
        cloudflare_api_token: str,
        management_tunnel_token: str,
    ) -> tuple[str, str]:
        """Create a new matched secret set and return token + public signing key.

        The raw district administrator token is returned exactly once and only
        its SHA-256 digest is stored. Existing material is never overwritten.
        """

        manager = self._manager()
        if any(manager.read(target) is not None for target in ALL_TARGETS):
            raise CredentialError(
                "Protected district credentials already exist; setup will not overwrite them."
            )
        api_token = str(cloudflare_api_token).strip()
        connector = str(management_tunnel_token).strip()
        if not 20 <= len(api_token) <= 2048 or any(c in api_token for c in "\r\n\0"):
            raise CredentialError("The Cloudflare API token is invalid.")
        if not 20 <= len(connector) <= 4096 or any(c in connector for c in "\r\n\0"):
            raise CredentialError("The management tunnel token is invalid.")

        private_key = Ed25519PrivateKey.generate()
        private_raw = private_key.private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        )
        public_raw = private_key.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
        private_b64 = base64.b64encode(private_raw).decode("ascii")
        public_b64 = base64.b64encode(public_raw).decode("ascii")
        handoff_b64 = base64.b64encode(os.urandom(32)).decode("ascii")
        administrator_token = secrets.token_urlsafe(48)
        digest = hashlib.sha256(administrator_token.encode("utf-8")).hexdigest()
        values = {
            AUTHORIZATION_PRIVATE_KEY_TARGET: private_b64,
            HANDOFF_ENCRYPTION_KEY_TARGET: handoff_b64,
            DISTRICT_ADMIN_DIGEST_TARGET: digest,
            CLOUDFLARE_API_TOKEN_TARGET: api_token,
            MANAGEMENT_TUNNEL_TOKEN_TARGET: connector,
        }
        written: list[str] = []
        try:
            for target, value in values.items():
                manager.write(target, value)
                written.append(target)
        except Exception:
            for target in reversed(written):
                try:
                    manager.delete(target)
                except Exception:
                    pass
            raise
        return administrator_token, public_b64

    def remove_all(self) -> None:
        manager = self._manager()
        for target in ALL_TARGETS:
            manager.delete(target)

    def restore_initial(self, material: DistrictSecretMaterial) -> None:
        """Restore one validated secret set only onto an unconfigured host."""

        if not isinstance(material, DistrictSecretMaterial):
            raise CredentialError("District recovery credentials are invalid.")
        self._validate_base64_key(
            material.authorization_private_key_base64, "authorization signing"
        )
        self._validate_base64_key(
            material.handoff_encryption_key_base64, "credential handoff"
        )
        digest = str(material.district_admin_token_sha256).strip().casefold()
        api_token = str(material.cloudflare_api_token).strip()
        connector = str(material.management_tunnel_token).strip()
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise CredentialError("The recovered district administrator digest is invalid.")
        if not 20 <= len(api_token) <= 2048 or any(c in api_token for c in "\r\n\0"):
            raise CredentialError("The recovered Cloudflare API token is invalid.")
        if not 20 <= len(connector) <= 4096 or any(c in connector for c in "\r\n\0"):
            raise CredentialError("The recovered management tunnel token is invalid.")
        manager = self._manager()
        if any(manager.read(target) is not None for target in ALL_TARGETS):
            raise CredentialError(
                "Protected district credentials already exist; recovery will not overwrite them."
            )
        values = {
            AUTHORIZATION_PRIVATE_KEY_TARGET: material.authorization_private_key_base64,
            HANDOFF_ENCRYPTION_KEY_TARGET: material.handoff_encryption_key_base64,
            DISTRICT_ADMIN_DIGEST_TARGET: digest,
            CLOUDFLARE_API_TOKEN_TARGET: api_token,
            MANAGEMENT_TUNNEL_TOKEN_TARGET: connector,
        }
        written: list[str] = []
        try:
            for target, value in values.items():
                manager.write(target, str(value))
                written.append(target)
        except Exception:
            for target in reversed(written):
                try:
                    manager.delete(target)
                except Exception:
                    pass
            raise

    def rotate_administrator_token(self) -> str:
        """Replace only the stored administrator digest and return the token once."""

        # Validate the complete credential set first so a damaged deployment is
        # not partially "repaired" by rotating only one field.
        self.load()
        administrator_token = secrets.token_urlsafe(48)
        digest = hashlib.sha256(administrator_token.encode("utf-8")).hexdigest()
        self._manager().write(DISTRICT_ADMIN_DIGEST_TARGET, digest)
        return administrator_token

    def _required(self, target: str) -> str:
        value = self._manager().read(target)
        if value is None or not str(value).strip():
            label = target.rsplit(".", 1)[-1]
            raise CredentialError(
                f"The protected {label} credential has not been provisioned."
            )
        return str(value).strip()

    @staticmethod
    def _validate_base64_key(value: str, label: str) -> None:
        try:
            material = base64.b64decode(value, validate=True)
        except (ValueError, TypeError):
            material = b""
        if len(material) != 32:
            raise CredentialError(f"The protected {label} key is invalid.")

    def _manager(self) -> CredentialBackend:
        if self._backend is None:
            self._backend = WindowsCredentialManager()
        return self._backend
