"""Portable encrypted recovery backups for Internet Gateway server transfer.

``.mossbak`` is a versioned, school-wide recovery backup that is deliberately
not bound to a destination computer.  Restore first authenticates and validates
the backup in an inert directory, then converts only its allowlisted payload to
a short-lived destination-bound ``.mossmig`` package and uses the existing
atomic migration commit/rollback boundary.

The older recovery artifacts created by individual stores, the installer, and
migration rollback remain installation-specific.  A ``.mossmig`` file remains
a destination-bound migration package and is never classified as a normal
backup by this module.
"""

from __future__ import annotations

from base64 import b64decode, b64encode
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import struct
import tempfile
from typing import Any, Mapping
from uuid import uuid4
import zipfile

from school_csm_control_center.runtime_paths import resolve_runtime_paths
from school_csm_control_center.services.gateway_migration import (
    AesGcmMigrationCipher,
    ENVELOPE_MAGIC,
    EXCLUDED_COMPONENTS,
    EXPECTED_RECORD_COUNT_KEYS,
    GatewayMigrationService,
    MAXIMUM_ARCHIVE_ENTRIES,
    MAXIMUM_HEADER_BYTES,
    MigrationCommitReceipt,
    MigrationPackageValidationError,
    PORTABLE_SETTINGS_KEYS,
    _atomic_json,
    _canonical_json_bytes,
    _cryptography_primitives,
    _extract_and_validate_archive,
    _is_portable_logical_path,
    _is_sha256,
    _manifest_digest,
    _manifest_entry,
    _portable_settings,
    _portable_sources,
    _read_json,
    _record_counts,
    _temporary_sibling,
    _validate_extracted_files,
    _validate_school_id,
    _validate_staged_store_data,
    _validate_timestamp,
    _validate_uuid,
    _validate_version,
    _version_tuple,
    _write_zip_bytes,
)


BACKUP_ASSISTED_TRANSFER_SCHEMA_VERSION = "1.0"
PORTABLE_BACKUP_EXTENSION = ".mossbak"
PORTABLE_BACKUP_MAGIC = b"MOSSCBAK1\n"
PORTABLE_BACKUP_FILE_TYPE = "School CSM Encrypted Portable Recovery Backup"
PORTABLE_BACKUP_ENVELOPE_VERSION = 1
PORTABLE_BACKUP_MANIFEST_FILE_TYPE = "School CSM Portable Recovery Backup Manifest"
PORTABLE_BACKUP_MANIFEST_SCHEMA_VERSION = "1.0"
PORTABLE_BACKUP_FORMAT_VERSION = "1.0"
BACKUP_TRANSFER_SOURCE_KIND = "portable_backup"


class BackupAssistedTransferUnavailableError(RuntimeError):
    """Raised when a selected recovery artifact cannot be used for transfer."""


@dataclass(frozen=True)
class BackupAssistedTransferCapability:
    """Machine-readable release capability shown before choosing a source."""

    schema_version: str
    supported: bool
    portable_backup_format: str | None
    reason: str
    requirements: tuple[str, ...]
    migration_package_extension: str = ".mossmig"
    portable_backup_extension: str = PORTABLE_BACKUP_EXTENSION

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BackupSourceInspection:
    """Non-mutating classification of a possible backup-assisted source."""

    schema_version: str
    source_path: str
    source_kind: str
    exists: bool
    is_ordinary_backup: bool
    can_prepare_transfer: bool
    message: str
    next_action: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PreparedPortableBackup:
    """Metadata plus a one-use in-memory handle to the generated backup key."""

    package_path: Path
    backup_id: str
    school_id: str
    source_installation_id: str
    application_version: str
    created_at: str
    manifest_sha256: str
    record_counts: dict[str, int]
    _one_time_key: bytearray = field(repr=False)
    _key_taken: bool = field(default=False, init=False, repr=False)

    def take_one_time_key_hex(self) -> str:
        """Return the 256-bit key once and zero this object's mutable buffer."""

        if self._key_taken or not self._one_time_key:
            raise RuntimeError("The portable-backup key was already revealed or discarded.")
        value = bytes(self._one_time_key).hex()
        self.discard_one_time_key()
        self._key_taken = True
        return value

    def discard_one_time_key(self) -> None:
        for index in range(len(self._one_time_key)):
            self._one_time_key[index] = 0
        self._one_time_key.clear()
        self._key_taken = True


@dataclass(frozen=True)
class BackupRestoreReceipt:
    """Mode-bound proof that a portable backup reached the atomic commit boundary."""

    backup_id: str
    backup_manifest_sha256: str
    source_installation_id: str
    migration_manifest_sha256: str
    transaction_id: str
    school_id: str
    destination_installation_id: str
    committed_at: str
    active_tree_sha256: str
    previous_tree_sha256: str
    record_counts: dict[str, int]
    backup_root: Path
    journal_path: Path

    def data_validation(self) -> dict[str, Any]:
        """Return the exact receipt shape accepted only for ``backup_assisted``."""

        return {
            "verified": True,
            "source_kind": BACKUP_TRANSFER_SOURCE_KIND,
            "backup_id": self.backup_id,
            "backup_manifest_sha256": self.backup_manifest_sha256,
            "transaction_id": self.transaction_id,
            "manifest_sha256": self.migration_manifest_sha256,
            "active_tree_sha256": self.active_tree_sha256,
            "record_counts": deepcopy(self.record_counts),
        }

    def migration_receipt(self) -> MigrationCommitReceipt:
        """Return the native receipt accepted by the verified rollback API."""

        return MigrationCommitReceipt(
            transaction_id=self.transaction_id,
            school_id=self.school_id,
            destination_installation_id=self.destination_installation_id,
            committed_at=self.committed_at,
            backup_root=self.backup_root,
            active_tree_sha256=self.active_tree_sha256,
            previous_tree_sha256=self.previous_tree_sha256,
            journal_path=self.journal_path,
        )


class AesGcmBackupCipher:
    """Streaming AES-256-GCM envelope with a backup-specific magic and context."""

    algorithm = AesGcmMigrationCipher.algorithm
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
        _validate_backup_key(key)
        safe_context = _backup_envelope_context(context)
        nonce = secrets.token_bytes(12)
        header = {
            "file_type": PORTABLE_BACKUP_FILE_TYPE,
            "envelope_version": PORTABLE_BACKUP_ENVELOPE_VERSION,
            "algorithm": self.algorithm,
            "nonce": b64encode(nonce).decode("ascii"),
            "plaintext_bytes": plaintext.stat().st_size,
            "context": safe_context,
        }
        header_bytes = _canonical_json_bytes(header)
        if len(header_bytes) > MAXIMUM_HEADER_BYTES:
            raise ValueError("The encrypted recovery-backup header is unexpectedly large.")
        prefix = PORTABLE_BACKUP_MAGIC + struct.pack(">I", len(header_bytes)) + header_bytes
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
        _validate_backup_key(key)
        with source.open("rb") as stream:
            if stream.read(len(PORTABLE_BACKUP_MAGIC)) != PORTABLE_BACKUP_MAGIC:
                raise MigrationPackageValidationError(
                    "This is not a School CSM portable recovery backup."
                )
            length_bytes = stream.read(4)
            if len(length_bytes) != 4:
                raise MigrationPackageValidationError(
                    "The encrypted recovery-backup header is incomplete."
                )
            header_length = struct.unpack(">I", length_bytes)[0]
            if header_length <= 0 or header_length > MAXIMUM_HEADER_BYTES:
                raise MigrationPackageValidationError(
                    "The encrypted recovery-backup header size is invalid."
                )
            header_bytes = stream.read(header_length)
            try:
                header = json.loads(header_bytes.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise MigrationPackageValidationError(
                    "The encrypted recovery-backup header is unreadable."
                ) from exc
            _validate_backup_envelope_header(header)
            prefix = PORTABLE_BACKUP_MAGIC + length_bytes + header_bytes
            ciphertext_start = stream.tell()
            ciphertext_bytes = source.stat().st_size - ciphertext_start - self.tag_bytes
            if ciphertext_bytes != int(header["plaintext_bytes"]):
                raise MigrationPackageValidationError(
                    "The encrypted recovery-backup package size is invalid."
                )
            stream.seek(-self.tag_bytes, os.SEEK_END)
            tag = stream.read(self.tag_bytes)
            stream.seek(ciphertext_start)
            try:
                nonce = b64decode(str(header["nonce"]), validate=True)
            except Exception as exc:
                raise MigrationPackageValidationError(
                    "The encrypted recovery-backup nonce is invalid."
                ) from exc
            decryptor = Cipher(algorithms.AES(key), modes.GCM(nonce, tag)).decryptor()
            decryptor.authenticate_additional_data(prefix)
            temporary = _temporary_sibling(destination)
            remaining = ciphertext_bytes
            try:
                with temporary.open("wb") as output:
                    while remaining:
                        chunk = stream.read(min(1024 * 1024, remaining))
                        if not chunk:
                            raise MigrationPackageValidationError(
                                "The encrypted recovery-backup payload ended unexpectedly."
                            )
                        remaining -= len(chunk)
                        output.write(decryptor.update(chunk))
                    try:
                        output.write(decryptor.finalize())
                    except Exception as exc:
                        raise MigrationPackageValidationError(
                            "The recovery-backup authentication check failed."
                        ) from exc
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)
        return deepcopy(header)


class GatewayBackupService:
    """Create and atomically restore portable encrypted School CSM backups."""

    def __init__(
        self,
        project_root: str | Path,
        *,
        data_root: str | Path | None = None,
        migration_service: GatewayMigrationService | None = None,
        cipher: AesGcmBackupCipher | None = None,
    ) -> None:
        self.project_root = Path(project_root).expanduser().resolve()
        self.paths = resolve_runtime_paths(self.project_root, data_root)
        self.paths.ensure_directories()
        self.migration = migration_service or GatewayMigrationService(
            self.project_root,
            data_root=self.paths.data_root,
        )
        self.cipher = cipher or AesGcmBackupCipher()
        self.transactions_root = self.paths.migration_dir / "backup-assisted"
        self.transactions_root.mkdir(parents=True, exist_ok=True)

    def create_backup(
        self,
        destination: str | Path,
        *,
        school_id: str,
        source_installation_id: str,
        application_version: str,
        source_data_root: str | Path | None = None,
        overwrite: bool = False,
    ) -> PreparedPortableBackup:
        """Create a validated backup and retain its fresh key only in memory."""

        school = _validate_school_id(school_id)
        source_id = _validate_uuid(source_installation_id, "Source installation ID")
        version = _validate_version(application_version)
        backup_id = _validate_uuid(str(uuid4()), "Portable backup ID")
        created_at = _utc_now()
        root = (
            Path(source_data_root).expanduser().resolve()
            if source_data_root is not None
            else self.paths.data_root
        )
        settings = _portable_settings(root, expected_school_id=school)
        sources = _portable_sources(root)
        destination_path = _portable_backup_destination(destination)
        if _is_redirected(destination_path):
            raise ValueError("The portable-backup destination is redirected or unsafe.")
        if destination_path.exists() and not overwrite:
            raise FileExistsError(
                "The selected portable-backup file already exists. Choose a new name or confirm replacement."
            )

        working = Path(
            tempfile.mkdtemp(prefix="school-csm-backup-create-", dir=self.transactions_root)
        )
        archive_path = working / "payload.zip"
        key = secrets.token_bytes(32)
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
                    "file_type": PORTABLE_BACKUP_MANIFEST_FILE_TYPE,
                    "schema_version": PORTABLE_BACKUP_MANIFEST_SCHEMA_VERSION,
                    "backup_format_version": PORTABLE_BACKUP_FORMAT_VERSION,
                    "backup_id": backup_id,
                    "school_id": school,
                    "source_installation_id": source_id,
                    "application_version": version,
                    "created_at": created_at,
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

            # Validate the exact archive before any encrypted result is exposed.
            verification_root = working / "verification"
            verified_manifest = _extract_and_validate_archive(archive_path, verification_root)
            _validate_backup_manifest(
                verified_manifest,
                expected_school_id=school,
                current_application_version=version,
            )
            _validate_extracted_files(verification_root, verified_manifest)
            _validate_staged_store_data(verification_root, verified_manifest)
            if verified_manifest != manifest:
                raise MigrationPackageValidationError(
                    "The newly created recovery-backup manifest changed during verification."
                )

            self.cipher.encrypt_file(
                archive_path,
                destination_path,
                key,
                context={"backup_id": backup_id, "school_id": school},
            )
            return PreparedPortableBackup(
                package_path=destination_path,
                backup_id=backup_id,
                school_id=school,
                source_installation_id=source_id,
                application_version=version,
                created_at=created_at,
                manifest_sha256=str(manifest["manifest_sha256"]),
                record_counts=deepcopy(counts),
                _one_time_key=bytearray(key),
            )
        finally:
            key = b""
            shutil.rmtree(working, ignore_errors=True)

    def restore_backup(
        self,
        backup_path: str | Path,
        *,
        key: bytes | str,
        expected_school_id: str,
        destination_installation_id: str,
        current_application_version: str,
        replace_existing: bool = False,
    ) -> BackupRestoreReceipt:
        """Validate a backup and activate it through migration commit/rollback."""

        selected_backup = Path(backup_path).expanduser()
        if _is_redirected(selected_backup):
            raise MigrationPackageValidationError(
                "The selected portable recovery backup is unavailable or redirected."
            )
        backup = selected_backup.resolve()
        if not backup.is_file():
            raise MigrationPackageValidationError(
                "The selected portable recovery backup is unavailable or redirected."
            )
        if not _has_backup_envelope(backup):
            raise MigrationPackageValidationError(
                "The selected file is not a School CSM portable recovery backup."
            )
        decoded_key = _normalize_backup_key(key)
        school = _validate_school_id(expected_school_id)
        destination_id = _validate_uuid(
            destination_installation_id,
            "Destination installation ID",
        )
        current_version = _validate_version(current_application_version)
        working = Path(
            tempfile.mkdtemp(prefix="school-csm-backup-restore-", dir=self.transactions_root)
        )
        archive_path = working / "decrypted.zip"
        extracted_root = working / "extracted"
        internal_package = working / "destination-bound.mossmig"
        staged = None
        internal_key = secrets.token_bytes(32)
        try:
            header = self.cipher.decrypt_file(backup, archive_path, decoded_key)
            manifest = _extract_and_validate_archive(archive_path, extracted_root)
            _validate_backup_manifest(
                manifest,
                expected_school_id=school,
                current_application_version=current_version,
            )
            context = header.get("context") if isinstance(header, Mapping) else None
            if not isinstance(context, Mapping) or set(context) != {"backup_id", "school_id"}:
                raise MigrationPackageValidationError(
                    "Encrypted recovery-backup context is missing or unsupported."
                )
            if _validate_uuid(context.get("backup_id"), "Encrypted portable backup ID") != manifest[
                "backup_id"
            ]:
                raise MigrationPackageValidationError(
                    "Encrypted recovery-backup identity does not match its manifest."
                )
            if _validate_school_id(context.get("school_id")) != school:
                raise MigrationPackageValidationError(
                    "Encrypted recovery backup belongs to a different School ID."
                )
            _validate_extracted_files(extracted_root, manifest)
            _validate_staged_store_data(extracted_root, manifest)

            transaction_id = str(uuid4())
            migration_manifest = self.migration.create_package(
                internal_package,
                key=internal_key,
                school_id=school,
                source_installation_id=manifest["source_installation_id"],
                destination_installation_id=destination_id,
                application_version=manifest["application_version"],
                transaction_id=transaction_id,
                source_data_root=extracted_root / "payload",
            )
            if migration_manifest["record_counts"] != manifest["record_counts"]:
                raise MigrationPackageValidationError(
                    "Converted migration counts do not match the validated recovery backup."
                )
            staged = self.migration.stage_import(
                internal_package,
                key=internal_key,
                expected_school_id=school,
                destination_installation_id=destination_id,
                current_application_version=current_version,
                replace_existing=replace_existing,
            )
            # Persist mode binding and backup proof before the atomic activation.
            journal = _read_json(staged.journal_path, "migration journal")
            journal["transfer_source"] = {
                "source_kind": BACKUP_TRANSFER_SOURCE_KIND,
                "backup_id": manifest["backup_id"],
                "backup_manifest_sha256": manifest["manifest_sha256"],
                "source_installation_id": manifest["source_installation_id"],
            }
            _atomic_json(staged.journal_path, journal)
            commit = self.migration.commit_import(staged)
            return _backup_restore_receipt(
                manifest=manifest,
                migration_manifest=migration_manifest,
                commit=commit,
            )
        except Exception:
            if staged is not None:
                try:
                    journal = _read_json(staged.journal_path, "migration journal")
                    if str(journal.get("state") or "") == "validated":
                        self.migration.discard_staged_import(staged)
                    elif str(journal.get("state") or "") == "commit_failed_rolled_back":
                        shutil.rmtree(staged.staged_root, ignore_errors=True)
                except Exception:
                    pass
            raise
        finally:
            decoded_key = b""
            internal_key = b""
            shutil.rmtree(working, ignore_errors=True)


class GatewayBackupAdapter:
    """Inspect and gate recovery artifacts by authenticated format, not suffix."""

    _CAPABILITY = BackupAssistedTransferCapability(
        schema_version=BACKUP_ASSISTED_TRANSFER_SCHEMA_VERSION,
        supported=True,
        portable_backup_format="School CSM Portable Recovery Backup 1.0",
        reason=(
            "This release supports encrypted .mossbak recovery backups through "
            "strict validation and the existing atomic migration commit boundary."
        ),
        requirements=(
            "a single portable backup manifest with an explicit schema version",
            "the official School ID and application-version compatibility data",
            "an allowlist of school-owned stores with SHA-256 hashes and record counts",
            "authenticated encryption and strict archive path validation",
            "staged validation, atomic activation, and verified rollback",
        ),
    )

    def capability(self) -> BackupAssistedTransferCapability:
        """Return an immutable description of support in this release."""

        return self._CAPABILITY

    def inspect_source(self, source: str | Path) -> BackupSourceInspection:
        """Classify ``source`` without extracting, decrypting, or changing it."""

        raw = str(source or "").strip()
        if not raw:
            return self._result(
                Path("."),
                source_path="",
                source_kind="not_selected",
                exists=False,
                message="No backup source was selected.",
                next_action="Select a supported recovery source only when the application offers one.",
            )

        path = Path(raw).expanduser()
        display_path = str(path)
        try:
            exists = path.exists() or path.is_symlink()
        except OSError:
            exists = False
        if not exists:
            return self._result(
                path,
                source_path=display_path,
                source_kind="missing",
                exists=False,
                message="The selected recovery source does not exist.",
                next_action="Choose an available source; no data was read or changed.",
            )
        if _is_redirected(path):
            return self._result(
                path,
                source_path=display_path,
                source_kind="unsafe_redirect",
                exists=True,
                message="Redirected links and reparse points are not accepted as recovery sources.",
                next_action="Use a regular local file produced by a supported School CSM workflow.",
            )

        if path.is_file():
            if _has_backup_envelope(path):
                return self._result(
                    path,
                    source_path=display_path,
                    source_kind="portable_encrypted_backup",
                    exists=True,
                    message=(
                        "This is an encrypted School CSM portable recovery backup. "
                        "Its key and full manifest must still pass validation before restore."
                    ),
                    next_action="Use Server and Data from backup and enter its one-time backup key.",
                    is_ordinary_backup=True,
                    can_prepare_transfer=True,
                )
            if _has_migration_envelope(path):
                return self._result(
                    path,
                    source_path=display_path,
                    source_kind="encrypted_migration_package",
                    exists=True,
                    message=(
                        "This is a destination-bound encrypted migration package, "
                        "not an ordinary School CSM backup."
                    ),
                    next_action=(
                        "Use the Server and Data migration-package workflow and its one-time key; "
                        "do not label this file as backup-assisted recovery."
                    ),
                )
            if path.name.casefold().endswith(".last-good.bak"):
                return self._result(
                    path,
                    source_path=display_path,
                    source_kind="single_store_last_good_copy",
                    exists=True,
                    message=(
                        "This is an automatic last-known-good copy of one data store, "
                        "not a complete school backup."
                    ),
                    next_action="Use it only through a store-specific recovery procedure.",
                )
            if _looks_like_zip(path):
                kind = "unrecognized_archive"
                message = (
                    "This archive has no supported School CSM portable-backup manifest and "
                    "will not be extracted."
                )
            else:
                kind = "unrecognized_file"
                message = "This file is not a supported School CSM portable backup."
            return self._result(
                path,
                source_path=display_path,
                source_kind=kind,
                exists=True,
                message=message,
                next_action="Do not continue with backup-assisted transfer using this file.",
            )

        if path.is_dir():
            if (path / "migration-receipt.json").is_file() and (
                path / "data" / "csm_survey"
            ).is_dir():
                kind = "destination_rollback_evidence"
                message = (
                    "This directory is destination-local migration rollback evidence, "
                    "not a portable backup."
                )
                action = "Use only the verified rollback operation on the installation that created it."
            elif _contains_legacy_migration_copy(path):
                kind = "legacy_or_installer_migration_copy"
                message = (
                    "This directory is a legacy or installer migration copy without a portable "
                    "full-backup manifest."
                )
                action = "Use the installation recovery procedure that created this copy."
            else:
                kind = "unrecognized_directory"
                message = "This directory is not a supported School CSM portable backup."
                action = "Do not continue with backup-assisted transfer using this directory."
            return self._result(
                path,
                source_path=display_path,
                source_kind=kind,
                exists=True,
                message=message,
                next_action=action,
            )

        return self._result(
            path,
            source_path=display_path,
            source_kind="unsupported_filesystem_object",
            exists=True,
            message="This filesystem object is not a regular file or directory.",
            next_action="Choose a regular source created by a supported School CSM workflow.",
        )

    def require_supported_source(self, source: str | Path) -> BackupSourceInspection:
        """Return only an authenticated-envelope backup; fail closed otherwise."""

        inspection = self.inspect_source(source)
        if inspection.is_ordinary_backup and inspection.can_prepare_transfer:
            return inspection
        raise BackupAssistedTransferUnavailableError(
            f"{inspection.message} {inspection.next_action}"
        )

    @staticmethod
    def _result(
        _path: Path,
        *,
        source_path: str,
        source_kind: str,
        exists: bool,
        message: str,
        next_action: str,
        is_ordinary_backup: bool = False,
        can_prepare_transfer: bool = False,
    ) -> BackupSourceInspection:
        return BackupSourceInspection(
            schema_version=BACKUP_ASSISTED_TRANSFER_SCHEMA_VERSION,
            source_path=source_path,
            source_kind=source_kind,
            exists=exists,
            is_ordinary_backup=is_ordinary_backup,
            can_prepare_transfer=can_prepare_transfer,
            message=message,
            next_action=next_action,
        )


def _has_migration_envelope(path: Path) -> bool:
    try:
        with path.open("rb") as stream:
            return stream.read(len(ENVELOPE_MAGIC)) == ENVELOPE_MAGIC
    except OSError:
        return False


def _has_backup_envelope(path: Path) -> bool:
    try:
        with path.open("rb") as stream:
            return stream.read(len(PORTABLE_BACKUP_MAGIC)) == PORTABLE_BACKUP_MAGIC
    except OSError:
        return False


def _backup_restore_receipt(
    *,
    manifest: Mapping[str, Any],
    migration_manifest: Mapping[str, Any],
    commit: MigrationCommitReceipt,
) -> BackupRestoreReceipt:
    return BackupRestoreReceipt(
        backup_id=str(manifest["backup_id"]),
        backup_manifest_sha256=str(manifest["manifest_sha256"]),
        source_installation_id=str(manifest["source_installation_id"]),
        migration_manifest_sha256=str(migration_manifest["manifest_sha256"]),
        transaction_id=str(commit.transaction_id),
        school_id=str(commit.school_id),
        destination_installation_id=str(commit.destination_installation_id),
        committed_at=str(commit.committed_at),
        active_tree_sha256=str(commit.active_tree_sha256),
        previous_tree_sha256=str(commit.previous_tree_sha256),
        record_counts=deepcopy(dict(manifest["record_counts"])),
        backup_root=Path(commit.backup_root),
        journal_path=Path(commit.journal_path),
    )


def _validate_backup_manifest(
    manifest: Mapping[str, Any],
    *,
    expected_school_id: str,
    current_application_version: str,
) -> None:
    required = {
        "file_type",
        "schema_version",
        "backup_format_version",
        "backup_id",
        "school_id",
        "source_installation_id",
        "application_version",
        "created_at",
        "included_data_stores",
        "excluded_components",
        "record_counts",
        "files",
        "manifest_sha256",
    }
    if not isinstance(manifest, Mapping) or set(manifest) != required:
        raise MigrationPackageValidationError(
            "Portable recovery-backup manifest fields are incomplete or unsupported."
        )
    if manifest.get("file_type") != PORTABLE_BACKUP_MANIFEST_FILE_TYPE:
        raise MigrationPackageValidationError(
            "Portable recovery-backup manifest type is not recognized."
        )
    if manifest.get("schema_version") != PORTABLE_BACKUP_MANIFEST_SCHEMA_VERSION:
        raise MigrationPackageValidationError(
            "Portable recovery-backup manifest schema is unsupported."
        )
    if manifest.get("backup_format_version") != PORTABLE_BACKUP_FORMAT_VERSION:
        raise MigrationPackageValidationError("Portable recovery-backup format is unsupported.")
    if str(manifest.get("manifest_sha256") or "") != _manifest_digest(manifest):
        raise MigrationPackageValidationError(
            "Portable recovery-backup manifest integrity check failed."
        )
    _validate_uuid(manifest.get("backup_id"), "Portable backup ID")
    if _validate_school_id(manifest.get("school_id")) != expected_school_id:
        raise MigrationPackageValidationError(
            "Portable recovery backup belongs to a different School ID."
        )
    _validate_uuid(manifest.get("source_installation_id"), "Source installation ID")
    backup_version = _validate_version(manifest.get("application_version"))
    current_version = _validate_version(current_application_version)
    if (
        _version_tuple(backup_version)[0] != _version_tuple(current_version)[0]
        or _version_tuple(backup_version) > _version_tuple(current_version)
    ):
        raise MigrationPackageValidationError(
            "Portable recovery backup requires an incompatible or newer application version."
        )
    _validate_timestamp(manifest.get("created_at"), "Portable backup timestamp")

    included = manifest.get("included_data_stores")
    if (
        not isinstance(included, list)
        or any(not isinstance(item, str) for item in included)
        or len(set(included)) != len(included)
        or set(included) != set(EXPECTED_RECORD_COUNT_KEYS)
    ):
        raise MigrationPackageValidationError(
            "Portable recovery-backup included-store list is invalid."
        )
    if set(manifest.get("excluded_components") or ()) != set(EXCLUDED_COMPONENTS):
        raise MigrationPackageValidationError(
            "Portable recovery-backup exclusion policy does not match this application."
        )
    counts = manifest.get("record_counts")
    if not isinstance(counts, Mapping) or set(counts) != set(EXPECTED_RECORD_COUNT_KEYS):
        raise MigrationPackageValidationError(
            "Portable recovery-backup record counts are invalid."
        )
    if any(
        isinstance(count, bool) or not isinstance(count, int) or count < 0
        for count in counts.values()
    ):
        raise MigrationPackageValidationError(
            "Portable recovery-backup record counts are invalid."
        )

    files = manifest.get("files")
    if not isinstance(files, list) or not files or len(files) > MAXIMUM_ARCHIVE_ENTRIES - 1:
        raise MigrationPackageValidationError(
            "Portable recovery backup contains no school-owned files."
        )
    logical_paths: set[str] = set()
    archive_paths: set[str] = set()
    settings_found = False
    for entry in files:
        if not isinstance(entry, Mapping) or set(entry) != {
            "logical_path",
            "archive_path",
            "bytes",
            "sha256",
            "kind",
        }:
            raise MigrationPackageValidationError(
                "Portable recovery-backup file entry is invalid."
            )
        logical = str(entry.get("logical_path") or "")
        archive = str(entry.get("archive_path") or "")
        if not _is_portable_logical_path(logical) or archive != "payload/" + logical:
            raise MigrationPackageValidationError(
                "Portable recovery-backup manifest includes a non-portable path."
            )
        if logical in logical_paths or archive in archive_paths:
            raise MigrationPackageValidationError(
                "Portable recovery-backup manifest contains duplicate paths."
            )
        logical_paths.add(logical)
        archive_paths.add(archive)
        byte_count = entry.get("bytes")
        if (
            isinstance(byte_count, bool)
            or not isinstance(byte_count, int)
            or byte_count < 0
            or not _is_sha256(entry.get("sha256"))
        ):
            raise MigrationPackageValidationError(
                "Portable recovery-backup checksum metadata is invalid."
            )
        if entry.get("kind") not in {"file", "portable_settings"}:
            raise MigrationPackageValidationError(
                "Portable recovery-backup file kind is unsupported."
            )
        if logical == "data/csm_survey/control_center_settings.json":
            settings_found = entry.get("kind") == "portable_settings"
        elif entry.get("kind") != "file":
            raise MigrationPackageValidationError(
                "Only filtered School Information may be marked as portable settings."
            )
    if not settings_found:
        raise MigrationPackageValidationError(
            "Portable recovery backup is missing filtered School Information."
        )


def _validate_backup_key(key: bytes) -> None:
    if not isinstance(key, bytes) or len(key) != 32:
        raise ValueError("A portable recovery backup requires exactly 32 bytes of key material.")


def _normalize_backup_key(value: bytes | str) -> bytes:
    if isinstance(value, bytes):
        _validate_backup_key(value)
        return value
    text = "".join(str(value or "").split())
    if len(text) != 64:
        raise ValueError("The portable-backup key must be exactly 64 hexadecimal characters.")
    try:
        decoded = bytes.fromhex(text)
    except ValueError as exc:
        raise ValueError(
            "The portable-backup key must be exactly 64 hexadecimal characters."
        ) from exc
    _validate_backup_key(decoded)
    return decoded


def _backup_envelope_context(context: Mapping[str, Any]) -> dict[str, str]:
    if not isinstance(context, Mapping) or set(context) != {"backup_id", "school_id"}:
        raise ValueError("Encrypted recovery-backup context contains unsupported metadata.")
    return {
        "backup_id": _validate_uuid(context.get("backup_id"), "Portable backup ID"),
        "school_id": _validate_school_id(context.get("school_id")),
    }


def _validate_backup_envelope_header(header: Any) -> None:
    if not isinstance(header, Mapping) or set(header) != {
        "file_type",
        "envelope_version",
        "algorithm",
        "nonce",
        "plaintext_bytes",
        "context",
    }:
        raise MigrationPackageValidationError(
            "Encrypted recovery-backup header contains unsupported fields."
        )
    if (
        header.get("file_type") != PORTABLE_BACKUP_FILE_TYPE
        or header.get("envelope_version") != PORTABLE_BACKUP_ENVELOPE_VERSION
        or header.get("algorithm") != AesGcmBackupCipher.algorithm
    ):
        raise MigrationPackageValidationError(
            "Encrypted recovery-backup envelope version or algorithm is unsupported."
        )
    try:
        nonce = b64decode(str(header.get("nonce") or ""), validate=True)
        size_value = header.get("plaintext_bytes")
        if isinstance(size_value, bool):
            raise ValueError
        size = int(size_value)
    except (ValueError, TypeError):
        raise MigrationPackageValidationError(
            "Encrypted recovery-backup header is invalid."
        ) from None
    context = header.get("context")
    try:
        _backup_envelope_context(context)
    except (ValueError, MigrationPackageValidationError):
        raise MigrationPackageValidationError(
            "Encrypted recovery-backup header context is invalid."
        ) from None
    if len(nonce) != 12 or size <= 0:
        raise MigrationPackageValidationError(
            "Encrypted recovery-backup header is invalid."
        )


def _portable_backup_destination(destination: str | Path) -> Path:
    text = str(destination or "").strip()
    if not text:
        raise ValueError("Choose where to save the portable recovery backup.")
    path = Path(text).expanduser()
    if path.suffix.casefold() != PORTABLE_BACKUP_EXTENSION:
        path = path.with_name(path.name + PORTABLE_BACKUP_EXTENSION)
    return path.absolute()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _looks_like_zip(path: Path) -> bool:
    try:
        with path.open("rb") as stream:
            return stream.read(4) in {b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"}
    except OSError:
        return False


def _contains_legacy_migration_copy(path: Path) -> bool:
    return (
        (path / "data" / "csm_survey").is_dir()
        or (path / "csm_survey").is_dir()
        or (path / "migration-receipt.json").is_file()
    )


def _is_redirected(path: Path) -> bool:
    """Reject a link/reparse point in the selected path or any ancestor."""

    candidate = Path(path).absolute()
    while True:
        if _path_component_is_redirected(candidate):
            return True
        parent = candidate.parent
        if parent == candidate:
            return False
        candidate = parent


def _path_component_is_redirected(path: Path) -> bool:
    try:
        status = path.lstat()
    except FileNotFoundError:
        return False
    except OSError:
        # An unreadable path component is not a safe backup boundary.
        return True
    if path.is_symlink():
        return True
    attributes = int(getattr(status, "st_file_attributes", 0) or 0)
    reparse_flag = int(getattr(os, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    return bool(attributes & reparse_flag)
