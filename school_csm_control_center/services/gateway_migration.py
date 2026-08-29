"""Encrypted, staged Server and Data migration for the Internet Gateway.

Only school-portable records are packaged.  Installation identity, retirement
state, tunnel/device credentials, Windows settings, logs, caches, and local
network configuration are deliberately excluded.  Import is a two-step
validate/commit operation and Internet authority transfer is intentionally not
performed here: the Registration Service may cut over only after a successful
commit receipt is available.
"""

from __future__ import annotations

from base64 import b64decode, b64encode
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import secrets
import shutil
import struct
import tempfile
from typing import Any, Callable, Iterable, Mapping, Protocol
from uuid import UUID, uuid4
import zipfile

from school_csm_control_center.runtime_paths import RuntimePaths, resolve_runtime_paths
from school_csm_control_center.storage.control_center_settings import (
    ControlCenterSettingsStore,
    DEFAULT_SETTINGS,
)
from school_csm_control_center.storage.dashboard_snapshot_store import DashboardSnapshotStore
from school_csm_control_center.storage.field_preset_store import FieldPresetStore
from school_csm_control_center.storage.mrs_field_tests import MRSFieldTestStore
from school_csm_control_center.storage.mrs_registry import MRSRegistryStore
from school_csm_control_center.storage.narrative_report_store import NarrativeReportStore
from school_csm_control_center.storage.print_history_store import PrintHistoryStore
from school_csm_control_center.storage.scanner_security import ScannerOperatorStore
from school_csm_control_center.storage.survey_store import SurveyStore


MIGRATION_PACKAGE_FILE_TYPE = "School CSM Encrypted Server Migration"
MIGRATION_PACKAGE_VERSION = "1.0"
MIGRATION_MANIFEST_FILE_TYPE = "School CSM Server Migration Manifest"
MIGRATION_MANIFEST_SCHEMA_VERSION = "1.0"
ENVELOPE_MAGIC = b"MOSSCMIG1\n"
MAXIMUM_HEADER_BYTES = 64 * 1024
MAXIMUM_ARCHIVE_ENTRIES = 250_000
MAXIMUM_UNCOMPRESSED_BYTES = 16 * 1024 * 1024 * 1024
EXPECTED_RECORD_COUNT_KEYS = frozenset(
    {
        "school_information",
        "survey_responses",
        "mrs_forms",
        "mrs_print_attempts",
        "mrs_print_batches",
        "mrs_scan_attempts",
        "dashboard_print_history",
        "narrative_reports",
        "mrs_field_tests",
        "scanner_operators",
        "dashboard_snapshots",
        "scanner_jobs",
        "scanner_previews",
        "field_presets",
    }
)

# Exact files and directory trees that belong to the school rather than the
# computer.  No other path below the mutable root may enter a package.
PORTABLE_FILE_PATHS = frozenset(
    {
        "data/csm_survey/control_center_settings.json",
        "data/csm_survey/school_logo.png",
        "data/csm_survey/survey_results.mossjson",
        "data/csm_survey/mrs_registry.mossjson",
        "data/csm_survey/print_history.mossjson",
        "data/csm_survey/narrative_reports.mossjson",
        "data/csm_survey/mrs_field_tests.mossjson",
        "data/csm_survey/scanner_operators.json",
        "data/csm_survey/field_presets.mossjson",
    }
)
PORTABLE_DIRECTORY_PATHS = frozenset(
    {
        "data/csm_survey/dashboard_snapshots",
        "data/csm_survey/scanner_jobs",
        "data/csm_survey/scanner_previews",
    }
)
PORTABLE_SETTINGS_KEYS = frozenset(
    {
        "school_name",
        "school_id",
        "school_region",
        "school_division",
        "school_district",
        "school_address",
        "school_email",
        "school_contact",
        "school_head",
        "csm_focal_person",
        "school_logo_path",
        "school_identifier",
        "survey_status",
        "status_message",
        "active_mode",
        "survey_date",
        "session_duration_seconds",
        "public_access_key",
        "scanner_intake_enabled",
        "scanner_remote_enabled",
        "scanner_access_key",
        "scanner_store_preview",
        "scanner_processing_limit",
        "scanner_session_inactivity_seconds",
        "scanner_session_max_seconds",
        "scanner_failed_login_limit",
        "scanner_lockout_seconds",
    }
)
MACHINE_SPECIFIC_SETTINGS_KEYS = frozenset(set(DEFAULT_SETTINGS) - set(PORTABLE_SETTINGS_KEYS))
EXCLUDED_COMPONENTS = frozenset(
    {
        "gateway",
        "logs",
        "exports",
        "backups",
        "migration",
        "locks",
        "recovery",
        "scanner_trash",
        "windows_credential_manager",
        "openai_api_key",
        "tunnel_credentials",
        "installation_identity",
        "retirement_marker",
        "firewall_state",
        "windows_startup_registration",
        "local_network_configuration",
    }
)
_IGNORED_PARTS = frozenset({".locks", "backups", "recovery", "scanner_trash"})
_IGNORED_SUFFIXES = frozenset({".tmp", ".lock", ".pyc", ".pyo"})


class GatewayMigrationError(RuntimeError):
    """Base migration error."""


class MigrationEncryptionUnavailableError(GatewayMigrationError):
    """Raised when the audited AES-GCM provider is not installed."""


class MigrationPackageValidationError(GatewayMigrationError):
    """Raised before any active data is modified."""


class MigrationCommitError(GatewayMigrationError):
    """Raised when commit or rollback cannot be completed safely."""


class MigrationCipher(Protocol):
    algorithm: str

    def encrypt_file(
        self,
        plaintext: Path,
        destination: Path,
        key: bytes,
        *,
        context: Mapping[str, Any],
    ) -> None: ...

    def decrypt_file(self, source: Path, destination: Path, key: bytes) -> dict[str, Any]: ...


class AesGcmMigrationCipher:
    """Streaming AES-256-GCM envelope backed by optional ``cryptography``.

    The project does not silently fall back to custom cryptography.  Builds
    without the provider keep local-only functionality and fail closed when an
    encrypted migration is requested.
    """

    algorithm = "AES-256-GCM"
    tag_bytes = 16

    def encrypt_file(
        self,
        plaintext: Path,
        destination: Path,
        key: bytes,
        *,
        context: Mapping[str, Any],
    ) -> None:
        Cipher, algorithms, modes = _cryptography_primitives()
        _validate_migration_key(key)
        source_size = plaintext.stat().st_size
        nonce = secrets.token_bytes(12)
        header = {
            "file_type": MIGRATION_PACKAGE_FILE_TYPE,
            "envelope_version": 1,
            "algorithm": self.algorithm,
            "nonce": b64encode(nonce).decode("ascii"),
            "plaintext_bytes": source_size,
            "context": _safe_envelope_context(context),
        }
        header_bytes = _canonical_json_bytes(header)
        if len(header_bytes) > MAXIMUM_HEADER_BYTES:
            raise GatewayMigrationError("The encrypted migration header is unexpectedly large.")
        prefix = ENVELOPE_MAGIC + struct.pack(">I", len(header_bytes)) + header_bytes
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = _temporary_sibling(destination)
        try:
            encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
            encryptor.authenticate_additional_data(prefix)
            with plaintext.open("rb") as source, temporary.open("wb") as output:
                output.write(prefix)
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    output.write(encryptor.update(chunk))
                output.write(encryptor.finalize())
                output.write(encryptor.tag)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)

    def decrypt_file(self, source: Path, destination: Path, key: bytes) -> dict[str, Any]:
        Cipher, algorithms, modes = _cryptography_primitives()
        _validate_migration_key(key)
        with source.open("rb") as stream:
            magic = stream.read(len(ENVELOPE_MAGIC))
            if magic != ENVELOPE_MAGIC:
                raise MigrationPackageValidationError("This is not a School CSM migration package.")
            length_bytes = stream.read(4)
            if len(length_bytes) != 4:
                raise MigrationPackageValidationError("The encrypted migration header is incomplete.")
            header_length = struct.unpack(">I", length_bytes)[0]
            if header_length <= 0 or header_length > MAXIMUM_HEADER_BYTES:
                raise MigrationPackageValidationError("The encrypted migration header size is invalid.")
            header_bytes = stream.read(header_length)
            try:
                header = json.loads(header_bytes.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise MigrationPackageValidationError("The encrypted migration header is unreadable.") from exc
            _validate_envelope_header(header)
            prefix = magic + length_bytes + header_bytes
            ciphertext_start = stream.tell()
            ciphertext_bytes = source.stat().st_size - ciphertext_start - self.tag_bytes
            if ciphertext_bytes != int(header["plaintext_bytes"]):
                raise MigrationPackageValidationError("The encrypted migration package size is invalid.")
            stream.seek(-self.tag_bytes, os.SEEK_END)
            tag = stream.read(self.tag_bytes)
            stream.seek(ciphertext_start)
            nonce = b64decode(header["nonce"], validate=True)
            decryptor = Cipher(algorithms.AES(key), modes.GCM(nonce, tag)).decryptor()
            decryptor.authenticate_additional_data(prefix)
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = _temporary_sibling(destination)
            remaining = ciphertext_bytes
            try:
                with temporary.open("wb") as output:
                    while remaining:
                        chunk = stream.read(min(1024 * 1024, remaining))
                        if not chunk:
                            raise MigrationPackageValidationError(
                                "The encrypted migration payload ended unexpectedly."
                            )
                        remaining -= len(chunk)
                        output.write(decryptor.update(chunk))
                    try:
                        output.write(decryptor.finalize())
                    except Exception as exc:
                        raise MigrationPackageValidationError(
                            "The migration package authentication check failed."
                        ) from exc
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)
        return deepcopy(header)


@dataclass(frozen=True)
class StagedMigration:
    transaction_id: str
    school_id: str
    source_installation_id: str
    destination_installation_id: str
    application_version: str
    package_path: Path
    transaction_root: Path
    staged_root: Path
    manifest_path: Path
    journal_path: Path
    record_counts: dict[str, int]


@dataclass(frozen=True)
class MigrationCommitReceipt:
    transaction_id: str
    school_id: str
    destination_installation_id: str
    committed_at: str
    backup_root: Path
    active_tree_sha256: str
    previous_tree_sha256: str
    journal_path: Path


class GatewayMigrationService:
    """Build, validate, atomically commit, and safely roll back school data."""

    def __init__(
        self,
        project_root: str | Path,
        *,
        data_root: str | Path | None = None,
        cipher: MigrationCipher | None = None,
    ) -> None:
        self.project_root = Path(project_root).expanduser().resolve()
        self.paths = resolve_runtime_paths(self.project_root, data_root)
        self.paths.ensure_directories()
        self.cipher = cipher or AesGcmMigrationCipher()
        self.transactions_root = self.paths.migration_dir / "server-transfer"
        self.commit_lock_path = self.paths.locks_dir / "gateway-migration-commit.lock"

    def create_package(
        self,
        destination: str | Path,
        *,
        key: bytes,
        school_id: str,
        source_installation_id: str,
        destination_installation_id: str,
        application_version: str,
        transaction_id: str | None = None,
        source_data_root: str | Path | None = None,
    ) -> dict[str, Any]:
        """Create an authenticated encrypted package from the portable allowlist."""

        school = _validate_school_id(school_id)
        source_id = _validate_uuid(source_installation_id, "Source installation ID")
        destination_id = _validate_uuid(destination_installation_id, "Destination installation ID")
        transaction = _validate_transaction_id(transaction_id or str(uuid4()))
        version = _validate_version(application_version)
        root = Path(source_data_root).expanduser().resolve() if source_data_root else self.paths.data_root
        settings = _portable_settings(root, expected_school_id=school)
        sources = _portable_sources(root)
        working = Path(tempfile.mkdtemp(prefix="school-csm-migration-create-", dir=self.paths.migration_dir))
        archive_path = working / "payload.zip"
        try:
            files: list[dict[str, Any]] = []
            with zipfile.ZipFile(
                archive_path,
                "w",
                compression=zipfile.ZIP_DEFLATED,
                compresslevel=6,
                allowZip64=True,
            ) as archive:
                settings_bytes = _canonical_json_bytes(settings) + b"\n"
                _write_zip_bytes(
                    archive,
                    "payload/data/csm_survey/control_center_settings.json",
                    settings_bytes,
                )
                files.append(
                    _manifest_entry(
                        "data/csm_survey/control_center_settings.json",
                        settings_bytes,
                        "portable_settings",
                    )
                )
                for source, logical_path in sources:
                    if logical_path == "data/csm_survey/control_center_settings.json":
                        continue
                    archive_name = "payload/" + logical_path
                    digest = hashlib.sha256()
                    size = 0
                    info = zipfile.ZipInfo(archive_name, date_time=(2026, 1, 1, 0, 0, 0))
                    info.compress_type = zipfile.ZIP_DEFLATED
                    info.create_system = 0
                    info.external_attr = 0o100600 << 16
                    with source.open("rb") as input_stream, archive.open(
                        info, "w", force_zip64=True
                    ) as output_stream:
                        for chunk in iter(lambda: input_stream.read(1024 * 1024), b""):
                            size += len(chunk)
                            digest.update(chunk)
                            output_stream.write(chunk)
                    files.append(
                        {
                            "logical_path": logical_path,
                            "archive_path": archive_name,
                            "bytes": size,
                            "sha256": digest.hexdigest(),
                            "kind": "file",
                        }
                    )

                counts = _record_counts(root, settings=settings)
                manifest = {
                    "file_type": MIGRATION_MANIFEST_FILE_TYPE,
                    "schema_version": MIGRATION_MANIFEST_SCHEMA_VERSION,
                    "migration_package_version": MIGRATION_PACKAGE_VERSION,
                    "transaction_id": transaction,
                    "school_id": school,
                    "source_installation_id": source_id,
                    "destination_installation_id": destination_id,
                    "application_version": version,
                    "created_at": _utc_now(),
                    "included_data_stores": sorted(counts),
                    "excluded_components": sorted(EXCLUDED_COMPONENTS),
                    "record_counts": counts,
                    "files": sorted(files, key=lambda row: row["logical_path"]),
                }
                manifest["manifest_sha256"] = _manifest_digest(manifest)
                _write_zip_bytes(
                    archive,
                    "manifest.json",
                    _canonical_json_bytes(manifest) + b"\n",
                )
            # Source stores are read sequentially.  Even with the Survey Server
            # stopped, a second local process could change a store between its
            # byte capture and record counting.  Validate the exact archive now
            # so an internally inconsistent package is never reported as ready.
            verification_root = working / "verification"
            verified_manifest = _extract_and_validate_archive(archive_path, verification_root)
            _validate_manifest(
                verified_manifest,
                expected_school_id=school,
                destination_installation_id=destination_id,
                current_application_version=version,
            )
            _validate_extracted_files(verification_root, verified_manifest)
            _validate_staged_store_data(verification_root, verified_manifest)
            if verified_manifest != manifest:
                raise MigrationPackageValidationError(
                    "The newly created migration manifest changed during verification."
                )
            shutil.rmtree(verification_root, ignore_errors=True)
            destination_path = Path(destination).expanduser()
            if _has_reparse_component(destination_path):
                raise MigrationPackageValidationError(
                    "The migration-package destination is redirected or unsafe."
                )
            destination_path = destination_path.absolute()
            self.cipher.encrypt_file(
                archive_path,
                destination_path,
                key,
                context={
                    "transaction_id": transaction,
                    "destination_installation_id": destination_id,
                },
            )
            return deepcopy(manifest)
        finally:
            shutil.rmtree(working, ignore_errors=True)

    def stage_import(
        self,
        package_path: str | Path,
        *,
        key: bytes,
        expected_school_id: str,
        destination_installation_id: str,
        current_application_version: str,
        replace_existing: bool = False,
    ) -> StagedMigration:
        """Decrypt and fully validate into an inert transaction directory."""

        school = _validate_school_id(expected_school_id)
        destination_id = _validate_uuid(destination_installation_id, "Destination installation ID")
        current_version = _validate_version(current_application_version)
        selected_package = Path(package_path).expanduser()
        if _has_reparse_component(selected_package):
            raise MigrationPackageValidationError("The selected migration package is unavailable or redirected.")
        package = selected_package.resolve()
        if not package.is_file():
            raise MigrationPackageValidationError("The selected migration package is unavailable or redirected.")

        transaction_root = self.transactions_root / ("stage-" + str(uuid4()))
        extracted_root = transaction_root / "extracted"
        archive_path = transaction_root / "decrypted.zip"
        journal_path = transaction_root / "journal.json"
        transaction_root.mkdir(parents=True, exist_ok=False)
        try:
            header = self.cipher.decrypt_file(package, archive_path, key)
            manifest = _extract_and_validate_archive(archive_path, extracted_root)
            _validate_manifest(
                manifest,
                expected_school_id=school,
                destination_installation_id=destination_id,
                current_application_version=current_version,
            )
            context = header.get("context") if isinstance(header, Mapping) else {}
            if not isinstance(context, Mapping) or set(context) != {
                "transaction_id",
                "destination_installation_id",
            }:
                raise MigrationPackageValidationError(
                    "Encrypted migration context is missing or unsupported."
                )
            context_transaction = _validate_transaction_id(context.get("transaction_id"))
            context_destination = _validate_uuid(
                context.get("destination_installation_id"),
                "Encrypted destination installation ID",
            )
            if context_transaction != manifest["transaction_id"]:
                raise MigrationPackageValidationError(
                    "Encrypted envelope transaction does not match its manifest."
                )
            if context_destination != destination_id:
                raise MigrationPackageValidationError(
                    "Encrypted package is intended for another installation."
                )
            _validate_extracted_files(extracted_root, manifest)
            _validate_staged_store_data(extracted_root, manifest)
            if not replace_existing and _destination_has_school_records(self.paths.data_root):
                raise MigrationPackageValidationError(
                    "The destination already contains School CSM records. Explicit replacement confirmation is required."
                )
            journal = {
                "file_type": "School CSM Migration Transaction",
                "schema_version": "1.0",
                "state": "validated",
                "validated_at": _utc_now(),
                "package_path": str(package),
                "manifest": manifest,
                "replace_existing": bool(replace_existing),
            }
            _atomic_json(journal_path, journal)
            return StagedMigration(
                transaction_id=manifest["transaction_id"],
                school_id=school,
                source_installation_id=manifest["source_installation_id"],
                destination_installation_id=destination_id,
                application_version=manifest["application_version"],
                package_path=package,
                transaction_root=transaction_root,
                staged_root=extracted_root,
                manifest_path=extracted_root / "manifest.json",
                journal_path=journal_path,
                record_counts=deepcopy(manifest["record_counts"]),
            )
        except Exception:
            failed = {"state": "validation_failed", "failed_at": _utc_now()}
            try:
                _atomic_json(journal_path, failed)
            except Exception:
                pass
            # A failed validation must not leave a decrypted copy of school
            # records behind.  The small failure journal contains no records.
            shutil.rmtree(extracted_root, ignore_errors=True)
            raise
        finally:
            archive_path.unlink(missing_ok=True)

    def commit_import(self, staged: StagedMigration) -> MigrationCommitReceipt:
        """Atomically activate a validated package and retain a rollback copy.

        The method changes only ``data/csm_survey``.  Gateway identity,
        Credential Manager, Windows integration, logs, exports, and backups are
        outside the swap.  Callers must stop the Survey Server/scanner workers
        first; the exclusive commit lock protects against another migration.
        """

        from school_csm_control_center.storage.file_safety import InterProcessFileLock

        transaction_root = Path(staged.transaction_root).absolute()
        try:
            _require_within(transaction_root, self.transactions_root, "Migration transaction")
            for path, label in (
                (transaction_root, "Migration transaction"),
                (Path(staged.staged_root).absolute(), "Staged migration root"),
                (Path(staged.manifest_path).absolute(), "Staged migration manifest"),
                (Path(staged.journal_path).absolute(), "Migration journal"),
            ):
                _require_within(path, transaction_root, label)
                if _has_reparse_component_within(path, transaction_root):
                    raise MigrationPackageValidationError(f"{label} is redirected or unsafe.")
        except MigrationPackageValidationError as exc:
            raise MigrationCommitError(
                "The staged migration boundary changed after validation and was not committed."
            ) from exc
        journal = _read_json(staged.journal_path, "migration journal")
        if str(journal.get("state") or "") != "validated":
            raise MigrationCommitError("Only a validated migration transaction can be committed.")
        manifest = _read_json(staged.manifest_path, "migration manifest")
        if str(manifest.get("transaction_id") or "") != staged.transaction_id:
            raise MigrationCommitError("The staged migration identity changed before commit.")
        try:
            _validate_manifest(
                manifest,
                expected_school_id=staged.school_id,
                destination_installation_id=staged.destination_installation_id,
                current_application_version=staged.application_version,
            )
            if manifest.get("source_installation_id") != staged.source_installation_id:
                raise MigrationPackageValidationError(
                    "The staged source installation identity changed before commit."
                )
            if dict(manifest.get("record_counts") or {}) != dict(staged.record_counts):
                raise MigrationPackageValidationError(
                    "The staged record counts changed before commit."
                )
            if journal.get("manifest") != manifest:
                raise MigrationPackageValidationError(
                    "The staged manifest no longer matches its validation journal."
                )
            _validate_extracted_files(staged.staged_root, manifest)
            _validate_staged_store_data(staged.staged_root, manifest)
        except MigrationPackageValidationError as exc:
            raise MigrationCommitError(
                "The staged migration changed after validation and was not committed."
            ) from exc

        lock = InterProcessFileLock(self.commit_lock_path, timeout=0.0)
        if not lock.acquire():
            raise MigrationCommitError("Another migration or data-maintenance operation is running.")
        try:
            if not bool(journal.get("replace_existing")) and _destination_has_school_records(
                self.paths.data_root
            ):
                raise MigrationCommitError(
                    "School CSM records were added after validation. Explicit replacement confirmation is required."
                )
            live = self.paths.csm_data_dir.absolute()
            if _has_reparse_component_within(live, self.paths.data_root):
                raise MigrationCommitError(
                    "The active School CSM data directory is redirected or unsafe."
                )
            candidate_root = transaction_root / "candidate"
            candidate = candidate_root / "data" / "csm_survey"
            displaced = transaction_root / "displaced-live"
            backup_root = (
                self.paths.backups_dir
                / "server-transfer"
                / staged.transaction_id
                / "data"
                / "csm_survey"
            )
            if _has_reparse_component_within(backup_root, self.paths.data_root):
                raise MigrationCommitError(
                    "The migration rollback destination is redirected or unsafe."
                )
            if candidate_root.exists() or displaced.exists() or backup_root.exists():
                raise MigrationCommitError("Migration transaction work paths already exist.")
            candidate.parent.mkdir(parents=True, exist_ok=True)
            if live.exists():
                _copy_directory_no_follow(live, candidate)
            else:
                candidate.mkdir(parents=True)
            _apply_staged_payload(
                staged.staged_root,
                candidate_root,
                existing_settings_path=candidate / "control_center_settings.json",
            )
            _validate_active_candidate(candidate_root, manifest)

            previous_digest = _tree_digest(live) if live.exists() else _empty_tree_digest()
            imported_digest = _tree_digest(candidate)
            journal.update(
                {
                    "state": "committing",
                    "commit_started_at": _utc_now(),
                    "previous_tree_sha256": previous_digest,
                    "candidate_tree_sha256": imported_digest,
                }
            )
            _atomic_json(staged.journal_path, journal)

            moved_live = False
            activated = False
            backup_created = False
            try:
                live.parent.mkdir(parents=True, exist_ok=True)
                if live.exists():
                    os.replace(live, displaced)
                    moved_live = True
                os.replace(candidate, live)
                activated = True
                if _tree_digest(live) != imported_digest:
                    raise MigrationCommitError("Activated migration data failed its final integrity check.")
                backup_root.parent.mkdir(parents=True, exist_ok=True)
                if moved_live:
                    os.replace(displaced, backup_root)
                else:
                    backup_root.mkdir(parents=True, exist_ok=False)
                backup_created = True
                committed_at = _utc_now()
                journal.update(
                    {
                        "state": "committed",
                        "committed_at": committed_at,
                        "backup_root": str(backup_root),
                        "active_tree_sha256": imported_digest,
                    }
                )
                _atomic_json(staged.journal_path, journal)
                _write_backup_receipt(
                    backup_root.parent.parent,
                    staged,
                    committed_at=committed_at,
                    active_digest=imported_digest,
                    previous_digest=previous_digest,
                )
                # The short-lived transfer package is consumed after the
                # destination tree and rollback receipt are durable.  If an
                # external policy prevents deletion, record that cleanup is
                # pending without undoing a fully verified data activation.
                try:
                    staged.package_path.unlink(missing_ok=True)
                except OSError as cleanup_error:
                    journal["package_cleanup_pending"] = True
                    journal["package_cleanup_error"] = _safe_error(cleanup_error)
                    _atomic_json(staged.journal_path, journal)
                shutil.rmtree(staged.staged_root, ignore_errors=True)
                return MigrationCommitReceipt(
                    transaction_id=staged.transaction_id,
                    school_id=staged.school_id,
                    destination_installation_id=staged.destination_installation_id,
                    committed_at=committed_at,
                    backup_root=backup_root,
                    active_tree_sha256=imported_digest,
                    previous_tree_sha256=previous_digest,
                    journal_path=staged.journal_path,
                )
            except Exception as exc:
                # If the new tree became live, move it aside before restoring.
                failed_active = transaction_root / "failed-active"
                try:
                    if activated and live.exists():
                        os.replace(live, failed_active)
                    if moved_live:
                        restore_source = (
                            displaced
                            if displaced.exists()
                            else backup_root if backup_created and backup_root.exists() else None
                        )
                        if restore_source is None:
                            raise MigrationCommitError(
                                "The previous data copy is unavailable for automatic rollback."
                            )
                        os.replace(restore_source, live)
                    elif activated:
                        # No data directory existed before this transaction.
                        live.mkdir(parents=True, exist_ok=False)
                        if backup_created:
                            shutil.rmtree(backup_root, ignore_errors=True)
                    journal.update(
                        {
                            "state": "commit_failed_rolled_back",
                            "failed_at": _utc_now(),
                            "error": _safe_error(exc),
                        }
                    )
                    _atomic_json(staged.journal_path, journal)
                except Exception as rollback_error:
                    journal.update(
                        {
                            "state": "commit_failed_manual_recovery_required",
                            "failed_at": _utc_now(),
                            "error": _safe_error(exc),
                            "rollback_error": _safe_error(rollback_error),
                            "displaced_live": str(displaced),
                            "failed_active": str(failed_active),
                        }
                    )
                    try:
                        _atomic_json(staged.journal_path, journal)
                    except Exception:
                        pass
                    raise MigrationCommitError(
                        "Migration activation failed and automatic rollback also failed. "
                        "Internet Server authority must not be transferred."
                    ) from rollback_error
                raise MigrationCommitError(
                    "Migration activation failed; the previous local data was restored. "
                    "Internet Server authority must not be transferred."
                ) from exc
            finally:
                shutil.rmtree(candidate_root, ignore_errors=True)
        finally:
            lock.release()

    def rollback_import(self, receipt: MigrationCommitReceipt) -> dict[str, Any]:
        """Restore a commit only if no post-import data has been written."""

        from school_csm_control_center.storage.file_safety import InterProcessFileLock

        transaction_root = Path(receipt.journal_path).absolute().parent
        try:
            _require_within(transaction_root, self.transactions_root, "Migration transaction")
            if _has_reparse_component_within(
                Path(receipt.journal_path).absolute(), self.transactions_root
            ):
                raise MigrationPackageValidationError(
                    "The migration rollback journal is redirected or unsafe."
                )
        except MigrationPackageValidationError as exc:
            raise MigrationCommitError(
                "The migration rollback boundary is redirected or unsafe."
            ) from exc
        journal = _read_json(receipt.journal_path, "migration journal")
        if str(journal.get("state") or "") != "committed":
            raise MigrationCommitError("This migration is not in a committed state.")
        if str(journal.get("transaction_id") or journal.get("manifest", {}).get("transaction_id") or "") != receipt.transaction_id:
            raise MigrationCommitError("Migration rollback receipt does not match the journal.")
        live = self.paths.csm_data_dir.absolute()
        backup = Path(receipt.backup_root).absolute()
        rollback_boundary = self.paths.backups_dir / "server-transfer"
        try:
            _require_within(backup, rollback_boundary, "Migration rollback")
            if _has_reparse_component_within(live, self.paths.data_root) or _has_reparse_component_within(
                backup, rollback_boundary
            ):
                raise MigrationPackageValidationError(
                    "The migration rollback data boundary is redirected or unsafe."
                )
        except MigrationPackageValidationError as exc:
            raise MigrationCommitError(
                "The migration rollback data boundary is redirected or unsafe."
            ) from exc
        if not backup.is_dir():
            raise MigrationCommitError("The pre-migration rollback copy is unavailable.")

        lock = InterProcessFileLock(self.commit_lock_path, timeout=0.0)
        if not lock.acquire():
            raise MigrationCommitError("Another migration or data-maintenance operation is running.")
        try:
            current_digest = _tree_digest(live)
            if current_digest != receipt.active_tree_sha256:
                raise MigrationCommitError(
                    "School CSM records changed after migration. Automatic rollback was refused to prevent data loss."
                )
            transaction_root = Path(receipt.journal_path).resolve().parent
            displaced = transaction_root / "rollback-imported-data"
            if displaced.exists():
                raise MigrationCommitError("Rollback work path already exists.")
            try:
                os.replace(live, displaced)
                os.replace(backup, live)
                if _tree_digest(live) != receipt.previous_tree_sha256:
                    raise MigrationCommitError("Restored pre-migration data failed integrity verification.")
                journal.update(
                    {
                        "state": "rolled_back",
                        "rolled_back_at": _utc_now(),
                        "rolled_back_imported_data": str(displaced),
                    }
                )
                _atomic_json(receipt.journal_path, journal)
                return deepcopy(journal)
            except Exception as exc:
                try:
                    if live.exists():
                        failed_restore = transaction_root / "failed-rollback-restore"
                        os.replace(live, failed_restore)
                    if displaced.exists():
                        os.replace(displaced, live)
                except Exception as restore_error:
                    raise MigrationCommitError(
                        "Rollback failed and the imported data could not be restored automatically."
                    ) from restore_error
                raise MigrationCommitError(
                    "Rollback failed; the imported data remains active."
                ) from exc
        finally:
            lock.release()

    def discard_staged_import(self, staged: StagedMigration) -> None:
        """Delete only an uncommitted transaction's inert staging directory."""

        root = Path(staged.transaction_root).resolve()
        _require_within(root, self.transactions_root, "Migration transaction")
        journal = _read_json(staged.journal_path, "migration journal")
        if str(journal.get("state") or "") in {"committing", "committed"}:
            raise MigrationCommitError("A committing or committed migration cannot be discarded.")
        shutil.rmtree(root)


def _cryptography_primitives() -> tuple[Any, Any, Any]:
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except ImportError:
        raise MigrationEncryptionUnavailableError(
            "Encrypted Server Transfer requires the audited cryptography runtime component. "
            "Repair or update the installed application before trying again."
        ) from None
    return Cipher, algorithms, modes


def _validate_migration_key(key: bytes) -> None:
    if not isinstance(key, bytes) or len(key) != 32:
        raise ValueError("A migration transaction requires exactly 32 bytes of key material.")


def _safe_envelope_context(context: Mapping[str, Any]) -> dict[str, str]:
    allowed = {"transaction_id", "destination_installation_id"}
    unknown = set(str(key) for key in context) - allowed
    if unknown:
        raise ValueError("Encrypted migration context contains unsupported metadata.")
    return {key: str(context.get(key) or "") for key in sorted(allowed)}


def _validate_envelope_header(header: Any) -> None:
    if not isinstance(header, Mapping):
        raise MigrationPackageValidationError("The encrypted migration header is not an object.")
    if set(header) != {
        "file_type",
        "envelope_version",
        "algorithm",
        "nonce",
        "plaintext_bytes",
        "context",
    }:
        raise MigrationPackageValidationError(
            "The encrypted migration header contains unsupported fields."
        )
    if header.get("file_type") != MIGRATION_PACKAGE_FILE_TYPE or header.get("envelope_version") != 1:
        raise MigrationPackageValidationError("The encrypted migration envelope version is unsupported.")
    if header.get("algorithm") != AesGcmMigrationCipher.algorithm:
        raise MigrationPackageValidationError("The migration encryption algorithm is unsupported.")
    try:
        nonce = b64decode(str(header.get("nonce") or ""), validate=True)
        raw_size = header.get("plaintext_bytes")
        if isinstance(raw_size, bool):
            raise ValueError
        size = int(raw_size)
    except (ValueError, TypeError):
        raise MigrationPackageValidationError("The encrypted migration header is invalid.") from None
    context = header.get("context")
    if (
        len(nonce) != 12
        or size <= 0
        or not isinstance(context, Mapping)
        or set(context) != {"transaction_id", "destination_installation_id"}
    ):
        raise MigrationPackageValidationError("The encrypted migration header is invalid.")


def _portable_sources(data_root: Path) -> list[tuple[Path, str]]:
    sources: list[tuple[Path, str]] = []
    for logical in sorted(PORTABLE_FILE_PATHS):
        path = data_root / PurePosixPath(logical)
        if path.is_file() or path.is_symlink():
            _require_regular_portable_source(path, data_root)
            sources.append((path.resolve(), logical))
    for logical_root in sorted(PORTABLE_DIRECTORY_PATHS):
        configured_root = data_root / PurePosixPath(logical_root)
        if _is_reparse(configured_root):
            raise MigrationPackageValidationError(
                f"Portable data directory is redirected: {configured_root}"
            )
        if not configured_root.is_dir():
            continue
        root = configured_root.resolve()
        _require_within(root, data_root, "Portable data")
        for path in sorted(root.rglob("*")):
            if _is_reparse(path):
                raise MigrationPackageValidationError(
                    f"Portable data contains a redirected object: {path}"
                )
            if not path.is_file() and not path.is_symlink():
                continue
            relative = path.relative_to(data_root)
            if any(part in _IGNORED_PARTS for part in relative.parts):
                continue
            if path.suffix.casefold() in _IGNORED_SUFFIXES:
                continue
            _require_regular_portable_source(path, data_root)
            sources.append((path.resolve(), relative.as_posix()))
    return sources


def _portable_settings(data_root: Path, *, expected_school_id: str) -> dict[str, Any]:
    path = data_root / "data" / "csm_survey" / "control_center_settings.json"
    if not path.is_file():
        raise MigrationPackageValidationError("School Information is unavailable for migration.")
    settings = _read_json(path, "Control Center settings")
    school_id = _validate_school_id(settings.get("school_id"))
    if school_id != expected_school_id:
        raise MigrationPackageValidationError("School Information does not match the transfer School ID.")
    return {key: deepcopy(settings[key]) for key in PORTABLE_SETTINGS_KEYS if key in settings}


def _manifest_entry(logical_path: str, content: bytes, kind: str) -> dict[str, Any]:
    return {
        "logical_path": logical_path,
        "archive_path": "payload/" + logical_path,
        "bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "kind": kind,
    }


def _write_zip_bytes(archive: zipfile.ZipFile, name: str, content: bytes) -> None:
    info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 0
    info.external_attr = 0o100600 << 16
    archive.writestr(info, content)


def _record_counts(data_root: Path, *, settings: Mapping[str, Any]) -> dict[str, int]:
    csm = data_root / "data" / "csm_survey"
    counts = {"school_information": 1 if settings.get("school_id") else 0}
    specifications = {
        "survey_responses": (csm / "survey_results.mossjson", "records"),
        "mrs_forms": (csm / "mrs_registry.mossjson", "forms"),
        "mrs_print_attempts": (csm / "mrs_registry.mossjson", "print_attempts"),
        "mrs_print_batches": (csm / "mrs_registry.mossjson", "print_batches"),
        "mrs_scan_attempts": (csm / "mrs_registry.mossjson", "scan_attempts"),
        "dashboard_print_history": (csm / "print_history.mossjson", "records"),
        "narrative_reports": (csm / "narrative_reports.mossjson", "records"),
        "mrs_field_tests": (csm / "mrs_field_tests.mossjson", "tests"),
        "scanner_operators": (csm / "scanner_operators.json", "accounts"),
    }
    parsed_cache: dict[Path, dict[str, Any]] = {}
    for label, (path, key) in specifications.items():
        if not path.is_file():
            counts[label] = 0
            continue
        document = parsed_cache.setdefault(path, _read_json(path, path.name))
        rows = document.get(key, [])
        if not isinstance(rows, list):
            raise MigrationPackageValidationError(f"{path.name} value {key!r} is not a list.")
        counts[label] = len(rows)
    counts["dashboard_snapshots"] = sum(1 for _ in (csm / "dashboard_snapshots").glob("*/manifest.mossjson"))
    counts["scanner_jobs"] = sum(1 for _ in (csm / "scanner_jobs").glob("*/job.json"))
    counts["scanner_previews"] = sum(
        1
        for path in (csm / "scanner_previews").rglob("*")
        if path.is_file()
        and not any(part in _IGNORED_PARTS for part in path.relative_to(csm).parts)
        and path.suffix.casefold() not in _IGNORED_SUFFIXES
    )
    fields_path = csm / "field_presets.mossjson"
    fields = _read_json(fields_path, fields_path.name).get("fields", {}) if fields_path.is_file() else {}
    counts["field_presets"] = len(fields) if isinstance(fields, Mapping) else 0
    return counts


def _extract_and_validate_archive(archive_path: Path, destination: Path) -> dict[str, Any]:
    destination.mkdir(parents=True, exist_ok=False)
    destination_prefix = str(destination.resolve()) + os.sep
    declared_bytes = 0
    output_paths: set[str] = set()
    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            infos = archive.infolist()
            if not infos or len(infos) > MAXIMUM_ARCHIVE_ENTRIES:
                raise MigrationPackageValidationError("Migration archive entry count is invalid.")
            names = {info.filename for info in infos}
            if "manifest.json" not in names:
                raise MigrationPackageValidationError("Migration manifest is missing.")
            for info in infos:
                _validate_archive_name(info)
                declared_bytes += int(info.file_size)
                if declared_bytes > MAXIMUM_UNCOMPRESSED_BYTES:
                    raise MigrationPackageValidationError("Migration archive is unexpectedly large.")
                output = (destination / PurePosixPath(info.filename)).resolve()
                if not str(output).startswith(destination_prefix):
                    raise MigrationPackageValidationError("Migration archive contains an unsafe parent path.")
                key = os.path.normcase(str(output))
                if key in output_paths:
                    raise MigrationPackageValidationError("Migration archive contains duplicate paths.")
                output_paths.add(key)
                output.parent.mkdir(parents=True, exist_ok=True)
                actual_bytes = 0
                digest = hashlib.sha256()
                with archive.open(info, "r") as source, output.open("xb") as target:
                    for chunk in iter(lambda: source.read(1024 * 1024), b""):
                        actual_bytes += len(chunk)
                        if actual_bytes > int(info.file_size):
                            raise MigrationPackageValidationError(
                                "Migration archive entry expanded beyond its declared size."
                            )
                        digest.update(chunk)
                        target.write(chunk)
                if actual_bytes != int(info.file_size):
                    raise MigrationPackageValidationError("Migration archive entry is incomplete.")
    except (zipfile.BadZipFile, OSError) as exc:
        raise MigrationPackageValidationError("The decrypted migration archive is invalid.") from exc
    manifest = _read_json(destination / "manifest.json", "migration manifest")
    return manifest


def _validate_archive_name(info: zipfile.ZipInfo) -> None:
    name = str(info.filename or "")
    path = PurePosixPath(name)
    if (
        not name
        or "\\" in name
        or "\x00" in name
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or name.endswith("/")
    ):
        raise MigrationPackageValidationError("Migration archive contains an unsafe path.")
    unix_mode = (int(info.external_attr) >> 16) & 0xFFFF
    if unix_mode & 0o170000 == 0o120000 or int(info.external_attr) & 0x0400:
        raise MigrationPackageValidationError("Migration archive contains a link or reparse point.")
    if name != "manifest.json" and not name.startswith("payload/data/csm_survey/"):
        raise MigrationPackageValidationError("Migration archive contains a non-portable component.")


def _validate_manifest(
    manifest: Mapping[str, Any],
    *,
    expected_school_id: str,
    destination_installation_id: str,
    current_application_version: str,
) -> None:
    required = {
        "file_type",
        "schema_version",
        "migration_package_version",
        "transaction_id",
        "school_id",
        "source_installation_id",
        "destination_installation_id",
        "application_version",
        "created_at",
        "included_data_stores",
        "excluded_components",
        "record_counts",
        "files",
        "manifest_sha256",
    }
    if set(manifest) != required:
        raise MigrationPackageValidationError("Migration manifest fields are incomplete or unsupported.")
    if manifest.get("file_type") != MIGRATION_MANIFEST_FILE_TYPE:
        raise MigrationPackageValidationError("Migration manifest type is not recognized.")
    if str(manifest.get("schema_version") or "") != MIGRATION_MANIFEST_SCHEMA_VERSION:
        raise MigrationPackageValidationError("Migration manifest schema is not supported.")
    if str(manifest.get("migration_package_version") or "") != MIGRATION_PACKAGE_VERSION:
        raise MigrationPackageValidationError("Migration package version is not supported.")
    if str(manifest.get("manifest_sha256") or "") != _manifest_digest(manifest):
        raise MigrationPackageValidationError("Migration manifest integrity check failed.")
    if _validate_school_id(manifest.get("school_id")) != expected_school_id:
        raise MigrationPackageValidationError("Migration package belongs to a different School ID.")
    if _validate_uuid(manifest.get("destination_installation_id"), "Destination installation ID") != destination_installation_id:
        raise MigrationPackageValidationError("Migration package is intended for another installation.")
    _validate_uuid(manifest.get("source_installation_id"), "Source installation ID")
    _validate_transaction_id(manifest.get("transaction_id"))
    package_version = _validate_version(manifest.get("application_version"))
    current = _validate_version(current_application_version)
    if _version_tuple(package_version)[0] != _version_tuple(current)[0] or _version_tuple(package_version) > _version_tuple(current):
        raise MigrationPackageValidationError(
            "Migration package requires an incompatible or newer application version."
        )
    _validate_timestamp(manifest.get("created_at"), "Migration timestamp")
    included = manifest.get("included_data_stores")
    if (
        not isinstance(included, list)
        or any(not isinstance(item, str) for item in included)
        or len(set(included)) != len(included)
        or set(included) != set(EXPECTED_RECORD_COUNT_KEYS)
    ):
        raise MigrationPackageValidationError("Migration included-store list is invalid.")
    if set(manifest.get("excluded_components") or ()) != set(EXCLUDED_COMPONENTS):
        raise MigrationPackageValidationError("Migration exclusion policy does not match this application.")
    counts = manifest.get("record_counts")
    if not isinstance(counts, Mapping) or set(counts) != set(EXPECTED_RECORD_COUNT_KEYS):
        raise MigrationPackageValidationError("Migration record counts are invalid.")
    for count in counts.values():
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise MigrationPackageValidationError("Migration record counts are invalid.")
    files = manifest.get("files")
    if not isinstance(files, list) or not files or len(files) > MAXIMUM_ARCHIVE_ENTRIES - 1:
        raise MigrationPackageValidationError("Migration package contains no portable files.")
    logical_paths: set[str] = set()
    archive_paths: set[str] = set()
    settings_found = False
    for entry in files:
        if not isinstance(entry, Mapping) or set(entry) != {
            "logical_path", "archive_path", "bytes", "sha256", "kind"
        }:
            raise MigrationPackageValidationError("Migration file manifest entry is invalid.")
        logical = str(entry.get("logical_path") or "")
        archive = str(entry.get("archive_path") or "")
        if not _is_portable_logical_path(logical) or archive != "payload/" + logical:
            raise MigrationPackageValidationError("Migration manifest includes a non-portable path.")
        if logical in logical_paths or archive in archive_paths:
            raise MigrationPackageValidationError("Migration manifest contains duplicate file paths.")
        logical_paths.add(logical)
        archive_paths.add(archive)
        byte_count = entry.get("bytes")
        if (
            isinstance(byte_count, bool)
            or not isinstance(byte_count, int)
            or byte_count < 0
            or not _is_sha256(entry.get("sha256"))
        ):
            raise MigrationPackageValidationError("Migration file checksum metadata is invalid.")
        if entry.get("kind") not in {"file", "portable_settings"}:
            raise MigrationPackageValidationError("Migration file kind is unsupported.")
        if logical == "data/csm_survey/control_center_settings.json":
            settings_found = entry.get("kind") == "portable_settings"
        elif entry.get("kind") != "file":
            raise MigrationPackageValidationError(
                "Only the filtered Control Center settings may be marked as portable settings."
            )
    if not settings_found:
        raise MigrationPackageValidationError("Portable School Information is missing.")


def _validate_extracted_files(root: Path, manifest: Mapping[str, Any]) -> None:
    expected = {"manifest.json"}
    for entry in manifest["files"]:
        archive_path = str(entry["archive_path"])
        expected.add(archive_path)
        path = root / PurePosixPath(archive_path)
        _require_within(path, root, "Staged migration file")
        if _has_reparse_component_within(path, root) or not path.is_file():
            raise MigrationPackageValidationError("A staged migration file is missing or redirected.")
        if path.stat().st_size != int(entry["bytes"]) or _sha256_file(path) != str(entry["sha256"]):
            raise MigrationPackageValidationError(
                f"Staged migration file failed integrity verification: {entry['logical_path']}"
            )
    actual: set[str] = set()
    for path in root.rglob("*"):
        if _is_reparse(path):
            raise MigrationPackageValidationError(
                "The staged migration tree contains a redirected object."
            )
        if (
            path.is_file()
            and not any(part in _IGNORED_PARTS for part in path.relative_to(root).parts)
            and path.suffix.casefold() not in _IGNORED_SUFFIXES
        ):
            actual.add(path.relative_to(root).as_posix())
    if actual != expected:
        raise MigrationPackageValidationError("Migration archive has unmanifested or missing files.")


def _validate_staged_store_data(root: Path, manifest: Mapping[str, Any]) -> None:
    payload_root = root / "payload"
    settings_path = payload_root / "data" / "csm_survey" / "control_center_settings.json"
    settings = _read_json(settings_path, "portable Control Center settings")
    if set(settings) - set(PORTABLE_SETTINGS_KEYS):
        raise MigrationPackageValidationError("Portable settings contain machine-specific fields.")
    if _validate_school_id(settings.get("school_id")) != manifest["school_id"]:
        raise MigrationPackageValidationError("Portable settings School ID does not match the manifest.")
    calculated = _record_counts(payload_root, settings=settings)
    expected_counts = {str(key): int(value) for key, value in manifest["record_counts"].items()}
    if calculated != expected_counts:
        raise MigrationPackageValidationError("Migration record counts do not match the manifest.")
    _validate_store_instances(payload_root)


def _validate_active_candidate(candidate_root: Path, manifest: Mapping[str, Any]) -> None:
    settings = ControlCenterSettingsStore(candidate_root, data_root=candidate_root).load()
    if _validate_school_id(settings.get("school_id")) != manifest["school_id"]:
        raise MigrationPackageValidationError("Candidate School Information does not match the migration.")
    _validate_store_instances(candidate_root)
    calculated_counts = _record_counts(candidate_root, settings=settings)
    expected_counts = {
        str(key): int(value) for key, value in manifest["record_counts"].items()
    }
    if calculated_counts != expected_counts:
        raise MigrationPackageValidationError(
            "Candidate data counts do not match the validated migration package."
        )
    csm = candidate_root / "data" / "csm_survey"
    for snapshot in (csm / "dashboard_snapshots").glob("*/manifest.mossjson"):
        reference = _read_json(snapshot, "Dashboard snapshot manifest")
        snapshot_id = str(reference.get("snapshot_id") or snapshot.parent.name)
        DashboardSnapshotStore(candidate_root, data_root=candidate_root).load(snapshot_id)


def _validate_store_instances(data_root: Path) -> None:
    checks: list[tuple[Any, Callable[[], Any]]] = []
    specifications: list[tuple[str, Any, str]] = [
        ("survey_results.mossjson", SurveyStore(data_root, data_root=data_root), "list"),
        ("mrs_registry.mossjson", MRSRegistryStore(data_root, data_root=data_root), "list_forms"),
        ("print_history.mossjson", PrintHistoryStore(data_root, data_root=data_root), "list"),
        ("narrative_reports.mossjson", NarrativeReportStore(data_root, data_root=data_root), "list"),
        ("mrs_field_tests.mossjson", MRSFieldTestStore(data_root, data_root=data_root), "list"),
        ("scanner_operators.json", ScannerOperatorStore(data_root, data_root=data_root), "list_accounts"),
        ("field_presets.mossjson", FieldPresetStore(data_root, data_root=data_root), "load"),
    ]
    csm = data_root / "data" / "csm_survey"
    for filename, store, method in specifications:
        if not (csm / filename).is_file():
            continue
        getattr(store, method)()
        if getattr(store, "last_read_error", None):
            raise MigrationPackageValidationError(f"Staged store is unreadable: {filename}")


def _apply_staged_payload(
    staged_root: Path,
    candidate_root: Path,
    *,
    existing_settings_path: Path,
) -> None:
    payload = staged_root / "payload" / "data" / "csm_survey"
    candidate = candidate_root / "data" / "csm_survey"
    portable_settings = _read_json(payload / "control_center_settings.json", "portable settings")
    current_settings: dict[str, Any] = {}
    if existing_settings_path.is_file():
        current_settings = _read_json(existing_settings_path, "destination settings")
    merged = deepcopy(DEFAULT_SETTINGS)
    merged.update({key: deepcopy(value) for key, value in current_settings.items() if key in DEFAULT_SETTINGS})
    merged.update({key: deepcopy(value) for key, value in portable_settings.items() if key in PORTABLE_SETTINGS_KEYS})

    # Exact transferred directories replace destination copies; unrelated
    # destination data remains only when it is outside the portable allowlist.
    for directory in PORTABLE_DIRECTORY_PATHS:
        relative = PurePosixPath(directory).relative_to(PurePosixPath("data/csm_survey"))
        target = candidate / relative
        if target.exists():
            shutil.rmtree(target)
    # An absent allowlisted store in the source means that store is empty.  It
    # must not leave another school's destination records behind.
    for logical in PORTABLE_FILE_PATHS:
        if logical == "data/csm_survey/control_center_settings.json":
            continue
        relative = PurePosixPath(logical).relative_to(PurePosixPath("data/csm_survey"))
        target = candidate / relative
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        elif target.exists() or target.is_symlink():
            target.unlink()
    for source in sorted(payload.rglob("*")):
        if _is_reparse(source):
            raise MigrationCommitError(
                f"Staged migration data contains a redirected object: {source}"
            )
        if not source.is_file():
            continue
        relative = source.relative_to(payload)
        target = candidate / relative
        if source.name == "control_center_settings.json":
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    _atomic_json(candidate / "control_center_settings.json", merged)


def _destination_has_school_records(data_root: Path) -> bool:
    csm = data_root / "data" / "csm_survey"
    for filename, keys in (
        ("survey_results.mossjson", ("records",)),
        (
            "mrs_registry.mossjson",
            ("forms", "print_attempts", "print_batches", "scan_attempts"),
        ),
        ("print_history.mossjson", ("records",)),
        ("narrative_reports.mossjson", ("records",)),
        ("mrs_field_tests.mossjson", ("tests",)),
        ("scanner_operators.json", ("accounts",)),
    ):
        path = csm / filename
        if path.is_file():
            document = _read_json(path, filename)
            for key in keys:
                value = document.get(key, [])
                if isinstance(value, list) and value:
                    return True
    fields_path = csm / "field_presets.mossjson"
    if fields_path.is_file():
        fields = _read_json(fields_path, fields_path.name).get("fields", {})
        if isinstance(fields, Mapping) and fields:
            return True
    if (csm / "school_logo.png").is_file():
        return True
    for directory in PORTABLE_DIRECTORY_PATHS:
        portable_root = csm / PurePosixPath(directory).relative_to(
            PurePosixPath("data/csm_survey")
        )
        if not portable_root.is_dir():
            continue
        for path in portable_root.rglob("*"):
            relative = path.relative_to(csm)
            if _is_reparse(path):
                # A redirected object is both meaningful existing state and an
                # unsafe replacement boundary; never silently remove it.
                return True
            if (
                path.is_file()
                and not any(part in _IGNORED_PARTS for part in relative.parts)
                and path.suffix.casefold() not in _IGNORED_SUFFIXES
            ):
                return True
    return False


def _write_backup_receipt(
    backup_transaction_root: Path,
    staged: StagedMigration,
    *,
    committed_at: str,
    active_digest: str,
    previous_digest: str,
) -> None:
    _atomic_json(
        backup_transaction_root / "migration-receipt.json",
        {
            "file_type": "School CSM Migration Rollback Receipt",
            "schema_version": "1.0",
            "transaction_id": staged.transaction_id,
            "school_id": staged.school_id,
            "source_installation_id": staged.source_installation_id,
            "destination_installation_id": staged.destination_installation_id,
            "committed_at": committed_at,
            "active_tree_sha256": active_digest,
            "previous_tree_sha256": previous_digest,
            "record_counts": staged.record_counts,
        },
    )


def _copy_directory_no_follow(source: Path, destination: Path) -> None:
    source = source.absolute()
    if _is_reparse(source):
        raise MigrationCommitError(f"Data directory is a link or reparse point: {source}")
    destination.mkdir(parents=True, exist_ok=False)
    for child in source.iterdir():
        if _is_reparse(child):
            raise MigrationCommitError(f"Data contains a link or reparse point: {child}")
        target = destination / child.name
        if child.is_dir():
            _copy_directory_no_follow(child, target)
        elif child.is_file():
            shutil.copy2(child, target)


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    if not root.exists():
        return _empty_tree_digest()
    if _is_reparse(root):
        raise MigrationCommitError(f"Data tree is redirected: {root}")
    _require_within(root, root, "Data tree")
    for path in sorted(root.rglob("*")):
        if _is_reparse(path):
            raise MigrationCommitError(f"Data tree contains a redirected object: {path}")
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        # Lock bytes are process state, not records; excluding them keeps the
        # rollback guard stable across store reads.
        if ".locks" in path.relative_to(root).parts or path.suffix.casefold() == ".lock":
            continue
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(path.stat().st_size).encode("ascii"))
        digest.update(b"\0")
        digest.update(_sha256_file(path).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _empty_tree_digest() -> str:
    return hashlib.sha256(b"").hexdigest()


def _is_portable_logical_path(value: str) -> bool:
    path = PurePosixPath(str(value or ""))
    if (
        path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or any(part in _IGNORED_PARTS for part in path.parts)
        or path.suffix.casefold() in _IGNORED_SUFFIXES
    ):
        return False
    normalized = path.as_posix()
    if normalized in PORTABLE_FILE_PATHS:
        return True
    return any(normalized.startswith(root + "/") for root in PORTABLE_DIRECTORY_PATHS)


def _require_regular_portable_source(path: Path, root: Path) -> None:
    _require_within(path, root, "Portable data file")
    boundary = root.resolve()
    current = path
    while current.resolve() != boundary:
        if _is_reparse(current):
            raise MigrationPackageValidationError(
                f"Portable data file is redirected or unavailable: {path}"
            )
        parent = current.parent
        if parent == current:
            break
        current = parent
    if not path.is_file():
        raise MigrationPackageValidationError(
            f"Portable data file is redirected or unavailable: {path}"
        )


def _require_within(path: Path, parent: Path, label: str) -> None:
    resolved = path.resolve()
    allowed = parent.resolve()
    try:
        resolved.relative_to(allowed)
    except ValueError:
        raise MigrationPackageValidationError(f"{label} escaped its approved data directory.") from None


def _is_reparse(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        return bool(path.lstat().st_file_attributes & 0x400)
    except FileNotFoundError:
        return False
    except AttributeError:
        return False
    except OSError:
        return True


def _has_reparse_component(path: Path) -> bool:
    """Return true when the selected path or any ancestor is redirected."""

    candidate = Path(path).absolute()
    while True:
        if _is_reparse(candidate):
            return True
        parent = candidate.parent
        if parent == candidate:
            return False
        candidate = parent


def _has_reparse_component_within(path: Path, boundary: Path) -> bool:
    """Reject a redirected object between ``path`` and an approved boundary."""

    candidate = Path(path).absolute()
    limit = Path(boundary).absolute()
    try:
        candidate.relative_to(limit)
    except ValueError:
        return True
    while True:
        if _is_reparse(candidate):
            return True
        if candidate == limit:
            return False
        parent = candidate.parent
        if parent == candidate:
            return True
        candidate = parent


def _manifest_digest(manifest: Mapping[str, Any]) -> str:
    payload = {str(key): deepcopy(value) for key, value in manifest.items() if key != "manifest_sha256"}
    return hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = _temporary_sibling(path)
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        json.loads(temporary.read_text(encoding="utf-8"))
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _temporary_sibling(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    os.close(descriptor)
    temporary = Path(name)
    temporary.unlink(missing_ok=True)
    return temporary


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MigrationPackageValidationError(f"The {label} could not be read.") from exc
    if not isinstance(value, dict):
        raise MigrationPackageValidationError(f"The {label} is not an object.")
    return value


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_sha256(value: Any) -> bool:
    text = str(value or "")
    return len(text) == 64 and all(character in "0123456789abcdef" for character in text)


def _validate_school_id(value: Any) -> str:
    text = "".join(str(value or "").split())
    if not text.isdigit() or not 4 <= len(text) <= 12:
        raise MigrationPackageValidationError("School ID must contain 4 to 12 digits.")
    return text


def _validate_uuid(value: Any, label: str) -> str:
    text = str(value or "").strip()
    try:
        parsed = UUID(text)
    except (ValueError, TypeError, AttributeError):
        raise MigrationPackageValidationError(f"{label} is invalid.") from None
    if str(parsed) != text.casefold():
        raise MigrationPackageValidationError(f"{label} must use canonical UUID form.")
    return str(parsed)


def _validate_transaction_id(value: Any) -> str:
    return _validate_uuid(value, "Migration transaction ID")


def _validate_version(value: Any) -> str:
    text = str(value or "").strip()
    pieces = text.split(".")
    if len(pieces) != 3 or any(not piece.isdigit() for piece in pieces):
        raise MigrationPackageValidationError("Application version is invalid.")
    return ".".join(str(int(piece)) for piece in pieces)


def _version_tuple(value: str) -> tuple[int, int, int]:
    pieces = value.split(".")
    return int(pieces[0]), int(pieces[1]), int(pieces[2])


def _validate_timestamp(value: Any, label: str) -> str:
    text = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise MigrationPackageValidationError(f"{label} is invalid.") from None
    if parsed.tzinfo is None:
        raise MigrationPackageValidationError(f"{label} must include a timezone.")
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _safe_error(error: Exception) -> str:
    return " ".join(str(error or "Unknown error").split())[:800]
