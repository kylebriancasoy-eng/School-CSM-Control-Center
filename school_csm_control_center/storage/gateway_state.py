"""Persistent Internet Gateway identity, authorization, and retirement state.

Gateway authorization is deliberately kept outside ``control_center_settings``.
An ordinary settings reset must not erase an installation identity or a signed
retirement marker, and a data transfer must not accidentally copy either item
to replacement hardware.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import base64
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Callable, Mapping
from uuid import UUID, uuid4

from school_csm_control_center.runtime_paths import storage_root_for
from school_csm_control_center.storage.file_safety import (
    InterProcessRLock,
    atomic_write_json,
    preserve_corrupt_file,
)


GATEWAY_STATE_SCHEMA_VERSION = "1.0"
GATEWAY_AUDIT_SCHEMA_VERSION = "1.0"
RETIREMENT_RECORD_SCHEMA_VERSION = "1.0"
AUTHORIZATION_RECORD_SCHEMA_VERSION = "1.0"
AUTHORIZATION_ENVELOPE_SCHEMA_VERSION = "1.0"

GATEWAY_STATUSES = frozenset(
    {
        "not_configured",
        "registration_required",
        "connecting",
        "connected",
        "disconnected",
        "error",
        "authorization_transferred",
        "revoked",
    }
)
AUTHORIZATION_STATUSES = frozenset(
    {"UNREGISTERED", "ACTIVE", "TRANSFER_PENDING", "TRANSFERRED", "REVOKED", "REGISTRATION_ERROR"}
)
RETIREMENT_STATUSES = frozenset({"TRANSFERRED", "REVOKED", "RETIRED"})
SIGNATURE_ALGORITHMS = frozenset({"Ed25519", "ECDSA-P256-SHA256"})
SCHOOL_ID_PATTERN = re.compile(r"^\d{4,12}$")
PUBLIC_HOST_PATTERN = re.compile(
    r"^(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$"
)
_SECRET_KEY_PARTS = (
    "password",
    "private_key",
    "secret",
    "tunnel_credential",
    "access_token",
    "refresh_token",
    "api_key",
)

SignatureVerifier = Callable[[Mapping[str, Any]], bool]


class GatewayStateStoreError(RuntimeError):
    """Raised when gateway authorization state cannot be persisted safely."""


class GatewayRetirementError(GatewayStateStoreError):
    """Raised when a retirement or reactivation record is not authoritative."""


class GatewayStateStore:
    """Store non-secret infrastructure identity and signed retirement state.

    The separate ``retirement.mossjson`` file is the startup gate.  Once it is
    written, a later failure to update the convenience state/audit documents
    cannot allow the retired installation to resume normal operation.
    """

    FILE_TYPE = "School CSM Internet Gateway State"
    AUDIT_FILE_TYPE = "School CSM Internet Gateway Audit"
    RETIREMENT_FILE_TYPE = "School CSM Retired Installation"

    def __init__(
        self,
        project_root: str | Path,
        *,
        data_root: str | Path | None = None,
        signature_verifier: SignatureVerifier | None = None,
        provider_config: Any | None = None,
    ) -> None:
        self.install_root = Path(project_root).expanduser().resolve()
        self.data_root = storage_root_for(project_root, data_root)
        self.gateway_root = self.data_root / "gateway"
        self.path = self.gateway_root / "gateway_state.json"
        self.audit_path = self.gateway_root / "gateway_audit.mossjson"
        self.retirement_path = self.gateway_root / "retirement.mossjson"
        self._lock = InterProcessRLock(self.gateway_root / "gateway-state.guard")
        self._signature_verifier = signature_verifier
        self._provider_config = provider_config
        self.last_read_error: str | None = None
        self.last_recovery_copy: Path | None = None

    def load(self) -> dict[str, Any]:
        """Return non-secret state plus the authoritative retirement record."""

        with self._lock:
            state = self._read_state()
            state["retirement"] = self._read_retirement(local_state=state)
            return deepcopy(state)

    def retirement_record(self) -> dict[str, Any] | None:
        """Return the signed local startup gate, or ``None`` when not retired.

        An existing but unreadable marker raises instead of being treated as
        absent.  Startup callers can therefore fail closed without making
        Internet availability a prerequisite for ordinary offline operation.
        """

        with self._lock:
            # The signed retirement marker is the only startup authority. A
            # corrupt convenience state file must not brick an otherwise
            # Local-Only installation when that marker does not exist.
            value = self._read_retirement()
            return deepcopy(value) if value is not None else None

    def is_retired(self) -> bool:
        return self.retirement_record() is not None

    def ensure_installation_identity(self) -> str:
        """Create one non-secret installation UUID and return it."""

        with self._lock:
            state = self._read_state()
            installation_id = str(state.get("installation_id") or "").strip()
            if installation_id:
                _validate_uuid(installation_id, "Installation ID")
                return installation_id
            installation_id = str(uuid4())
            state["installation_id"] = installation_id
            state["updated_at"] = _utc_now()
            self._write_state(state)
            self._append_audit_locked(
                "installation_identity_created",
                installation_id=installation_id,
            )
            return installation_id

    def save_registration(
        self,
        *,
        school_id: str,
        registration_id: str,
        public_hostname: str,
        tunnel_id: str = "",
        provisioning_state: str = "",
        gateway_status: str = "disconnected",
        authorization_status: str = "ACTIVE",
        installation_id: str | None = None,
    ) -> dict[str, Any]:
        """Persist registration metadata; credentials must use Credential Manager."""

        school = _validate_school_id(school_id)
        registration = _required_identifier(registration_id, "Registration ID")
        hostname = _validate_public_hostname(public_hostname)
        gateway = _validate_gateway_status(gateway_status)
        authorization = _validate_authorization_status(authorization_status)
        tunnel = _clean_text(tunnel_id, 300)
        provisioning = _clean_text(provisioning_state, 100)
        with self._lock:
            if self._read_retirement() is not None:
                raise GatewayRetirementError(
                    "This installation is retired. A verified Server Transfer authorization "
                    "must clear the retirement marker before it can be registered again."
                )
            state = self._read_state()
            identity = str(installation_id or state.get("installation_id") or uuid4())
            _validate_uuid(identity, "Installation ID")
            previous_school = str(state.get("registration", {}).get("school_id") or "")
            if previous_school and previous_school != school:
                raise GatewayStateStoreError(
                    "This installation already belongs to a different School ID."
                )
            now = _utc_now()
            state.update(
                {
                    "installation_id": identity,
                    "gateway_status": gateway,
                    "authorization_status": authorization,
                    "registration": {
                        "school_id": school,
                        "registration_id": registration,
                        "public_hostname": hostname,
                        "tunnel_id": tunnel,
                        "provisioning_state": provisioning,
                        "registered_at": str(
                            state.get("registration", {}).get("registered_at") or now
                        ),
                    },
                    "updated_at": now,
                }
            )
            self._write_state(state)
            self._append_audit_locked(
                "registration_saved",
                school_id=school,
                installation_id=identity,
                registration_id=registration,
                gateway_status=gateway,
                authorization_status=authorization,
            )
            result = deepcopy(state)
            result["retirement"] = None
            return result

    def update_status(
        self,
        *,
        gateway_status: str | None = None,
        authorization_status: str | None = None,
        checked_at: str | None = None,
        detail: str = "",
    ) -> dict[str, Any]:
        """Update lightweight connection/authorization status without secrets."""

        with self._lock:
            state = self._read_state()
            if gateway_status is not None:
                state["gateway_status"] = _validate_gateway_status(gateway_status)
            if authorization_status is not None:
                state["authorization_status"] = _validate_authorization_status(
                    authorization_status
                )
            if checked_at is not None:
                state["last_authorization_check_at"] = _validate_timestamp(
                    checked_at, "Authorization check timestamp"
                )
            state["status_detail"] = _clean_text(detail, 500)
            state["updated_at"] = _utc_now()
            self._write_state(state)
            self._append_audit_locked(
                "gateway_status_changed",
                installation_id=str(state.get("installation_id") or ""),
                school_id=str(state.get("registration", {}).get("school_id") or ""),
                gateway_status=str(state.get("gateway_status") or ""),
                authorization_status=str(state.get("authorization_status") or ""),
                detail=state["status_detail"],
            )
            result = deepcopy(state)
            result["retirement"] = self._read_retirement(local_state=state)
            return result

    def retire(
        self,
        signed_record: Any,
        *,
        signature_verifier: SignatureVerifier | None = None,
        provider_config: Any | None = None,
    ) -> dict[str, Any]:
        """Persist a signed marker while preserving the legacy record API."""

        envelope = _authorization_envelope_from(signed_record)
        if envelope is not None:
            return self.retire_authorization(
                envelope,
                provider_config=provider_config,
            )

        record = validate_retirement_record(signed_record)
        verifier = signature_verifier or self._signature_verifier
        if verifier is None or not bool(verifier(deepcopy(record))):
            raise GatewayRetirementError(
                "The Registration Service retirement signature could not be verified."
            )
        with self._lock:
            state = self._read_state()
            local_identity = str(state.get("installation_id") or "")
            if local_identity and local_identity != record["retired_installation_id"]:
                raise GatewayRetirementError(
                    "The retirement record belongs to a different installation."
                )
            local_school = str(state.get("registration", {}).get("school_id") or "")
            if local_school and local_school != record["school_id"]:
                raise GatewayRetirementError(
                    "The retirement record belongs to a different School ID."
                )

            marker = {
                "file_type": self.RETIREMENT_FILE_TYPE,
                "schema_version": RETIREMENT_RECORD_SCHEMA_VERSION,
                "persisted_at": _utc_now(),
                "record": record,
            }
            # This write is the security boundary.  It deliberately precedes
            # convenience state/audit updates so any later fault remains safe.
            atomic_write_json(self.retirement_path, marker, allow_nan=False)

            state["gateway_status"] = (
                "revoked" if record["retirement_status"] == "REVOKED" else "authorization_transferred"
            )
            state["authorization_status"] = record["retirement_status"]
            state["updated_at"] = _utc_now()
            self._write_state(state)
            self._append_audit_locked(
                "installation_retired",
                school_id=record["school_id"],
                installation_id=record["retired_installation_id"],
                transaction_id=record["transfer_transaction_id"],
                authorization_status=record["retirement_status"],
            )
            return deepcopy(record)

    def retire_authorization(
        self,
        authorization: Any,
        *,
        provider_config: Any | None = None,
    ) -> dict[str, Any]:
        """Persist a provider-signed TRANSFERRED/REVOKED authorization.

        The original envelope is retained and re-verified on every startup.
        It is bound to this computer's existing installation UUID, School ID,
        managed domain, and provider signing-key fingerprint.
        """

        envelope = _authorization_envelope_from(authorization)
        if envelope is None:
            raise GatewayRetirementError(
                "The Registration Service retirement response is not a signed authorization envelope."
            )
        with self._lock:
            state = self._read_state()
            local_identity, local_school = _require_local_registration_identity(state)
            config = self._resolve_provider_config(provider_config)
            try:
                verified = _verify_provider_authorization_envelope(
                    envelope,
                    config,
                    expected_school_id=local_school,
                    expected_installation_id=local_identity,
                    allowed_states={"transferred", "revoked"},
                    require_current=True,
                )
            except GatewayRetirementError:
                raise
            except ValueError as exc:
                raise GatewayRetirementError(
                    "The Registration Service retirement signature could not be verified."
                ) from exc
            record = _retirement_record_from_provider_authorization(verified)
            marker = {
                "file_type": self.RETIREMENT_FILE_TYPE,
                "schema_version": RETIREMENT_RECORD_SCHEMA_VERSION,
                "persisted_at": _utc_now(),
                "record": record,
                "authorization_envelope": deepcopy(dict(envelope)),
                "provider_binding": _provider_binding(config, verified["key_id"]),
            }
            # This is the startup security boundary, so it precedes state and
            # audit convenience updates.
            try:
                atomic_write_json(self.retirement_path, marker, allow_nan=False)
            except (OSError, TypeError, ValueError) as exc:
                raise GatewayRetirementError(
                    "The verified retirement marker could not be saved."
                ) from exc

            status = verified["authorization_state"].upper()
            state["gateway_status"] = (
                "revoked" if status == "REVOKED" else "authorization_transferred"
            )
            state["authorization_status"] = status
            state["last_authorization_check_at"] = verified["issued_at"]
            state["updated_at"] = _utc_now()
            self._write_state(state)
            self._append_audit_locked(
                "installation_retired",
                school_id=verified["school_id"],
                installation_id=verified["installation_id"],
                transaction_id=verified["transaction_id"],
                authorization_status=status,
                provider_key_id=verified["key_id"],
            )
            return deepcopy(record)

    def retire_authorization_envelope(
        self,
        authorization: Any,
        *,
        provider_config: Any | None = None,
    ) -> dict[str, Any]:
        """Explicit-name compatibility alias used by lifecycle controllers."""

        return self.retire_authorization(
            authorization,
            provider_config=provider_config,
        )

    def clear_for_authorized_transfer(
        self,
        authorization_record: Any,
        *,
        signature_verifier: SignatureVerifier | None = None,
        provider_config: Any | None = None,
    ) -> dict[str, Any]:
        """Clear retirement only after a verified ACTIVE transfer authorization."""

        with self._lock:
            state = self._read_state()
            local_identity, local_school = _require_local_registration_identity(state)
            envelope = _authorization_envelope_from(authorization_record)
            if envelope is not None:
                config = self._resolve_provider_config(provider_config)
                try:
                    verified = _verify_provider_authorization_envelope(
                        envelope,
                        config,
                        expected_school_id=local_school,
                        expected_installation_id=local_identity,
                        allowed_states={"active"},
                        require_current=True,
                    )
                except GatewayRetirementError:
                    raise
                except ValueError as exc:
                    raise GatewayRetirementError(
                        "The Registration Service activation signature could not be verified."
                    ) from exc
                if not verified["transaction_id"]:
                    raise GatewayRetirementError(
                        "An active transfer authorization must identify its transfer transaction."
                    )
                record = _activation_record_from_provider_authorization(verified)
            else:
                record = validate_authorization_record(authorization_record)
                verifier = signature_verifier or self._signature_verifier
                if verifier is None or not bool(verifier(deepcopy(record))):
                    raise GatewayRetirementError(
                        "The Registration Service activation signature could not be verified."
                    )
                if record["active_installation_id"] != local_identity:
                    raise GatewayRetirementError(
                        "The activation record belongs to a different installation."
                    )
                if record["school_id"] != local_school:
                    raise GatewayRetirementError(
                        "The activation record belongs to a different School ID."
                    )

            retired = self._read_retirement(local_state=state)
            if retired is not None and retired["school_id"] != record["school_id"]:
                raise GatewayRetirementError(
                    "The activation record belongs to a different School ID."
                )
            state.update(
                {
                    "installation_id": record["active_installation_id"],
                    "gateway_status": "disconnected",
                    "authorization_status": "ACTIVE",
                    "registration": {
                        "school_id": record["school_id"],
                        "registration_id": record["registration_id"],
                        "public_hostname": record["public_hostname"],
                        "tunnel_id": record.get("tunnel_id", ""),
                        "provisioning_state": record.get("provisioning_state", ""),
                        "registered_at": record["confirmation_timestamp"],
                    },
                    "last_authorization_check_at": record["confirmation_timestamp"],
                    "updated_at": _utc_now(),
                }
            )
            self._write_state(state)
            try:
                self.retirement_path.unlink(missing_ok=True)
            except OSError as exc:
                raise GatewayRetirementError(
                    "The verified activation was saved, but the local retirement marker "
                    "could not be cleared. The installation remains blocked."
                ) from exc
            self._append_audit_locked(
                "installation_reactivated_by_transfer",
                school_id=record["school_id"],
                installation_id=record["active_installation_id"],
                registration_id=record["registration_id"],
                transaction_id=record["transfer_transaction_id"],
                authorization_status="ACTIVE",
            )
            result = deepcopy(state)
            result["retirement"] = None
            return result

    def audit_events(self) -> list[dict[str, Any]]:
        with self._lock:
            document = self._read_audit()
            return deepcopy(document["events"])

    def _empty_state(self) -> dict[str, Any]:
        return {
            "file_type": self.FILE_TYPE,
            "schema_version": GATEWAY_STATE_SCHEMA_VERSION,
            "installation_id": "",
            "gateway_status": "not_configured",
            "authorization_status": "UNREGISTERED",
            "last_authorization_check_at": "",
            "status_detail": "",
            "registration": {},
            "updated_at": None,
        }

    def _read_state(self) -> dict[str, Any]:
        self.last_read_error = None
        self.last_recovery_copy = None
        if not self.path.exists():
            return self._empty_state()
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise ValueError("Gateway state root is not an object.")
            if value.get("file_type") != self.FILE_TYPE:
                raise ValueError("Gateway state file type is not recognized.")
            if str(value.get("schema_version") or "") != GATEWAY_STATE_SCHEMA_VERSION:
                raise ValueError("Gateway state schema version is not supported.")
            _reject_secrets(value)
            installation_id = str(value.get("installation_id") or "")
            if installation_id:
                _validate_uuid(installation_id, "Installation ID")
            value["gateway_status"] = _validate_gateway_status(
                str(value.get("gateway_status") or "not_configured")
            )
            value["authorization_status"] = _validate_authorization_status(
                str(value.get("authorization_status") or "UNREGISTERED")
            )
            if not isinstance(value.get("registration"), dict):
                raise ValueError("Gateway registration metadata is not an object.")
            return value
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            self.last_read_error = str(exc)
            self.last_recovery_copy = preserve_corrupt_file(self.path)
            raise GatewayStateStoreError(
                "The Internet Gateway state could not be read and was left unchanged."
            ) from exc

    def _write_state(self, state: Mapping[str, Any]) -> None:
        value = deepcopy(dict(state))
        value["file_type"] = self.FILE_TYPE
        value["schema_version"] = GATEWAY_STATE_SCHEMA_VERSION
        _reject_secrets(value)
        try:
            atomic_write_json(self.path, value, allow_nan=False)
        except (OSError, TypeError, ValueError) as exc:
            raise GatewayStateStoreError(
                f"Unable to save Internet Gateway state to {self.path}: {exc}"
            ) from exc

    def _read_retirement(
        self,
        *,
        local_state: Mapping[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if not self.retirement_path.exists():
            return None
        try:
            marker = json.loads(self.retirement_path.read_text(encoding="utf-8"))
            if not isinstance(marker, dict) or marker.get("file_type") != self.RETIREMENT_FILE_TYPE:
                raise ValueError("Retirement marker file type is not recognized.")
            if str(marker.get("schema_version") or "") != RETIREMENT_RECORD_SCHEMA_VERSION:
                raise ValueError("Retirement marker schema version is not supported.")
            record = validate_retirement_record(marker.get("record"))
            if "authorization_envelope" in marker or "provider_binding" in marker:
                if not isinstance(local_state, Mapping):
                    local_state = self._read_state()
                local_identity, local_school = _require_local_registration_identity(local_state)
                binding = _strict_mapping(marker.get("provider_binding"), "Provider binding")
                if set(binding) != {
                    "managed_domain",
                    "signing_key_id",
                    "signing_public_key_sha256",
                }:
                    raise ValueError("Retirement marker provider binding is invalid.")
                config = self._resolve_provider_config(None)
                envelope = _strict_mapping(
                    marker.get("authorization_envelope"),
                    "Authorization envelope",
                )
                verified = _verify_provider_authorization_envelope(
                    envelope,
                    config,
                    expected_school_id=local_school,
                    expected_installation_id=local_identity,
                    allowed_states={"transferred", "revoked"},
                    # The status response can expire; a signed retirement is
                    # permanent until a separately verified ACTIVE transfer.
                    require_current=False,
                )
                if binding != _provider_binding(config, verified["key_id"]):
                    raise ValueError("Retirement marker provider binding changed.")
                projected = _retirement_record_from_provider_authorization(verified)
                if record != projected:
                    raise ValueError(
                        "Retirement marker projection does not match its signed envelope."
                    )
                return projected
            if self._signature_verifier is None:
                raise ValueError(
                    "Legacy retirement marker has no configured signature verifier."
                )
            if not bool(self._signature_verifier(deepcopy(record))):
                raise ValueError("Legacy retirement marker signature is invalid.")
            return record
        except GatewayRetirementError:
            raise
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            preserve_corrupt_file(self.retirement_path)
            raise GatewayRetirementError(
                "The local retirement marker exists but cannot be verified. Normal startup must remain blocked."
            ) from exc

    def _resolve_provider_config(self, provider_config: Any | None) -> Any:
        config = provider_config or self._provider_config
        if config is None:
            try:
                from school_csm_control_center.internet_gateway.config import (
                    load_gateway_provider_config,
                )

                config = load_gateway_provider_config(self.install_root, required=True)
            except Exception as exc:
                raise GatewayRetirementError(
                    "The Internet Gateway provider configuration required to verify the retirement marker is unavailable."
                ) from exc
        if not getattr(config, "configured", False):
            raise GatewayRetirementError(
                "The Internet Gateway provider configuration cannot verify retirement markers."
            )
        return config

    def _empty_audit(self) -> dict[str, Any]:
        return {
            "file_type": self.AUDIT_FILE_TYPE,
            "schema_version": GATEWAY_AUDIT_SCHEMA_VERSION,
            "events": [],
        }

    def _read_audit(self) -> dict[str, Any]:
        if not self.audit_path.exists():
            return self._empty_audit()
        try:
            value = json.loads(self.audit_path.read_text(encoding="utf-8"))
            if not isinstance(value, dict) or value.get("file_type") != self.AUDIT_FILE_TYPE:
                raise ValueError("Gateway audit file type is not recognized.")
            if str(value.get("schema_version") or "") != GATEWAY_AUDIT_SCHEMA_VERSION:
                raise ValueError("Gateway audit schema version is not supported.")
            if not isinstance(value.get("events"), list):
                raise ValueError("Gateway audit events are not a list.")
            _reject_secrets(value)
            return value
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            preserve_corrupt_file(self.audit_path)
            raise GatewayStateStoreError(
                "The Internet Gateway audit could not be read and was left unchanged."
            ) from exc

    def _append_audit_locked(self, event_type: str, **details: Any) -> None:
        document = self._read_audit()
        event = {
            "event_id": str(uuid4()),
            "event_type": _required_identifier(event_type, "Event type"),
            "recorded_at": _utc_now(),
            **{str(key): deepcopy(value) for key, value in details.items()},
        }
        _reject_secrets(event)
        document["events"].append(event)
        try:
            atomic_write_json(self.audit_path, document, allow_nan=False)
        except (OSError, TypeError, ValueError) as exc:
            raise GatewayStateStoreError(
                f"Unable to append the Internet Gateway audit: {exc}"
            ) from exc


def _authorization_envelope_from(value: Any) -> dict[str, Any] | None:
    """Return an authorization envelope from its DTO or raw mapping."""

    candidate = getattr(value, "envelope", None)
    if isinstance(candidate, Mapping):
        return {str(key): deepcopy(item) for key, item in candidate.items()}
    if isinstance(value, Mapping) and isinstance(value.get("payload"), Mapping):
        return {str(key): deepcopy(item) for key, item in value.items()}
    return None


def _require_local_registration_identity(
    state: Mapping[str, Any],
) -> tuple[str, str]:
    try:
        installation_id = _validate_uuid(
            state.get("installation_id"),
            "Local installation ID",
        )
        registration = state.get("registration")
        if not isinstance(registration, Mapping):
            raise ValueError("Local registration metadata is missing.")
        school_id = _validate_school_id(registration.get("school_id"))
    except ValueError as exc:
        raise GatewayRetirementError(
            "The signed authorization cannot be bound to this installation's saved identity."
        ) from exc
    return installation_id, school_id


def _verify_provider_authorization_envelope(
    envelope: Mapping[str, Any],
    config: Any,
    *,
    expected_school_id: str,
    expected_installation_id: str,
    allowed_states: set[str],
    require_current: bool,
) -> dict[str, Any]:
    """Verify an Ed25519 provider envelope and normalize its signed payload."""

    value = _strict_mapping(envelope, "Authorization envelope")
    if str(value.get("schema_version") or "") != AUTHORIZATION_ENVELOPE_SCHEMA_VERSION:
        raise ValueError("Authorization envelope schema version is not supported.")
    if str(value.get("algorithm") or "") != "Ed25519":
        raise ValueError("Authorization envelope signature algorithm is not supported.")
    payload = _strict_mapping(value.get("payload"), "Authorization payload")
    key_id = _required_identifier(value.get("key_id"), "Provider signing-key ID", maximum=80)
    public_keys = getattr(config, "signing_public_keys", None)
    if not isinstance(public_keys, Mapping) or key_id not in public_keys:
        raise ValueError("Authorization envelope uses an untrusted provider key.")
    public_key = bytes(public_keys[key_id])
    if len(public_key) != 32:
        raise ValueError("Provider signing key is invalid.")
    try:
        signature = base64.b64decode(str(value.get("signature") or ""), validate=True)
    except (TypeError, ValueError):
        raise ValueError("Authorization envelope signature is invalid.") from None
    if len(signature) != 64:
        raise ValueError("Authorization envelope signature is invalid.")
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        Ed25519PublicKey.from_public_bytes(public_key).verify(
            signature,
            json.dumps(
                payload,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8"),
        )
    except ImportError:
        raise GatewayRetirementError(
            "Provider authorization verification is unavailable. Repair the application installation."
        ) from None
    except (TypeError, ValueError):
        raise ValueError("Authorization envelope payload is invalid.") from None
    except Exception:
        raise ValueError("Authorization envelope signature could not be verified.") from None

    school_id = _validate_school_id(payload.get("school_id"))
    installation_id = _validate_uuid(
        payload.get("installation_id"),
        "Authorization installation ID",
    )
    if school_id != expected_school_id or installation_id != expected_installation_id:
        raise ValueError("Authorization envelope belongs to another school installation.")
    state = str(payload.get("authorization_state") or "").strip().casefold()
    if state not in allowed_states:
        raise ValueError("Authorization envelope state cannot perform this operation.")
    public_host = _validate_public_hostname(payload.get("public_host"))
    try:
        expected_host = str(config.public_host_for(school_id)).casefold().rstrip(".")
    except Exception as exc:
        raise ValueError("Provider configuration cannot derive the School ID hostname.") from exc
    if public_host != expected_host:
        raise ValueError("Authorization envelope public hostname is not provider-managed.")
    issued_at = _validate_timestamp(payload.get("issued_at"), "Authorization issue timestamp")
    expires_at = _validate_timestamp(payload.get("expires_at"), "Authorization expiry timestamp")
    issued = datetime.fromisoformat(issued_at.replace("Z", "+00:00"))
    expires = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    if expires <= issued:
        raise ValueError("Authorization envelope validity period is invalid.")
    if require_current:
        current = datetime.now(timezone.utc)
        if issued > current or expires <= current:
            raise ValueError("Authorization envelope is not currently valid.")

    transaction_id = _clean_text(payload.get("transaction_id"), 300)
    if state in {"transferred", "revoked"} and not transaction_id:
        raise ValueError("Retirement authorization is missing its transaction ID.")
    return {
        "school_id": school_id,
        "installation_id": installation_id,
        "registration_id": _required_identifier(
            payload.get("registration_id"),
            "Registration ID",
        ),
        "authorization_state": state,
        "public_host": public_host,
        "tunnel_id": _clean_text(payload.get("tunnel_id"), 300),
        "transaction_id": transaction_id,
        "issued_at": issued_at,
        "expires_at": expires_at,
        "key_id": key_id,
        "signature": str(value.get("signature") or ""),
    }


def _provider_binding(config: Any, key_id: str) -> dict[str, str]:
    public_keys = getattr(config, "signing_public_keys", {})
    key = bytes(public_keys[key_id])
    return {
        "managed_domain": str(getattr(config, "managed_domain", "")).casefold().rstrip("."),
        "signing_key_id": str(key_id),
        "signing_public_key_sha256": hashlib.sha256(key).hexdigest(),
    }


def _retirement_record_from_provider_authorization(
    authorization: Mapping[str, Any],
) -> dict[str, Any]:
    status = str(authorization["authorization_state"]).upper()
    return validate_retirement_record(
        {
            "schema_version": RETIREMENT_RECORD_SCHEMA_VERSION,
            "school_id": authorization["school_id"],
            "retired_installation_id": authorization["installation_id"],
            "transfer_transaction_id": authorization["transaction_id"],
            "confirmation_timestamp": authorization["issued_at"],
            "retirement_status": status,
            "registration_service_key_id": authorization["key_id"],
            "signature_algorithm": "Ed25519",
            "authority_signature": authorization["signature"],
            "reason": (
                "Internet Server authorization was transferred to another installation."
                if status == "TRANSFERRED"
                else "Internet Server authorization was revoked by the Registration Service."
            ),
        }
    )


def _activation_record_from_provider_authorization(
    authorization: Mapping[str, Any],
) -> dict[str, Any]:
    return validate_authorization_record(
        {
            "schema_version": AUTHORIZATION_RECORD_SCHEMA_VERSION,
            "school_id": authorization["school_id"],
            "active_installation_id": authorization["installation_id"],
            "registration_id": authorization["registration_id"],
            "public_hostname": authorization["public_host"],
            "tunnel_id": authorization["tunnel_id"],
            "provisioning_state": "active",
            "transfer_transaction_id": authorization["transaction_id"],
            "confirmation_timestamp": authorization["issued_at"],
            "authorization_status": "ACTIVE",
            "registration_service_key_id": authorization["key_id"],
            "signature_algorithm": "Ed25519",
            "authority_signature": authorization["signature"],
        }
    )


def validate_retirement_record(record: Any) -> dict[str, Any]:
    """Validate the canonical Registration Service retirement envelope."""

    value = _strict_mapping(record, "Retirement record")
    expected = {
        "schema_version",
        "school_id",
        "retired_installation_id",
        "transfer_transaction_id",
        "confirmation_timestamp",
        "retirement_status",
        "registration_service_key_id",
        "signature_algorithm",
        "authority_signature",
        "reason",
    }
    _reject_unknown_fields(value, expected, "retirement record")
    normalized = {
        "schema_version": str(value.get("schema_version") or ""),
        "school_id": _validate_school_id(value.get("school_id")),
        "retired_installation_id": _validate_uuid(
            value.get("retired_installation_id"), "Retired installation ID"
        ),
        "transfer_transaction_id": _required_identifier(
            value.get("transfer_transaction_id"), "Transfer transaction ID"
        ),
        "confirmation_timestamp": _validate_timestamp(
            value.get("confirmation_timestamp"), "Retirement confirmation timestamp"
        ),
        "retirement_status": str(value.get("retirement_status") or "").strip().upper(),
        "registration_service_key_id": _required_identifier(
            value.get("registration_service_key_id"), "Registration Service key ID"
        ),
        "signature_algorithm": str(value.get("signature_algorithm") or "").strip(),
        "authority_signature": _required_identifier(
            value.get("authority_signature"), "Registration Service signature", maximum=4096
        ),
        "reason": _clean_text(value.get("reason"), 500),
    }
    if normalized["schema_version"] != RETIREMENT_RECORD_SCHEMA_VERSION:
        raise ValueError("Retirement record schema version is not supported.")
    if normalized["retirement_status"] not in RETIREMENT_STATUSES:
        raise ValueError("Retirement status must be TRANSFERRED, REVOKED, or RETIRED.")
    if normalized["signature_algorithm"] not in SIGNATURE_ALGORITHMS:
        raise ValueError("Retirement signature algorithm is not supported.")
    return normalized


def validate_authorization_record(record: Any) -> dict[str, Any]:
    """Validate a signed ACTIVE record that can release a retirement gate."""

    value = _strict_mapping(record, "Activation record")
    expected = {
        "schema_version",
        "school_id",
        "active_installation_id",
        "registration_id",
        "public_hostname",
        "tunnel_id",
        "provisioning_state",
        "transfer_transaction_id",
        "confirmation_timestamp",
        "authorization_status",
        "registration_service_key_id",
        "signature_algorithm",
        "authority_signature",
    }
    _reject_unknown_fields(value, expected, "activation record")
    normalized = {
        "schema_version": str(value.get("schema_version") or ""),
        "school_id": _validate_school_id(value.get("school_id")),
        "active_installation_id": _validate_uuid(
            value.get("active_installation_id"), "Active installation ID"
        ),
        "registration_id": _required_identifier(value.get("registration_id"), "Registration ID"),
        "public_hostname": _validate_public_hostname(value.get("public_hostname")),
        "tunnel_id": _clean_text(value.get("tunnel_id"), 300),
        "provisioning_state": _clean_text(value.get("provisioning_state"), 100),
        "transfer_transaction_id": _required_identifier(
            value.get("transfer_transaction_id"), "Transfer transaction ID"
        ),
        "confirmation_timestamp": _validate_timestamp(
            value.get("confirmation_timestamp"), "Activation confirmation timestamp"
        ),
        "authorization_status": str(value.get("authorization_status") or "").strip().upper(),
        "registration_service_key_id": _required_identifier(
            value.get("registration_service_key_id"), "Registration Service key ID"
        ),
        "signature_algorithm": str(value.get("signature_algorithm") or "").strip(),
        "authority_signature": _required_identifier(
            value.get("authority_signature"), "Registration Service signature", maximum=4096
        ),
    }
    if normalized["schema_version"] != AUTHORIZATION_RECORD_SCHEMA_VERSION:
        raise ValueError("Activation record schema version is not supported.")
    if normalized["authorization_status"] != "ACTIVE":
        raise ValueError("Only an ACTIVE authorization can release retirement.")
    if normalized["signature_algorithm"] not in SIGNATURE_ALGORITHMS:
        raise ValueError("Activation signature algorithm is not supported.")
    return normalized


def canonical_authority_payload(record: Mapping[str, Any]) -> bytes:
    """Return stable bytes for Registration Service signature verification."""

    payload = {
        str(key): deepcopy(value)
        for key, value in record.items()
        if str(key) != "authority_signature"
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _strict_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} is not an object.")
    return {str(key): deepcopy(item) for key, item in value.items()}


def _reject_unknown_fields(value: Mapping[str, Any], allowed: set[str], label: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError(f"The {label} contains unsupported fields: {', '.join(unknown)}")


def _reject_secrets(value: Any, path: str = "") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).strip().casefold().replace("-", "_")
            if any(part in normalized for part in _SECRET_KEY_PARTS):
                raise ValueError(f"Gateway state must not contain secret field {path + str(key)!r}.")
            _reject_secrets(item, f"{path}{key}.")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _reject_secrets(item, f"{path}{index}.")


def _validate_school_id(value: Any) -> str:
    school_id = re.sub(r"\s+", "", str(value or "").strip())
    if not SCHOOL_ID_PATTERN.fullmatch(school_id):
        raise ValueError("School ID must contain 4 to 12 digits only.")
    return school_id


def _validate_uuid(value: Any, label: str) -> str:
    text = str(value or "").strip()
    try:
        parsed = UUID(text)
    except (ValueError, AttributeError, TypeError):
        raise ValueError(f"{label} is not a valid UUID.") from None
    if str(parsed) != text.casefold():
        raise ValueError(f"{label} must use canonical UUID form.")
    return str(parsed)


def _validate_public_hostname(value: Any) -> str:
    hostname = str(value or "").strip().casefold().rstrip(".")
    if not PUBLIC_HOST_PATTERN.fullmatch(hostname):
        raise ValueError("The Internet Gateway public hostname is invalid.")
    return hostname


def _validate_gateway_status(value: Any) -> str:
    status = str(value or "").strip().casefold().replace(" ", "_")
    if status not in GATEWAY_STATUSES:
        raise ValueError("Internet Gateway status is not recognized.")
    return status


def _validate_authorization_status(value: Any) -> str:
    status = str(value or "").strip().upper().replace(" ", "_")
    if status not in AUTHORIZATION_STATUSES:
        raise ValueError("Internet Gateway authorization status is not recognized.")
    return status


def _required_identifier(value: Any, label: str, maximum: int = 300) -> str:
    text = " ".join(str(value or "").split())
    if not text or len(text) > maximum or any(ord(character) < 32 for character in text):
        raise ValueError(f"{label} is missing or invalid.")
    return text


def _clean_text(value: Any, maximum: int) -> str:
    return " ".join(str(value or "").split())[:maximum]


def _validate_timestamp(value: Any, label: str) -> str:
    text = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"{label} is invalid.") from None
    if parsed.tzinfo is None:
        raise ValueError(f"{label} must include a timezone.")
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
