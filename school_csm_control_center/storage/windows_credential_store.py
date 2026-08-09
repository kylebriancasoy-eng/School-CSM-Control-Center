"""Windows Credential Manager storage for the operator's OpenAI API key."""

from __future__ import annotations

import os
from typing import Any


OPENAI_API_KEY_CREDENTIAL_TARGET = (
    "MoSSLab.SchoolCSMControlCenter.OpenAIApiKey"
)


class CredentialStoreError(RuntimeError):
    """Raised when the operating-system credential vault cannot be used."""


class CredentialStoreUnavailableError(CredentialStoreError):
    """Raised outside Windows or when Credential Manager is unavailable."""


class WindowsCredentialManager:
    """Minimal generic-credential wrapper with no third-party dependency."""

    CRED_TYPE_GENERIC = 1
    CRED_PERSIST_LOCAL_MACHINE = 2
    ERROR_NOT_FOUND = 1168
    MAX_BLOB_BYTES = 2560

    def __init__(self) -> None:
        if os.name != "nt":
            raise CredentialStoreUnavailableError(
                "Windows Credential Manager is available only on Windows."
            )

    def write(self, target: str, secret: str) -> None:
        ctypes, wintypes, credential_type, advapi32 = self._api()
        encoded = secret.encode("utf-16-le")
        if not encoded or len(encoded) > self.MAX_BLOB_BYTES:
            raise CredentialStoreError("The API key cannot be stored in Windows Credential Manager.")
        blob = (ctypes.c_ubyte * len(encoded)).from_buffer_copy(encoded)
        credential = credential_type()
        credential.Flags = 0
        credential.Type = self.CRED_TYPE_GENERIC
        credential.TargetName = target
        credential.Comment = "School CSM Control Center optional OpenAI API access"
        credential.CredentialBlobSize = len(encoded)
        credential.CredentialBlob = ctypes.cast(blob, ctypes.POINTER(ctypes.c_ubyte))
        credential.Persist = self.CRED_PERSIST_LOCAL_MACHINE
        credential.AttributeCount = 0
        credential.Attributes = None
        credential.TargetAlias = None
        credential.UserName = "School CSM Control Center operator"
        try:
            if not advapi32.CredWriteW(ctypes.byref(credential), 0):
                raise ctypes.WinError(ctypes.get_last_error())
        except OSError:
            raise CredentialStoreError(
                "Windows Credential Manager could not save the OpenAI API key."
            ) from None
        finally:
            for index in range(len(blob)):
                blob[index] = 0

    def read(self, target: str) -> str | None:
        ctypes, _wintypes, credential_type, advapi32 = self._api()
        pointer = ctypes.POINTER(credential_type)()
        if not advapi32.CredReadW(
            target,
            self.CRED_TYPE_GENERIC,
            0,
            ctypes.byref(pointer),
        ):
            error = ctypes.get_last_error()
            if error == self.ERROR_NOT_FOUND:
                return None
            raise CredentialStoreError(
                "Windows Credential Manager could not read the OpenAI API key."
            )
        try:
            credential = pointer.contents
            if not credential.CredentialBlob or credential.CredentialBlobSize <= 0:
                return None
            raw = ctypes.string_at(
                credential.CredentialBlob,
                credential.CredentialBlobSize,
            )
            try:
                return raw.decode("utf-16-le")
            except UnicodeDecodeError:
                raise CredentialStoreError(
                    "The saved OpenAI API key is unreadable."
                ) from None
        finally:
            advapi32.CredFree(pointer)

    def delete(self, target: str) -> bool:
        ctypes, _wintypes, _credential_type, advapi32 = self._api()
        if advapi32.CredDeleteW(target, self.CRED_TYPE_GENERIC, 0):
            return True
        error = ctypes.get_last_error()
        if error == self.ERROR_NOT_FOUND:
            return False
        raise CredentialStoreError(
            "Windows Credential Manager could not remove the OpenAI API key."
        )

    def _api(self) -> tuple[Any, Any, Any, Any]:
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
            return ctypes, wintypes, CREDENTIALW, advapi32
        except (ImportError, AttributeError, OSError):
            raise CredentialStoreUnavailableError(
                "Windows Credential Manager is unavailable."
            ) from None


class OpenAIApiKeyCredentialStore:
    """Purpose-specific API-key store used only by the assisted action."""

    target = OPENAI_API_KEY_CREDENTIAL_TARGET

    def __init__(self, manager: Any | None = None) -> None:
        self._manager = manager

    def save(self, api_key: str) -> None:
        key = str(api_key or "").strip()
        if not key:
            raise ValueError("OpenAI API key cannot be empty.")
        self._backend().write(self.target, key)

    def load(self) -> str | None:
        value = self._backend().read(self.target)
        return str(value) if value else None

    def exists(self) -> bool:
        return self.load() is not None

    def delete(self) -> bool:
        """Explicitly remove the key; normal update/uninstall must not call this."""

        return bool(self._backend().delete(self.target))

    def _backend(self) -> Any:
        if self._manager is None:
            self._manager = WindowsCredentialManager()
        return self._manager
