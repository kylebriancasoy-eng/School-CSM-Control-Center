"""Safe local migration for a renamed direct Cloudflare Worker hostname.

This operation changes only the non-secret ``public_hostname`` stored in the
Internet Gateway registration.  The Cloudflare connector credential remains
in Windows Credential Manager and is deliberately never opened by this
module.  School records live outside the Gateway state directory and are not
read or written.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any, Mapping
from uuid import UUID, uuid4

from school_csm_control_center.runtime_paths import resolve_runtime_paths
from school_csm_control_center.storage.file_safety import (
    InterProcessRLock,
    atomic_write_json,
)
from school_csm_control_center.storage.gateway_state import GatewayStateStore


DIRECT_WORKER_PROVISIONING_STATE = "direct_worker_vpc"
BACKUP_FILE_TYPE = "School CSM Direct Worker Hostname Migration Backup"
BACKUP_SCHEMA_VERSION = "1.0"
_HOST_PATTERN = re.compile(
    r"^(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$"
)


class DirectWorkerHostnameMigrationError(RuntimeError):
    """Raised when a hostname-only migration cannot complete safely."""


@dataclass(frozen=True)
class DirectWorkerHostnameMigrationResult:
    school_id: str
    old_hostname: str
    new_hostname: str
    changed: bool
    backup_root: Path | None
    manifest_path: Path | None


class DirectWorkerHostnameMigration:
    """Validate, back up, and update one direct Worker registration.

    A lock compatible with :class:`GatewayStateStore` is held from the first
    validation through the final verification.  ``save_registration`` remains
    the only normal writer, preserving its atomic state and audit behavior.
    If that two-file operation or final verification fails, the exact verified
    state/audit backups are restored while the same lock is still held.
    """

    def __init__(
        self,
        project_root: str | Path,
        *,
        data_root: str | Path | None = None,
        state_store: GatewayStateStore | None = None,
    ) -> None:
        self.project_root = Path(project_root).expanduser().resolve()
        self.paths = resolve_runtime_paths(self.project_root, data_root)
        self.paths.ensure_directories()
        self.state_store = state_store or GatewayStateStore(
            self.project_root,
            data_root=self.paths.data_root,
        )
        # GatewayStateStore uses this same logical data path for its
        # cross-process lock.  InterProcessRLock is process-wide re-entrant, so
        # nested store calls remain safe while no other application process can
        # alter Gateway state between validation and backup.
        self._lock = InterProcessRLock(
            self.state_store.gateway_root / "gateway-state.guard"
        )

    def migrate(
        self,
        *,
        expected_school_id: Any,
        expected_old_hostname: Any,
        new_hostname: Any,
        dry_run: bool = False,
    ) -> DirectWorkerHostnameMigrationResult:
        school_id = _validate_school_id(expected_school_id)
        old_host = _validate_workers_hostname(expected_old_hostname, school_id)
        new_host = _validate_workers_hostname(new_hostname, school_id)
        if old_host == new_host:
            raise DirectWorkerHostnameMigrationError(
                "The new Worker hostname must differ from the current hostname."
            )

        with self._lock:
            before = self.state_store.load()
            _validate_current_state(
                before,
                expected_school_id=school_id,
                expected_old_hostname=old_host,
            )
            before_audit = self._load_audit_document()

            if dry_run:
                return DirectWorkerHostnameMigrationResult(
                    school_id=school_id,
                    old_hostname=old_host,
                    new_hostname=new_host,
                    changed=False,
                    backup_root=None,
                    manifest_path=None,
                )

            operation_id = str(uuid4())
            created_at = _utc_now()
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
            backup_root = (
                self.paths.backups_dir
                / "gateway-hostname-migrations"
                / f"{timestamp}-{operation_id[:8]}"
            )
            manifest_path = backup_root / "manifest.json"
            state_backup = backup_root / "gateway_state.json"
            audit_backup = backup_root / "gateway_audit.mossjson"

            try:
                state_descriptor = _backup_file(self.state_store.path, state_backup)
                audit_descriptor = _backup_file(
                    self.state_store.audit_path,
                    audit_backup,
                    allow_absent=True,
                )
                manifest = {
                    "file_type": BACKUP_FILE_TYPE,
                    "schema_version": BACKUP_SCHEMA_VERSION,
                    "operation_id": operation_id,
                    "status": "prepared",
                    "created_at": created_at,
                    "completed_at": "",
                    "school_id": school_id,
                    "old_hostname": old_host,
                    "new_hostname": new_host,
                    "state_backup": state_descriptor,
                    "audit_backup": audit_descriptor,
                }
                atomic_write_json(manifest_path, manifest, allow_nan=False)
            except Exception as exc:
                raise DirectWorkerHostnameMigrationError(
                    "The timestamped Gateway backup could not be created; the hostname was not changed."
                ) from exc

            registration = _registration(before)
            try:
                self.state_store.save_registration(
                    school_id=school_id,
                    registration_id=registration["registration_id"],
                    public_hostname=new_host,
                    tunnel_id=registration["tunnel_id"],
                    provisioning_state=registration["provisioning_state"],
                    gateway_status="disconnected",
                    authorization_status="ACTIVE",
                    installation_id=str(before.get("installation_id") or ""),
                )
                after = self.state_store.load()
                after_audit = self._load_audit_document()
                _verify_only_hostname_changed(before, after, new_host)
                _verify_audit_append(before_audit, after_audit)
                manifest["status"] = "completed"
                manifest["completed_at"] = _utc_now()
                atomic_write_json(manifest_path, manifest, allow_nan=False)
            except Exception as exc:
                rollback_succeeded = self._restore_backups(
                    state_backup=state_backup,
                    audit_backup=audit_backup,
                    audit_existed=bool(audit_descriptor["existed"]),
                )
                manifest["status"] = (
                    "rolled_back" if rollback_succeeded else "rollback_failed"
                )
                manifest["completed_at"] = _utc_now()
                try:
                    atomic_write_json(manifest_path, manifest, allow_nan=False)
                except Exception:
                    pass
                if rollback_succeeded:
                    raise DirectWorkerHostnameMigrationError(
                        "The hostname migration failed and the original Gateway state and audit were restored."
                    ) from exc
                raise DirectWorkerHostnameMigrationError(
                    "The hostname migration failed and automatic restoration was incomplete. "
                    f"Use the verified backup at {backup_root}."
                ) from exc

            return DirectWorkerHostnameMigrationResult(
                school_id=school_id,
                old_hostname=old_host,
                new_hostname=new_host,
                changed=True,
                backup_root=backup_root,
                manifest_path=manifest_path,
            )

    def _load_audit_document(self) -> dict[str, Any]:
        """Use the store's validated reader without exposing mutable state."""

        # GatewayStateStore currently has no public audit-reader because the
        # desktop UI never needs one.  This maintenance service deliberately
        # reuses its schema and secret-key validation instead of duplicating a
        # second, weaker parser.
        reader = getattr(self.state_store, "_read_audit", None)
        if not callable(reader):
            raise DirectWorkerHostnameMigrationError(
                "The installed Gateway state store cannot validate its audit file."
            )
        return deepcopy(dict(reader()))

    def _restore_backups(
        self,
        *,
        state_backup: Path,
        audit_backup: Path,
        audit_existed: bool,
    ) -> bool:
        try:
            _restore_file(state_backup, self.state_store.path)
            if audit_existed:
                _restore_file(audit_backup, self.state_store.audit_path)
            elif self.state_store.audit_path.exists():
                self.state_store.audit_path.unlink()
            # Re-read through the authoritative store validators before
            # reporting that rollback succeeded.
            self.state_store.load()
            self._load_audit_document()
            return True
        except Exception:
            return False


def _validate_current_state(
    state: Mapping[str, Any],
    *,
    expected_school_id: str,
    expected_old_hostname: str,
) -> None:
    if state.get("retirement") is not None:
        raise DirectWorkerHostnameMigrationError(
            "A retired installation cannot change its Internet address."
        )
    if str(state.get("gateway_status") or "").casefold() != "disconnected":
        raise DirectWorkerHostnameMigrationError(
            "Disconnect the Internet Gateway before changing its Worker hostname."
        )
    if str(state.get("authorization_status") or "").upper() != "ACTIVE":
        raise DirectWorkerHostnameMigrationError(
            "Only an ACTIVE authorized installation can change its Worker hostname."
        )
    installation_id = str(state.get("installation_id") or "").strip()
    try:
        if str(UUID(installation_id)) != installation_id.casefold():
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise DirectWorkerHostnameMigrationError(
            "The Gateway installation identity is invalid."
        ) from None

    registration = _registration(state)
    if registration["school_id"] != expected_school_id:
        raise DirectWorkerHostnameMigrationError(
            "The expected School ID does not match this Gateway registration."
        )
    if registration["provisioning_state"] != DIRECT_WORKER_PROVISIONING_STATE:
        raise DirectWorkerHostnameMigrationError(
            "This utility applies only to an existing direct Workers VPC gateway."
        )
    current_host = _validate_workers_hostname(
        registration["public_hostname"],
        expected_school_id,
    )
    if current_host != expected_old_hostname:
        raise DirectWorkerHostnameMigrationError(
            "The expected old hostname does not match the saved Gateway hostname."
        )
    if not registration["registration_id"]:
        raise DirectWorkerHostnameMigrationError(
            "The saved Gateway registration ID is missing."
        )
    tunnel_id = registration["tunnel_id"]
    try:
        if str(UUID(tunnel_id)) != tunnel_id.casefold():
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise DirectWorkerHostnameMigrationError(
            "The saved Cloudflare Tunnel ID is invalid."
        ) from None


def _registration(state: Mapping[str, Any]) -> dict[str, str]:
    value = state.get("registration")
    if not isinstance(value, Mapping):
        raise DirectWorkerHostnameMigrationError(
            "The saved Gateway registration is missing or invalid."
        )
    return {
        "school_id": str(value.get("school_id") or "").strip(),
        "registration_id": str(value.get("registration_id") or "").strip(),
        "public_hostname": str(value.get("public_hostname") or "").strip(),
        "tunnel_id": str(value.get("tunnel_id") or "").strip(),
        "provisioning_state": str(value.get("provisioning_state") or "").strip(),
    }


def _verify_only_hostname_changed(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    expected_new_hostname: str,
) -> None:
    before_copy = deepcopy(dict(before))
    after_copy = deepcopy(dict(after))
    before_copy.pop("updated_at", None)
    after_copy.pop("updated_at", None)
    before_registration = dict(before_copy.pop("registration", {}))
    after_registration = dict(after_copy.pop("registration", {}))
    before_registration.pop("public_hostname", None)
    after_hostname = str(after_registration.pop("public_hostname", "")).casefold()
    if after_hostname != expected_new_hostname:
        raise DirectWorkerHostnameMigrationError(
            "The saved Gateway hostname did not match the requested hostname."
        )
    if before_copy != after_copy or before_registration != after_registration:
        raise DirectWorkerHostnameMigrationError(
            "Gateway metadata other than the hostname changed unexpectedly."
        )


def _verify_audit_append(
    before: Mapping[str, Any], after: Mapping[str, Any]
) -> None:
    before_events = before.get("events")
    after_events = after.get("events")
    if not isinstance(before_events, list) or not isinstance(after_events, list):
        raise DirectWorkerHostnameMigrationError(
            "The Gateway audit event list is invalid."
        )
    if after_events[:-1] != before_events or len(after_events) != len(before_events) + 1:
        raise DirectWorkerHostnameMigrationError(
            "The Gateway audit did not record exactly one migration write."
        )
    event = after_events[-1]
    if not isinstance(event, Mapping) or event.get("event_type") != "registration_saved":
        raise DirectWorkerHostnameMigrationError(
            "The Gateway audit did not record the registration update."
        )


def _backup_file(
    source: Path,
    destination: Path,
    *,
    allow_absent: bool = False,
) -> dict[str, Any]:
    if not source.is_file():
        if allow_absent:
            return {
                "file_name": source.name,
                "existed": False,
                "bytes": 0,
                "sha256": "",
            }
        raise DirectWorkerHostnameMigrationError(
            f"The required Gateway file is missing: {source.name}."
        )
    expected_hash = _sha256(source)
    _copy_file_atomic(source, destination)
    if _sha256(destination) != expected_hash:
        raise DirectWorkerHostnameMigrationError(
            f"The Gateway backup failed verification: {source.name}."
        )
    return {
        "file_name": source.name,
        "existed": True,
        "bytes": source.stat().st_size,
        "sha256": expected_hash,
    }


def _restore_file(source: Path, destination: Path) -> None:
    if not source.is_file():
        raise DirectWorkerHostnameMigrationError(
            f"The required rollback file is missing: {source.name}."
        )
    expected_hash = _sha256(source)
    _copy_file_atomic(source, destination)
    if _sha256(destination) != expected_hash:
        raise DirectWorkerHostnameMigrationError(
            f"The restored Gateway file failed verification: {destination.name}."
        )


def _copy_file_atomic(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            with source.open("rb") as source_handle:
                shutil.copyfileobj(source_handle, temporary, length=1024 * 1024)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, destination)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _validate_school_id(value: Any) -> str:
    text = "".join(str(value or "").split())
    if not text.isdigit() or not 4 <= len(text) <= 12:
        raise DirectWorkerHostnameMigrationError(
            "School ID must contain 4 to 12 digits."
        )
    return text


def _validate_workers_hostname(value: Any, school_id: str) -> str:
    hostname = str(value or "").strip().casefold().rstrip(".")
    if not _HOST_PATTERN.fullmatch(hostname):
        raise DirectWorkerHostnameMigrationError(
            "Enter a hostname only, without https://, a path, query, or port."
        )
    if not hostname.endswith(".workers.dev"):
        raise DirectWorkerHostnameMigrationError(
            "The direct pilot hostname must end in .workers.dev."
        )
    if hostname.split(".", 1)[0] != school_id:
        raise DirectWorkerHostnameMigrationError(
            "The Worker hostname must begin with the official School ID."
        )
    return hostname


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )
