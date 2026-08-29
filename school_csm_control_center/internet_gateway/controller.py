"""Desktop lifecycle bridge for the optional Internet Gateway.

Importing or constructing this controller never performs network access.  The
operator completes activation-code and passkey ceremonies in the provider's
HTTPS ``/manage`` page; the desktop accepts only a short-lived, one-use
completion code.  Local-only operation remains the default.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
import base64
import binascii
import json
from pathlib import Path
import secrets
from threading import RLock, Thread
from typing import Any, Callable, Mapping
from urllib.parse import quote, urlencode, urlparse, urlunparse

from PySide6.QtCore import QObject, Signal


REGISTRATION_LABELS = {
    "not_configured": "Not Configured",
    "registration_required": "Registration Required",
    "registered": "Registered",
    "transfer_pending": "Transfer Pending",
    "authorization_transferred": "Authorization Transferred",
    "revoked": "Revoked",
    "registration_error": "Registration Error",
}

GATEWAY_LABELS = {
    "not_configured": "Not Configured",
    "stopped": "Stopped",
    "connecting": "Connecting",
    "connected": "Connected",
    "disconnected": "Disconnected",
    "authorization_transferred": "Authorization Transferred",
    "revoked": "Revoked",
    "error": "Error",
}

AUTHORIZATION_LABELS = {
    "unregistered": "Not Registered",
    "active": "This Device",
    "transfer_pending": "Transfer Pending",
    "transferred": "Transferred",
    "revoked": "Revoked",
    "retired": "Retired",
    "registration_error": "Registration Error",
    "unknown_offline": "Not Checked (Offline)",
}

RETIRED_AUTHORIZATION_STATES = frozenset({"transferred", "revoked", "retired"})


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _normalize_host(value: Any) -> str:
    host = str(value or "").strip().casefold()
    if "://" in host:
        host = host.split("://", 1)[1]
    return host.split("/", 1)[0].rstrip(".")


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if is_dataclass(value):
        return asdict(value)
    data = getattr(value, "__dict__", None)
    return dict(data) if isinstance(data, Mapping) else {}


def _callable_attr(value: Any, *names: str) -> Callable[..., Any] | None:
    for name in names:
        candidate = getattr(value, name, None)
        if callable(candidate):
            return candidate
    return None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _decode_migration_key(value: Any) -> bytes:
    """Decode a 256-bit transfer key without retaining a normalized copy."""

    text = "".join(str(value or "").split())
    if not text:
        raise ValueError("Enter the migration key supplied for this encrypted package.")
    decoded: bytes
    if len(text) == 64:
        try:
            decoded = bytes.fromhex(text)
        except ValueError as exc:
            raise ValueError(
                "The migration key must be a 64-character hexadecimal or valid Base64 key."
            ) from exc
    else:
        try:
            decoded = base64.b64decode(text, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError(
                "The migration key must be a 64-character hexadecimal or valid Base64 key."
            ) from exc
    if len(decoded) != 32:
        raise ValueError("The migration key must contain exactly 32 bytes of key material.")
    return decoded


class InternetGatewayController(QObject):
    """Coordinate provider state, Credential Manager, and the tunnel process."""

    state_changed = Signal(object)
    public_urls_changed = Signal(str, str)
    operation_progress = Signal(object)
    diagnostics_ready = Signal(object)
    retirement_confirmed = Signal(object)
    notice_requested = Signal(str, str)

    def __init__(
        self,
        project_root: str | Path,
        *,
        local_settings_provider: Callable[[], Mapping[str, Any]],
        state_store: Any | None = None,
        client: Any | None = None,
        tunnel: Any | None = None,
        credentials: Any | None = None,
        migration: Any | None = None,
        backup: Any | None = None,
        provider_config: Any | None = None,
        public_health_probe: Callable[[str], Mapping[str, Any]] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.project_root = Path(project_root).expanduser().resolve()
        self._local_settings_provider = local_settings_provider
        self._provider_config = provider_config or getattr(client, "config", None)
        self._state_store = state_store if state_store is not None else self._default_state_store()
        self._client = client
        self._tunnel = tunnel
        self._credentials = credentials if credentials is not None else self._default_credentials()
        self._migration = migration
        self._backup = backup
        if public_health_probe is None:
            try:
                from school_csm_control_center.internet_gateway.client import (
                    probe_public_gateway,
                )

                public_health_probe = probe_public_gateway
            except ImportError:
                public_health_probe = None
        self._public_health_probe = public_health_probe
        self._operation = ""
        self._operation_lock = RLock()
        self._last_error = ""
        # Persisted ACTIVE state is useful UI context, but it is not authority
        # to widen the local listener in a new process. Only a successful
        # provider check in this running session can enable the tunnel origin.
        self._authorization_verified_this_session = False
        self._state = self._initial_state()
        self.reload()

    def _default_state_store(self) -> Any | None:
        try:
            from school_csm_control_center.storage.gateway_state import GatewayStateStore

            return GatewayStateStore(
                self.project_root,
                provider_config=self._provider_config,
            )
        except (ImportError, OSError, TypeError, ValueError):
            return None

    @staticmethod
    def _default_credentials() -> Any | None:
        try:
            from school_csm_control_center.storage.gateway_credentials import GatewayCredentialStore

            return GatewayCredentialStore()
        except (ImportError, OSError, TypeError, ValueError):
            return None

    @staticmethod
    def _initial_state() -> dict[str, Any]:
        return {
            "registration_state": "not_configured",
            "gateway_state": "not_configured",
            "authorization_state": "unregistered",
            "installation_id": "",
            "school_id": "",
            "local_school_id": "",
            "registered_school_name": "",
            "registration_id": "",
            "public_host": "",
            "tunnel_id": "",
            "survey_url": "",
            "scanner_url": "",
            "authorized_this_device": False,
            "last_authorization_check_at": "",
            "detail": "Internet Gateway is optional and has not been configured.",
            "operation": "",
            "retirement": None,
            "trusted_proxy_ips": [],
            "local_server_running": False,
            "survey_form_status": "offline",
            "scanner_remote_enabled": True,
            "provider_available": False,
        }

    def state(self) -> dict[str, Any]:
        return deepcopy(self._state)

    @property
    def configured(self) -> bool:
        return bool(
            self._state.get("registration_state") == "registered"
            and self._state.get("public_host")
        )

    @property
    def connected(self) -> bool:
        return self._state.get("gateway_state") == "connected"

    @property
    def retired(self) -> bool:
        return bool(
            self._state.get("retirement")
            or self._state.get("authorization_state") in RETIRED_AUTHORIZATION_STATES
        )

    @property
    def provider_available(self) -> bool:
        return bool(
            self._client is not None
            and self._provider_config is not None
            and str(getattr(self._provider_config, "service_url", "")).startswith("https://")
        )

    @property
    def operation_in_progress(self) -> bool:
        with self._operation_lock:
            return bool(self._operation)

    @property
    def authorization_verified_this_session(self) -> bool:
        return bool(self._authorization_verified_this_session)

    def reload(self) -> dict[str, Any]:
        document: Mapping[str, Any] = {}
        self._last_error = ""
        loader = _callable_attr(self._state_store, "load")
        if loader is not None:
            try:
                loaded = loader()
                if isinstance(loaded, Mapping):
                    document = loaded
            except Exception as exc:
                self._last_error = _clean_text(exc)

        registration = document.get("registration")
        registration = dict(registration) if isinstance(registration, Mapping) else {}
        gateway_status = str(document.get("gateway_status") or "not_configured").casefold()
        if gateway_status == "connected":
            snapshot = _callable_attr(self._tunnel, "snapshot")
            runtime_status = ""
            if snapshot is not None:
                try:
                    runtime_status = str(
                        _mapping(snapshot()).get("state") or ""
                    ).casefold()
                except Exception:
                    runtime_status = ""
            if runtime_status not in {"connected", "running", "started"}:
                # A persisted label cannot prove that this process owns a live
                # tunnel. Startup sequencing must reconnect explicitly.
                gateway_status = "disconnected"
        authorization = str(document.get("authorization_status") or "UNREGISTERED").casefold()
        public_host = _normalize_host(
            registration.get("public_hostname") or registration.get("public_host")
        )
        school_id = _clean_text(registration.get("school_id"))

        state = self._initial_state()
        state.update(
            installation_id=_clean_text(document.get("installation_id")),
            registration_state="registered" if school_id and public_host else "not_configured",
            gateway_state=gateway_status if gateway_status in GATEWAY_LABELS else "error",
            authorization_state=authorization if authorization in AUTHORIZATION_LABELS else "registration_error",
            school_id=school_id,
            registration_id=_clean_text(registration.get("registration_id")),
            public_host=public_host,
            tunnel_id=_clean_text(registration.get("tunnel_id")),
            last_authorization_check_at=str(document.get("last_authorization_check_at") or ""),
            detail=_clean_text(document.get("status_detail")),
            retirement=document.get("retirement"),
            trusted_proxy_ips=list(
                getattr(self._provider_config, "trusted_proxy_addresses", ()) or ()
            ),
            provider_available=self.provider_available,
        )
        if state["retirement"]:
            retirement_status = str(
                _mapping(state["retirement"]).get("retirement_status") or "revoked"
            ).casefold()
            state["authorization_state"] = (
                retirement_status
                if retirement_status in RETIRED_AUTHORIZATION_STATES
                else "revoked"
            )
            state["gateway_state"] = "revoked"
            state["registration_state"] = "revoked"
        if self._last_error:
            state["registration_state"] = "registration_error"
            state["gateway_state"] = "error"
            state["authorization_state"] = "registration_error"
            state["detail"] = (
                "Internet Gateway authorization state could not be read. "
                "Internet access is disabled; Local-Only operation remains available."
            )
        self._state = state
        self.refresh_local_settings(emit=False)
        self._emit_state()
        return self.state()

    def refresh_local_settings(self, *, emit: bool = True) -> dict[str, Any]:
        local = dict(self._local_settings_provider() or {})
        local_school_id = _clean_text(local.get("school_id"))
        registered_school_id = _clean_text(self._state.get("school_id"))
        access_key = str(local.get("public_access_key") or "").strip()

        if registered_school_id and local_school_id and registered_school_id != local_school_id:
            self._state.update(
                registration_state="registration_error",
                gateway_state="error",
                authorization_state="registration_error",
                authorized_this_device=False,
                detail=(
                    "School Information does not match this device's registered School ID. "
                    "Internet access is disabled until the mismatch is corrected."
                ),
            )
        self._state["registered_school_name"] = _clean_text(local.get("school_name"))
        self._state["local_school_id"] = local_school_id
        self._state["authorized_this_device"] = bool(
            self._state.get("authorization_state") == "active"
            and self._state.get("registration_state") == "registered"
            and not self.retired
        )
        public_host = _normalize_host(self._state.get("public_host"))
        self._state["survey_url"] = (
            f"https://{public_host}/access/{access_key}" if public_host and access_key else ""
        )
        self._state["scanner_url"] = f"https://{public_host}/scanner" if public_host else ""
        self._state["local_server_running"] = bool(local.get("server_running"))
        self._state["survey_form_status"] = str(local.get("survey_status") or "offline").casefold()
        self._state["scanner_remote_enabled"] = bool(local.get("scanner_remote_enabled", True))
        if emit:
            self._emit_state()
        return self.state()

    def web_server_settings(self) -> dict[str, Any]:
        """Return only non-secret request-classification settings."""

        return {
            "internet_gateway_enabled": bool(
                self.configured
                and self._state.get("authorization_state") == "active"
                and self._authorization_verified_this_session
                and not self.retired
            ),
            "internet_gateway_public_host": str(self._state.get("public_host") or ""),
            "internet_gateway_trusted_proxy_ips": list(self._state.get("trusted_proxy_ips") or []),
        }

    def set_runtime_services(
        self,
        *,
        client: Any | None = None,
        tunnel: Any | None = None,
        credentials: Any | None = None,
        migration: Any | None = None,
        backup: Any | None = None,
        provider_config: Any | None = None,
    ) -> None:
        self._authorization_verified_this_session = False
        if client is not None:
            self._client = client
        if tunnel is not None:
            self._tunnel = tunnel
        if credentials is not None:
            self._credentials = credentials
        if migration is not None:
            self._migration = migration
        if backup is not None:
            self._backup = backup
        if provider_config is not None:
            self._provider_config = provider_config
        elif client is not None:
            self._provider_config = getattr(client, "config", None)
        self.reload()

    def ensure_installation_id(self) -> str:
        ensure = _callable_attr(self._state_store, "ensure_installation_identity")
        if ensure is None:
            raise RuntimeError("Internet Gateway installation identity storage is unavailable.")
        installation_id = str(ensure())
        self._state["installation_id"] = installation_id
        return installation_id

    def management_url(
        self,
        *,
        mode: str = "registration",
        transfer_mode: str = "server_and_data",
        data_verified: bool = False,
    ) -> str:
        """Build the initial registration page without placing a secret in it.

        Transfer URLs are deliberately created only from a server-issued,
        short-lived intent.  The legacy arguments remain in the signature so
        older callers fail safely instead of silently opening an insecure
        browser-controlled transfer request.
        """

        del transfer_mode, data_verified
        if str(mode or "registration").casefold() != "registration":
            raise ValueError(
                "Server Transfer requires a one-time transfer intent created by the Control Center."
            )

        if not self.provider_available:
            raise RuntimeError(
                "Managed Internet Gateway provider configuration is not installed. "
                "Local-Only operation remains available."
            )
        local = dict(self._local_settings_provider() or {})
        school_id = _clean_text(local.get("school_id"))
        school_name = _clean_text(local.get("school_name"))
        if not school_id:
            raise ValueError("Complete the official School ID in School Information first.")
        installation_id = self.ensure_installation_id()
        parsed = urlparse(str(getattr(self._provider_config, "service_url", "")))
        base_path = parsed.path.rstrip("/")
        query: dict[str, str] = {
            "mode": "registration",
            "school_id": school_id,
            "school_name": school_name,
            "installation_id": installation_id,
        }
        return urlunparse(
            (parsed.scheme, parsed.netloc, f"{base_path}/manage", "", urlencode(query), "")
        )

    def passkey_management_url(self) -> str:
        """Open provider-owned administrator passkey management in a browser."""

        if not self.provider_available:
            raise RuntimeError("The managed Internet Gateway provider is unavailable.")
        school_id = _clean_text(
            self._state.get("school_id")
            or dict(self._local_settings_provider() or {}).get("school_id")
        )
        if not school_id:
            raise ValueError("Complete School Information before managing passkeys.")
        parsed = urlparse(str(getattr(self._provider_config, "service_url", "")))
        base_path = parsed.path.rstrip("/")
        return urlunparse(
            (
                parsed.scheme,
                parsed.netloc,
                f"{base_path}/manage",
                "",
                urlencode({"mode": "passkeys", "school_id": school_id}),
                "",
            )
        )

    def prepare_migration_import_async(self, request: Mapping[str, Any]) -> bool:
        payload = dict(request or {})
        return self._run_async(
            "migration_import",
            lambda: self.prepare_migration_import(payload),
            "Encrypted Server and Data import",
        )

    def create_migration_package_async(self, request: Mapping[str, Any]) -> bool:
        payload = dict(request or {})
        return self._run_async(
            "migration_export",
            lambda: self.create_migration_package(payload),
            "Encrypted Server and Data package",
        )

    def create_migration_package(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """Create a destination-bound package and return its key exactly once."""

        if self._migration is None:
            raise RuntimeError("The Server and Data migration component is unavailable.")
        if not self.configured or self._state.get("authorization_state") != "active":
            raise RuntimeError(
                "Only this school's active registered Internet Server can prepare a transfer package."
            )
        local = dict(self._local_settings_provider() or {})
        if bool(local.get("server_running")):
            raise RuntimeError(
                "Stop the local Survey Server before preparing a consistent transfer package."
            )
        school_id = _clean_text(local.get("school_id"))
        if not school_id or school_id != _clean_text(self._state.get("school_id")):
            raise RuntimeError("School Information does not match the registered School ID.")
        source_installation_id = self.ensure_installation_id()
        destination_installation_id = _clean_text(
            request.get("destination_installation_id")
        )
        if not destination_installation_id:
            raise ValueError("Enter the replacement computer's installation ID.")
        if destination_installation_id.casefold() == source_installation_id.casefold():
            raise ValueError("The destination must be a different application installation.")
        destination_text = str(request.get("destination_path") or "").strip()
        if not destination_text:
            raise ValueError("Choose where to save the encrypted migration package.")
        # Keep the caller's path lexical until the migration service has
        # inspected every existing ancestor.  Resolving here would collapse a
        # junction/symlink and erase the evidence the hardened writer needs to
        # reject redirected export destinations.
        destination = Path(destination_text).expanduser().absolute()
        if destination.suffix.casefold() != ".mossmig":
            destination = destination.with_name(destination.name + ".mossmig")
        if destination.exists():
            raise FileExistsError(
                "The selected migration-package file already exists. Choose a new name."
            )
        from school_csm_control_center.version import __version__

        key = secrets.token_bytes(32)
        manifest = self._migration.create_package(
            destination,
            key=key,
            school_id=school_id,
            source_installation_id=source_installation_id,
            destination_installation_id=destination_installation_id,
            application_version=__version__,
        )
        return {
            "package_path": str(destination),
            "migration_key": key.hex(),
            "transaction_id": str(manifest.get("transaction_id") or ""),
            "manifest_sha256": str(manifest.get("manifest_sha256") or ""),
            "record_counts": deepcopy(dict(manifest.get("record_counts") or {})),
        }

    def create_recovery_backup_async(self, request: Mapping[str, Any]) -> bool:
        payload = dict(request or {})
        return self._run_async(
            "backup_export",
            lambda: self.create_recovery_backup(payload),
            "Encrypted portable recovery backup",
        )

    def create_recovery_backup(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """Create a portable .mossbak and reveal its fresh key only once."""

        if self._backup is None:
            raise RuntimeError("Portable recovery backup support is unavailable.")
        if not self.configured or self._state.get("authorization_state") != "active":
            raise RuntimeError(
                "Only this school's active registered Internet Server can create a recovery backup."
            )
        local = dict(self._local_settings_provider() or {})
        if bool(local.get("server_running")):
            raise RuntimeError(
                "Stop the local Survey Server before creating a consistent recovery backup."
            )
        school_id = _clean_text(local.get("school_id"))
        if not school_id or school_id != _clean_text(self._state.get("school_id")):
            raise RuntimeError("School Information does not match the registered School ID.")
        destination_text = str(request.get("destination_path") or "").strip()
        if not destination_text:
            raise ValueError("Choose where to save the encrypted recovery backup.")
        from school_csm_control_center.version import __version__

        prepared = self._backup.create_backup(
            destination_text,
            school_id=school_id,
            source_installation_id=self.ensure_installation_id(),
            application_version=__version__,
            overwrite=False,
        )
        result = {
            "package_path": str(prepared.package_path),
            "backup_id": str(prepared.backup_id),
            "manifest_sha256": str(prepared.manifest_sha256),
            "record_counts": deepcopy(dict(prepared.record_counts)),
        }
        # This must be the final interaction with the prepared object: the
        # service zeroes its mutable key buffer as the value is taken.
        result["backup_key"] = prepared.take_one_time_key_hex()
        return result

    def restore_recovery_backup_async(self, request: Mapping[str, Any]) -> bool:
        payload = dict(request or {})
        return self._run_async(
            "backup_restore",
            lambda: self.restore_recovery_backup(payload),
            "Portable recovery backup restore",
        )

    def restore_recovery_backup(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """Authenticate, stage, and atomically activate a .mossbak backup."""

        if self._backup is None:
            raise RuntimeError("Portable recovery backup support is unavailable.")
        local = dict(self._local_settings_provider() or {})
        if bool(local.get("server_running")):
            raise RuntimeError(
                "Stop the local Survey Server before restoring transferred school data."
            )
        backup_path = str(request.get("package_path") or "").strip()
        if not backup_path:
            raise ValueError("Select the encrypted .mossbak recovery backup.")
        if Path(backup_path).suffix.casefold() != ".mossbak":
            raise ValueError("Recovery Backup accepts only a .mossbak file.")
        backup_key = str(request.get("migration_key") or request.get("backup_key") or "").strip()
        if not backup_key:
            raise ValueError("Enter the one-time key for this recovery backup.")
        school_id = _clean_text(local.get("school_id"))
        from school_csm_control_center.version import __version__

        receipt = self._backup.restore_backup(
            backup_path,
            key=backup_key,
            expected_school_id=school_id,
            destination_installation_id=self.ensure_installation_id(),
            current_application_version=__version__,
            replace_existing=bool(request.get("replace_existing")),
        )
        validation = dict(receipt.data_validation())
        return {
            "data_validation": validation,
            "record_counts": deepcopy(dict(receipt.record_counts)),
            "committed_at": str(receipt.committed_at),
        }

    def prepare_migration_import(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """Stage, validate, and commit an encrypted destination-bound package."""

        if self._migration is None:
            raise RuntimeError("The Server and Data migration component is unavailable.")
        local = dict(self._local_settings_provider() or {})
        if bool(local.get("server_running")):
            raise RuntimeError(
                "Stop the local Survey Server before activating transferred school data."
            )
        package_path = str(request.get("package_path") or "").strip()
        if not package_path:
            raise ValueError("Select the encrypted School CSM migration package.")
        if Path(package_path).suffix.casefold() != ".mossmig":
            raise ValueError("Server and Data accepts only a .mossmig package.")
        key = _decode_migration_key(request.get("migration_key"))
        school_id = _clean_text(local.get("school_id"))
        installation_id = self.ensure_installation_id()
        from school_csm_control_center.version import __version__

        staged = self._migration.stage_import(
            package_path,
            key=key,
            expected_school_id=school_id,
            destination_installation_id=installation_id,
            current_application_version=__version__,
            replace_existing=bool(request.get("replace_existing")),
        )
        try:
            manifest = json.loads(Path(staged.manifest_path).read_text(encoding="utf-8"))
            receipt = self._migration.commit_import(staged)
        except Exception:
            # The migration service preserves its failure journal and performs
            # commit rollback. Discard only inert, pre-commit staging data.
            discard = _callable_attr(self._migration, "discard_staged_import")
            if discard is not None:
                try:
                    discard(staged)
                except Exception:
                    pass
            raise
        validation = {
            "verified": True,
            "transaction_id": str(receipt.transaction_id),
            "manifest_sha256": str(manifest.get("manifest_sha256") or ""),
            "active_tree_sha256": str(receipt.active_tree_sha256),
            "record_counts": deepcopy(dict(staged.record_counts)),
        }
        if not validation["manifest_sha256"]:
            raise RuntimeError("The committed migration manifest digest is unavailable.")
        return {
            "data_validation": validation,
            "record_counts": deepcopy(dict(staged.record_counts)),
            "committed_at": str(receipt.committed_at),
        }

    def create_transfer_intent_async(self, request: Mapping[str, Any]) -> bool:
        payload = dict(request or {})
        return self._run_async(
            "transfer_intent",
            lambda: self.create_transfer_intent(payload),
            "Server Transfer authorization request",
        )

    def create_transfer_intent(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """Create a server-side, one-use intent before opening WebAuthn."""

        create = _callable_attr(self._client, "create_transfer_intent")
        if create is None or not self.provider_available:
            raise RuntimeError("The managed Server Transfer service is unavailable.")
        local = dict(self._local_settings_provider() or {})
        school_id = _clean_text(local.get("school_id"))
        if not school_id:
            raise ValueError("Complete School Information before Server Transfer.")
        transfer_mode = str(request.get("transfer_mode") or request.get("option") or "").casefold()
        if transfer_mode not in {"server_and_data", "backup_assisted", "server_only"}:
            raise ValueError("Choose a valid Server Transfer option.")
        validation = dict(request.get("data_validation") or {})
        if transfer_mode == "server_only":
            if validation != {"warning_confirmed": True}:
                raise ValueError(
                    "Confirm the Server Only data-loss warning before requesting authorization."
                )
        else:
            common_fields = {
                "verified",
                "transaction_id",
                "manifest_sha256",
                "active_tree_sha256",
                "record_counts",
            }
            backup_fields = {
                "source_kind",
                "backup_id",
                "backup_manifest_sha256",
            }
            required = common_fields | (
                backup_fields if transfer_mode == "backup_assisted" else set()
            )
            if set(validation) != required or validation.get("verified") is not True:
                raise ValueError(
                    "The selected transfer mode requires its exact completed data-validation receipt."
                )
            transaction_id = str(validation.get("transaction_id") or "").strip()
            manifest_digest = str(validation.get("manifest_sha256") or "").casefold()
            active_digest = str(validation.get("active_tree_sha256") or "").casefold()
            counts = validation.get("record_counts")
            if (
                not transaction_id
                or len(manifest_digest) != 64
                or len(active_digest) != 64
                or any(character not in "0123456789abcdef" for character in manifest_digest)
                or any(character not in "0123456789abcdef" for character in active_digest)
                or not isinstance(counts, Mapping)
                or any(
                    not isinstance(count, int) or isinstance(count, bool) or count < 0
                    for count in counts.values()
                )
            ):
                raise ValueError(
                    "The committed migration validation receipt is incomplete or invalid."
                )
            if transfer_mode == "backup_assisted":
                backup_id = str(validation.get("backup_id") or "").strip()
                backup_digest = str(
                    validation.get("backup_manifest_sha256") or ""
                ).casefold()
                if (
                    validation.get("source_kind") != "portable_backup"
                    or not backup_id
                    or len(backup_digest) != 64
                    or any(
                        character not in "0123456789abcdef"
                        for character in backup_digest
                    )
                ):
                    raise ValueError(
                        "The committed recovery-backup receipt is incomplete or invalid."
                    )
        result = _mapping(
            create(
                {
                    "school_id": school_id,
                    "destination_installation_id": self.ensure_installation_id(),
                    "transfer_mode": transfer_mode,
                    "data_validation": validation,
                }
            )
        )
        intent_id = str(result.get("intent_id") or "").strip()
        if not intent_id or any(character in intent_id for character in "\r\n\0#?"):
            raise RuntimeError("The Registration Service returned an invalid transfer intent.")
        parsed = urlparse(str(getattr(self._provider_config, "service_url", "")))
        base_path = parsed.path.rstrip("/")
        manage_url = urlunparse(
            (
                parsed.scheme,
                parsed.netloc,
                f"{base_path}/manage",
                "",
                "",
                "intent=" + quote(intent_id, safe=""),
            )
        )
        return {
            "intent_id": intent_id,
            "expires_at": str(result.get("expires_at") or ""),
            "management_url": manage_url,
        }

    def redeem_completion_code_async(self, completion_code: str) -> bool:
        code = str(completion_code or "").strip()
        return self._run_async(
            "completion_code",
            lambda: self.redeem_completion_code(code),
            "Internet Server authorization",
        )

    def redeem_completion_code(self, completion_code: str) -> dict[str, Any]:
        self._authorization_verified_this_session = False
        code = str(completion_code or "").strip()
        if not code:
            raise ValueError("Enter the one-time completion code shown in the secure browser page.")
        redeem = _callable_attr(self._client, "redeem_handoff")
        if redeem is None:
            raise RuntimeError("The managed Internet Gateway provider is unavailable.")
        installation_id = self.ensure_installation_id()
        result = redeem(code, installation_id)
        payload = dict(_mapping(result))
        if not bool(payload.get("ok", True)):
            raise RuntimeError(_clean_text(payload.get("detail")) or "The completion code was not accepted.")
        if str(payload.get("installation_id") or payload.get("destination_installation_id") or "") != installation_id:
            raise RuntimeError("The authorization belongs to a different application installation.")
        local_school_id = _clean_text((self._local_settings_provider() or {}).get("school_id"))
        if _clean_text(payload.get("school_id")) != local_school_id:
            raise RuntimeError("The authorization belongs to a different School ID.")
        public_hostname = _normalize_host(
            payload.get("public_hostname") or payload.get("public_host")
        )
        expected_host = str(self._provider_config.public_host_for(local_school_id))
        if public_hostname != expected_host:
            raise RuntimeError(
                "The authorization contains an unexpected managed Internet address."
            )

        tunnel_token = str(payload.pop("tunnel_token", "") or "").strip()
        installation_secret = str(payload.pop("installation_secret", "") or "").strip()
        acknowledgement_token = str(
            payload.pop("acknowledgement_token", "") or ""
        ).strip()
        payload.pop("handoff_expires_at", None)
        if not tunnel_token or not installation_secret or not acknowledgement_token:
            raise RuntimeError("The provider returned incomplete device authorization material.")
        if self._credentials is None:
            raise RuntimeError("Windows Credential Manager is unavailable for Internet Gateway credentials.")

        previous_tunnel = ""
        previous_installation_secret = ""
        load_tunnel = _callable_attr(self._credentials, "load_tunnel_credential")
        load_installation = _callable_attr(
            self._credentials, "load_installation_secret"
        )
        if load_tunnel is not None:
            previous_tunnel = str(load_tunnel() or "")
        if load_installation is not None:
            previous_installation_secret = str(load_installation() or "")

        def restore_previous_credentials() -> None:
            try:
                if previous_tunnel:
                    self._credentials.save_tunnel_credential(previous_tunnel)
                else:
                    delete = _callable_attr(
                        self._credentials, "delete_tunnel_credential"
                    )
                    if delete is not None:
                        delete()
                if previous_installation_secret:
                    self._credentials.save_installation_secret(
                        previous_installation_secret
                    )
                else:
                    delete = _callable_attr(
                        self._credentials, "delete_installation_secret"
                    )
                    if delete is not None:
                        delete()
                    else:
                        delete_all = _callable_attr(self._credentials, "delete_all")
                        if delete_all is not None:
                            delete_all()
            except Exception:
                # The original persistence error is more actionable. The
                # handoff remains unacknowledged and can still be retried.
                pass

        self._credentials.save_tunnel_credential(tunnel_token)
        try:
            self._credentials.save_installation_secret(installation_secret)
        except Exception:
            restore_previous_credentials()
            raise
        saver = _callable_attr(self._state_store, "save_registration")
        if saver is None:
            restore_previous_credentials()
            raise RuntimeError("Internet Gateway registration storage is unavailable.")
        try:
            saver(
                school_id=local_school_id,
                registration_id=str(payload.get("registration_id") or ""),
                public_hostname=public_hostname,
                tunnel_id=str(payload.get("tunnel_id") or ""),
                provisioning_state=str(payload.get("provisioning_state") or "ready"),
                gateway_status="disconnected",
                authorization_status="ACTIVE",
                installation_id=installation_id,
            )
        except Exception:
            # The server does not consume the completion code until it receives
            # a post-persistence acknowledgement, so restoring the prior local
            # credentials leaves this exact operation safely retryable.
            restore_previous_credentials()
            raise

        acknowledge = _callable_attr(self._client, "acknowledge_handoff")
        if acknowledge is None:
            raise RuntimeError(
                "Internet Server authorization was saved, but this application "
                "cannot confirm secure delivery. Run Setup and choose Repair, "
                "then enter the same completion code again."
            )
        acknowledgement_error: Exception | None = None
        for _attempt in range(2):
            try:
                acknowledgement = _mapping(
                    acknowledge(code, installation_id, acknowledgement_token)
                )
                if not bool(acknowledgement.get("ok", True)) or not bool(
                    acknowledgement.get("acknowledged")
                ):
                    raise RuntimeError(
                        _clean_text(acknowledgement.get("detail"))
                        or "The provider did not confirm secure credential delivery."
                    )
                acknowledgement_error = None
                break
            except Exception as exc:
                acknowledgement_error = exc
        state = self.reload()
        if acknowledgement_error is not None:
            raise RuntimeError(
                "Internet Server authorization was saved securely, but the "
                "Registration Service could not confirm delivery. Check the "
                "Internet connection and enter the same completion code again."
            ) from acknowledgement_error
        self.notice_requested.emit(
            "Internet Server authorization saved securely. Start the local Survey Server, then connect the Internet Gateway.",
            "success",
        )
        return state

    def connect_or_reconnect_async(self) -> bool:
        return self._run_async("connect", self.connect_or_reconnect, "Internet Gateway connection")

    def run_diagnostics_async(self) -> bool:
        return self._run_async("diagnostics", self.run_diagnostics, "Internet Gateway diagnostics")

    def check_authorization_async(self) -> bool:
        return self._run_async(
            "authorization_check",
            self.check_authorization,
            "Internet authorization check",
            quiet_failure=True,
        )

    def _run_async(
        self,
        operation: str,
        action: Callable[[], Any],
        label: str,
        *,
        quiet_failure: bool = False,
    ) -> bool:
        with self._operation_lock:
            if self._operation:
                return False
            self._operation = str(operation)
            self._state["operation"] = self._operation
        self.operation_progress.emit(
            {"operation": operation, "detail": f"{label} is in progress...", "busy": True}
        )
        self._emit_state()

        def worker() -> None:
            try:
                result = action()
                self.operation_progress.emit(
                    {
                        "operation": operation,
                        "detail": f"{label} completed.",
                        "busy": False,
                        "result": result,
                    }
                )
            except Exception as exc:
                message = _clean_text(exc) or f"{label} failed."
                self.operation_progress.emit(
                    {"operation": operation, "detail": message, "busy": False, "error": True}
                )
                if not quiet_failure:
                    self.notice_requested.emit(message, "error")
            finally:
                with self._operation_lock:
                    self._operation = ""
                    self._state["operation"] = ""
                self._emit_state()

        Thread(target=worker, name=f"SchoolCSMGateway-{operation}", daemon=True).start()
        return True

    def connect_or_reconnect(self) -> bool:
        if not self.configured:
            raise RuntimeError("Authorize this installation before connecting the Internet Gateway.")
        if self.retired:
            self._emit_retirement()
            return False
        if self._state.get("authorization_state") != "active":
            raise RuntimeError("This device is not the school's active Internet Server.")
        if not self._authorization_verified_this_session:
            raise RuntimeError(
                "Verify this device's Internet authorization before connecting the Internet Gateway."
            )
        local = dict(self._local_settings_provider() or {})
        if not bool(local.get("server_running")):
            raise RuntimeError("Start the local Survey Server before connecting the Internet Gateway.")
        if "tunnel_origin_url" in local and not str(
            local.get("tunnel_origin_url") or ""
        ).startswith("http://127.0.0.1:"):
            raise RuntimeError(
                "Restart the local Survey Server once so it can enable the private loopback tunnel origin. LAN access remains available during normal operation."
            )
        origin_port = int(local.get("server_port") or local.get("preferred_port") or 0)
        if not 1 <= origin_port <= 65535:
            raise RuntimeError("The running local Survey Server port is unavailable.")
        if self._tunnel is None or self._credentials is None:
            raise RuntimeError("Internet Gateway components are unavailable. Run Setup and choose Repair.")

        installation_secret = self._credentials.load_installation_secret()
        tunnel_token = self._credentials.load_tunnel_credential()
        fetch = _callable_attr(self._client, "tunnel_credential")
        if fetch is not None and installation_secret:
            refreshed = _mapping(
                fetch(
                    str(self._state.get("school_id") or ""),
                    str(self._state.get("installation_id") or ""),
                    installation_secret,
                    origin_port=origin_port,
                )
            )
            refreshed_token = str(refreshed.get("tunnel_token") or "").strip()
            if refreshed_token:
                self._credentials.save_tunnel_credential(refreshed_token)
                tunnel_token = refreshed_token
        if not tunnel_token:
            raise RuntimeError("The saved Internet Gateway tunnel credential is unavailable.")

        self._set_gateway_state("connecting", "Connecting the secure outbound Internet Gateway tunnel...")
        snapshot = self._tunnel.start(tunnel_token)
        result = _mapping(snapshot)
        status = str(result.get("state") or result.get("status") or "").casefold()
        if status not in {"connected", "running", "started"}:
            self._set_gateway_state("error", _clean_text(result.get("detail")) or "The tunnel did not start.")
            return False
        self._set_gateway_state("connected", _clean_text(result.get("detail")) or "Internet Gateway is connected.")
        return True

    def disconnect(self) -> None:
        stop = _callable_attr(self._tunnel, "stop", "disconnect")
        if stop is not None:
            try:
                stop()
            except Exception as exc:
                self._last_error = _clean_text(exc)
        if self.configured and not self.retired:
            self._set_gateway_state("disconnected", "Internet Gateway is disconnected. Local access remains available.")

    def check_authorization(self) -> dict[str, Any]:
        self._authorization_verified_this_session = False
        if not self.configured or self._client is None or self._credentials is None:
            return self.state()
        secret = self._credentials.load_installation_secret()
        if not secret:
            self._state["authorization_state"] = "registration_error"
            self._state["detail"] = "The device authorization credential is unavailable. Run Setup and choose Repair."
            self._emit_state()
            return self.state()
        try:
            authorization = self._client.authorization_status(
                str(self._state.get("school_id") or ""),
                str(self._state.get("installation_id") or ""),
                secret,
            )
        except Exception as exc:
            self._state["authorization_state"] = "unknown_offline"
            self._state["detail"] = _clean_text(exc) or "Authorization could not be checked. Local access remains available."
            self._emit_state()
            return self.state()

        result = _mapping(authorization)
        status = str(result.get("state") or result.get("authorization_state") or "").casefold()
        checked_at = _utc_now()
        if status == "active":
            updater = _callable_attr(self._state_store, "update_status")
            if updater is not None:
                updater(authorization_status="ACTIVE", checked_at=checked_at, detail="Authorization verified.")
            state = self.reload()
            self._authorization_verified_this_session = True
            return state
        if status in RETIRED_AUTHORIZATION_STATES:
            envelope = result.get("envelope") or getattr(authorization, "envelope", None)
            retire = _callable_attr(
                self._state_store,
                "retire_authorization_envelope",
                "retire_authorization",
            )
            if retire is None or not isinstance(envelope, Mapping):
                raise RuntimeError(
                    "A revoked authorization was received but could not be persisted with its signed proof. "
                    "The application has stopped Internet services."
                )
            stop = _callable_attr(self._tunnel, "stop", "disconnect")
            if stop is not None:
                try:
                    stop()
                except Exception:
                    pass
            try:
                retire(envelope, provider_config=self._provider_config)
            except Exception:
                # The provider response was already signature-verified by the
                # client.  Block this running process even if durable storage
                # is temporarily unavailable; never leave the public tunnel
                # operating after a verified revocation.
                self._state.update(
                    authorization_state=status,
                    gateway_state="revoked",
                    authorized_this_device=False,
                    retirement={
                        "school_id": result.get("school_id") or self._state.get("school_id"),
                        "retired_installation_id": result.get("installation_id")
                        or self._state.get("installation_id"),
                        "retirement_status": status.upper(),
                        "transfer_transaction_id": result.get("transaction_id") or "",
                    },
                    detail=(
                        "Internet authority was revoked. Its signed retirement proof could not be saved, "
                        "so this application remains blocked and should be repaired before restart."
                    ),
                )
                self._emit_state()
                self._emit_retirement()
                raise
            self.reload()
            self._emit_retirement()
            return self.state()
        updater = _callable_attr(self._state_store, "update_status")
        if updater is not None:
            updater(
                authorization_status=status.upper() if status else "REGISTRATION_ERROR",
                checked_at=checked_at,
                detail="Authorization status changed.",
            )
        return self.reload()

    def run_diagnostics(self) -> dict[str, Any]:
        local = dict(self._local_settings_provider() or {})
        credential_presence = {}
        if self._credentials is not None:
            try:
                credential_presence = dict(self._credentials.exists())
            except Exception:
                credential_presence = {}

        provider_error = ""
        registration_service_https: bool | None = None
        authorization_checked_now = False
        if self.provider_available:
            health_check = _callable_attr(self._client, "health_check")
            if health_check is None:
                provider_error = "Registration-service health diagnostics are unavailable."
            else:
                try:
                    registration_service_https = bool(health_check())
                except Exception:
                    registration_service_https = False
                    provider_error = "The registration service could not be reached securely."

            if (
                self.configured
                and credential_presence.get("installation_secret")
                and _callable_attr(self._client, "authorization_status") is not None
            ):
                try:
                    current = self.check_authorization()
                    authorization_checked_now = str(
                        current.get("authorization_state") or ""
                    ) not in {"", "unknown_offline", "registration_error"}
                    if not authorization_checked_now and not provider_error:
                        provider_error = (
                            "The installation authorization could not be verified securely."
                        )
                except Exception:
                    authorization_checked_now = False
                    if not provider_error:
                        provider_error = (
                            "The installation authorization could not be verified securely."
                        )

        public_checks: dict[str, bool] = {}
        public_host = str(self._state.get("public_host") or "")
        if self.configured and public_host and self._public_health_probe is not None:
            try:
                result = self._public_health_probe(public_host)
                if isinstance(result, Mapping):
                    public_checks = {
                        key: bool(result.get(key))
                        for key in ("public_dns", "public_https", "host_routing")
                    }
            except Exception:
                public_checks = {
                    "public_dns": False,
                    "public_https": False,
                    "host_routing": False,
                }
        component_installed = False
        if self._tunnel is not None:
            verify = _callable_attr(self._tunnel, "verify_component")
            if verify is not None:
                try:
                    component_installed = bool(verify())
                except Exception:
                    component_installed = False
        diagnostics = {
            "registration": REGISTRATION_LABELS.get(str(self._state.get("registration_state")), "Registration Error"),
            "school_id_match": bool(
                self._state.get("school_id")
                and str(local.get("school_id") or "") == self._state.get("school_id")
            ),
            "authorization": AUTHORIZATION_LABELS.get(str(self._state.get("authorization_state")), "Registration Error"),
            "gateway": GATEWAY_LABELS.get(str(self._state.get("gateway_state")), "Error"),
            "public_host": str(self._state.get("public_host") or ""),
            "local_server_running": bool(local.get("server_running")),
            "survey_form_status": str(local.get("survey_status") or "offline"),
            "scanner_remote_enabled": bool(local.get("scanner_remote_enabled", True)),
            "credential_present": bool(
                credential_presence.get("tunnel_credential")
                and credential_presence.get("installation_secret")
            ),
            "component_installed": component_installed,
            "tunnel_service": self.connected,
            "last_authorization_check_at": str(self._state.get("last_authorization_check_at") or ""),
            "detail": str(self._state.get("detail") or ""),
        }
        if registration_service_https is not None:
            diagnostics["registration_service_https"] = registration_service_https
            diagnostics["authorization_checked_now"] = authorization_checked_now
        diagnostics.update(public_checks)
        if provider_error:
            diagnostics["provider_error"] = provider_error
        self.diagnostics_ready.emit(deepcopy(diagnostics))
        return diagnostics

    def _set_gateway_state(self, status: str, detail: str) -> None:
        gateway_status = status if status in GATEWAY_LABELS else "error"
        self._state["gateway_state"] = gateway_status
        self._state["detail"] = _clean_text(detail)
        updater = _callable_attr(self._state_store, "update_status")
        if updater is not None:
            try:
                updater(gateway_status=gateway_status, detail=self._state["detail"])
            except (OSError, TypeError, ValueError):
                pass
        self.operation_progress.emit(
            {"operation": self._operation, "gateway_state": gateway_status, "detail": self._state["detail"]}
        )
        self._emit_state()

    def _emit_retirement(self) -> None:
        record = self._state.get("retirement")
        if not isinstance(record, Mapping):
            record = {
                "school_id": self._state.get("school_id"),
                "school_name": self._state.get("registered_school_name"),
                "retirement_status": self._state.get("authorization_state"),
            }
        self.retirement_confirmed.emit(deepcopy(dict(record)))

    def _emit_state(self) -> None:
        snapshot = self.state()
        self.state_changed.emit(snapshot)
        self.public_urls_changed.emit(
            str(snapshot.get("survey_url") or ""),
            str(snapshot.get("scanner_url") or ""),
        )


def build_gateway_web_settings(
    local_settings: Mapping[str, Any],
    gateway: InternetGatewayController | None,
) -> dict[str, Any]:
    """Merge gateway request policy without persisting it with local settings."""

    values = dict(local_settings)
    if gateway is None:
        values.update(
            internet_gateway_enabled=False,
            internet_gateway_public_host="",
            internet_gateway_trusted_proxy_ips=[],
        )
    else:
        values.update(gateway.web_server_settings())
    return values
