from __future__ import annotations

import json
from pathlib import Path
import secrets
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4
import zipfile

from school_csm_control_center.services.gateway_backup_adapter import (
    AesGcmBackupCipher,
    BackupAssistedTransferUnavailableError,
    GatewayBackupAdapter,
    GatewayBackupService,
    PORTABLE_BACKUP_EXTENSION,
)
from school_csm_control_center.services.gateway_migration import (
    AesGcmMigrationCipher,
    ENVELOPE_MAGIC,
    GatewayMigrationService,
    MigrationCommitError,
    MigrationPackageValidationError,
    _manifest_digest,
)


SCHOOL_ID = "123456"
SOURCE_ID = "11111111-1111-4111-8111-111111111111"
DESTINATION_ID = "22222222-2222-4222-8222-222222222222"
OTHER_DESTINATION_ID = "44444444-4444-4444-8444-444444444444"
TRANSACTION_ID = "33333333-3333-4333-8333-333333333333"


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def _school_settings(school_id: str, name: str) -> dict:
    return {
        "school_name": name,
        "school_id": school_id,
        "school_identifier": name.casefold().replace(" ", "-"),
        "survey_status": "online",
        "preferred_port": 8443,
        "network_password": f"{school_id}-device-only-secret",
        "background_server_startup_enabled": True,
    }


def _make_roots(base: Path) -> tuple[Path, Path]:
    source = base / "source"
    destination = base / "destination"
    _write_json(
        source / "data/csm_survey/control_center_settings.json",
        _school_settings(SCHOOL_ID, "Calapi Elementary School"),
    )
    _write_json(
        source / "data/csm_survey/survey_results.mossjson",
        {
            "file_type": "School CSM Control Center Results",
            "schema_version": "2.0",
            "records": [],
        },
    )
    _write_json(
        destination / "data/csm_survey/control_center_settings.json",
        _school_settings("654321", "Destination Before Transfer"),
    )
    source_preview = source / "data/csm_survey/scanner_previews/scan-source.png"
    source_preview.parent.mkdir(parents=True, exist_ok=True)
    source_preview.write_bytes(b"\x89PNG\r\nsource-scanner-preview")
    destination_preview = (
        destination / "data/csm_survey/scanner_previews/scan-before.png"
    )
    destination_preview.parent.mkdir(parents=True, exist_ok=True)
    destination_preview.write_bytes(b"\x89PNG\r\ndestination-scanner-preview")
    return source, destination


def _make_package(
    base: Path,
    *,
    key: bytes,
    destination_installation_id: str = DESTINATION_ID,
) -> tuple[GatewayMigrationService, Path, Path, Path, dict]:
    source, destination = _make_roots(base)
    service = GatewayMigrationService(base / "install", data_root=destination)
    package = base / "school-transfer.mossmig"
    manifest = service.create_package(
        package,
        key=key,
        school_id=SCHOOL_ID,
        source_installation_id=SOURCE_ID,
        destination_installation_id=destination_installation_id,
        application_version="0.5.0",
        transaction_id=TRANSACTION_ID,
        source_data_root=source,
    )
    return service, source, destination, package, manifest


def _rewrite_encrypted_archive(
    package: Path,
    *,
    key: bytes,
    mutate,
) -> None:
    cipher = AesGcmMigrationCipher()
    plain = package.with_suffix(".decrypted.zip")
    rewritten = package.with_suffix(".rewritten.zip")
    header = cipher.decrypt_file(package, plain, key)
    with zipfile.ZipFile(plain, "r") as archive:
        entries = {info.filename: archive.read(info.filename) for info in archive.infolist()}
    mutate(entries)
    with zipfile.ZipFile(
        rewritten,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        allowZip64=True,
    ) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    cipher.encrypt_file(rewritten, package, key, context=header["context"])
    plain.unlink(missing_ok=True)
    rewritten.unlink(missing_ok=True)


def _rewrite_backup_archive(
    package: Path,
    *,
    key: bytes,
    mutate,
) -> None:
    cipher = AesGcmBackupCipher()
    plain = package.with_suffix(".backup-decrypted.zip")
    rewritten = package.with_suffix(".backup-rewritten.zip")
    header = cipher.decrypt_file(package, plain, key)
    with zipfile.ZipFile(plain, "r") as archive:
        entries = {info.filename: archive.read(info.filename) for info in archive.infolist()}
    mutate(entries)
    with zipfile.ZipFile(
        rewritten,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        allowZip64=True,
    ) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    cipher.encrypt_file(rewritten, package, key, context=header["context"])
    plain.unlink(missing_ok=True)
    rewritten.unlink(missing_ok=True)


class GatewayBackupAdapterTests(unittest.TestCase):
    def test_release_reports_exact_portable_full_backup_capability(self) -> None:
        capability = GatewayBackupAdapter().capability()
        self.assertTrue(capability.supported)
        self.assertEqual(capability.portable_backup_extension, ".mossbak")
        self.assertIn("Portable Recovery Backup", capability.portable_backup_format)
        requirements = " ".join(capability.requirements).casefold()
        for requirement in ("manifest", "school id", "sha-256", "record counts", "encryption", "rollback"):
            self.assertIn(requirement, requirements)

    def test_mossmig_is_classified_as_migration_package_not_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            package = Path(temporary) / "transfer.mossmig"
            package.write_bytes(ENVELOPE_MAGIC + b"opaque encrypted content")
            adapter = GatewayBackupAdapter()
            result = adapter.inspect_source(package)
            self.assertEqual(result.source_kind, "encrypted_migration_package")
            self.assertFalse(result.is_ordinary_backup)
            self.assertFalse(result.can_prepare_transfer)
            self.assertIn("not an ordinary", result.message.casefold())
            self.assertIn("server and data", result.next_action.casefold())
            with self.assertRaisesRegex(
                BackupAssistedTransferUnavailableError,
                "destination-bound encrypted migration package",
            ):
                adapter.require_supported_source(package)

    def test_extension_alone_cannot_masquerade_as_a_migration_or_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fake = Path(temporary) / "not-really-valid.mossmig"
            fake.write_text("ordinary text", encoding="utf-8")
            result = GatewayBackupAdapter().inspect_source(fake)
            self.assertEqual(result.source_kind, "unrecognized_file")
            self.assertFalse(result.is_ordinary_backup)

    def test_existing_recovery_artifacts_are_not_promoted_to_full_backups(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            last_good = root / "survey_results.mossjson.last-good.bak"
            last_good.write_text("{}", encoding="utf-8")
            rollback = root / "server-transfer" / TRANSACTION_ID
            _write_json(rollback / "migration-receipt.json", {"transaction_id": TRANSACTION_ID})
            (rollback / "data/csm_survey").mkdir(parents=True)
            legacy = root / "legacy-migration-copy"
            (legacy / "data/csm_survey").mkdir(parents=True)

            adapter = GatewayBackupAdapter()
            self.assertEqual(
                adapter.inspect_source(last_good).source_kind,
                "single_store_last_good_copy",
            )
            self.assertEqual(
                adapter.inspect_source(rollback).source_kind,
                "destination_rollback_evidence",
            )
            self.assertEqual(
                adapter.inspect_source(legacy).source_kind,
                "legacy_or_installer_migration_copy",
            )
            self.assertTrue(
                all(
                    not adapter.inspect_source(path).can_prepare_transfer
                    for path in (last_good, rollback, legacy)
                )
            )

    def test_unrecognized_archive_is_never_extracted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive_path = root / "claimed-backup.zip"
            escaped = root / "escaped.txt"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("../escaped.txt", "unsafe")
            result = GatewayBackupAdapter().inspect_source(archive_path)
            self.assertEqual(result.source_kind, "unrecognized_archive")
            self.assertFalse(result.can_prepare_transfer)
            self.assertFalse(escaped.exists())


class PortableRecoveryBackupTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            import cryptography  # noqa: F401
        except ImportError:
            self.skipTest("cryptography is installed by the release dependency set")

    def _create_backup(self, base: Path):
        source, destination = _make_roots(base)
        source_service = GatewayBackupService(base / "source-install", data_root=source)
        prepared = source_service.create_backup(
            base / "recovery-copy",
            school_id=SCHOOL_ID,
            source_installation_id=SOURCE_ID,
            application_version="0.5.0",
            source_data_root=source,
        )
        key_hex = prepared.take_one_time_key_hex()
        return source, destination, source_service, prepared, key_hex

    def test_creation_is_allowlisted_encrypted_and_key_is_available_once(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            source, _destination = _make_roots(base)
            (source / "gateway").mkdir(parents=True)
            (source / "gateway/device-private-key.txt").write_text("secret", encoding="utf-8")
            (source / "logs").mkdir(parents=True)
            (source / "logs/private.log").write_text("secret log", encoding="utf-8")
            service = GatewayBackupService(base / "source-install", data_root=source)
            prepared = service.create_backup(
                base / "recovery-copy",
                school_id=SCHOOL_ID,
                source_installation_id=SOURCE_ID,
                application_version="0.5.0",
                source_data_root=source,
            )
            self.assertEqual(prepared.package_path.suffix.casefold(), PORTABLE_BACKUP_EXTENSION)
            self.assertTrue(prepared.package_path.is_file())
            inspection = GatewayBackupAdapter().require_supported_source(prepared.package_path)
            self.assertTrue(inspection.is_ordinary_backup)
            self.assertEqual(inspection.source_kind, "portable_encrypted_backup")

            key_hex = prepared.take_one_time_key_hex()
            self.assertEqual(len(key_hex), 64)
            with self.assertRaisesRegex(RuntimeError, "already revealed"):
                prepared.take_one_time_key_hex()
            package_bytes = prepared.package_path.read_bytes()
            self.assertNotIn(bytes.fromhex(key_hex), package_bytes)
            self.assertNotIn(key_hex.encode("ascii"), package_bytes)
            self.assertNotIn(key_hex, repr(prepared))

            plain = base / "inspect.zip"
            AesGcmBackupCipher().decrypt_file(prepared.package_path, plain, bytes.fromhex(key_hex))
            with zipfile.ZipFile(plain, "r") as archive:
                manifest = json.loads(archive.read("manifest.json"))
                names = set(archive.namelist())
            logical_paths = {entry["logical_path"] for entry in manifest["files"]}
            self.assertFalse(any(path.startswith(("gateway/", "logs/")) for path in logical_paths))
            self.assertFalse(any(name.startswith(("payload/gateway/", "payload/logs/")) for name in names))
            settings_name = "payload/data/csm_survey/control_center_settings.json"
            with zipfile.ZipFile(plain, "r") as archive:
                portable_settings = json.loads(archive.read(settings_name))
            self.assertNotIn("network_password", portable_settings)
            self.assertNotIn("background_server_startup_enabled", portable_settings)
            self.assertEqual(list(service.transactions_root.iterdir()), [])

    def test_successful_restore_has_mode_bound_receipt_and_preserves_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            source, destination, _source_service, prepared, key_hex = self._create_backup(base)
            settings = destination / "data/csm_survey/control_center_settings.json"
            before = settings.read_bytes()
            old_preview = (
                destination / "data/csm_survey/scanner_previews/scan-before.png"
            )
            old_preview_bytes = old_preview.read_bytes()
            (destination / "gateway").mkdir(parents=True)
            gateway_marker = destination / "gateway/device.json"
            gateway_marker.write_text("destination identity", encoding="utf-8")
            destination_service = GatewayBackupService(
                base / "destination-install",
                data_root=destination,
            )
            receipt = destination_service.restore_backup(
                prepared.package_path,
                key=key_hex,
                expected_school_id=SCHOOL_ID,
                destination_installation_id=DESTINATION_ID,
                current_application_version="0.5.0",
                replace_existing=True,
            )
            validation = receipt.data_validation()
            self.assertEqual(
                set(validation),
                {
                    "verified",
                    "source_kind",
                    "backup_id",
                    "backup_manifest_sha256",
                    "transaction_id",
                    "manifest_sha256",
                    "active_tree_sha256",
                    "record_counts",
                },
            )
            self.assertTrue(validation["verified"])
            self.assertEqual(validation["source_kind"], "portable_backup")
            self.assertEqual(validation["backup_id"], prepared.backup_id)
            self.assertEqual(len(validation["backup_manifest_sha256"]), 64)
            active = json.loads(settings.read_text(encoding="utf-8"))
            self.assertEqual(active["school_id"], SCHOOL_ID)
            self.assertEqual(active["network_password"], "654321-device-only-secret")
            restored_preview = (
                destination / "data/csm_survey/scanner_previews/scan-source.png"
            )
            self.assertEqual(
                restored_preview.read_bytes(),
                b"\x89PNG\r\nsource-scanner-preview",
            )
            self.assertEqual(validation["record_counts"]["scanner_previews"], 1)
            self.assertFalse(old_preview.exists())
            self.assertTrue(gateway_marker.is_file())
            self.assertTrue(prepared.package_path.is_file(), "a recovery backup is reusable, not consumed")
            self.assertEqual(list(destination_service.transactions_root.iterdir()), [])
            self.assertEqual(list(destination.rglob("*.mossmig")), [])
            journal = json.loads(receipt.journal_path.read_text(encoding="utf-8"))
            self.assertEqual(journal["transfer_source"]["source_kind"], "portable_backup")
            self.assertEqual(journal["transfer_source"]["backup_id"], prepared.backup_id)

            rolled_back = destination_service.migration.rollback_import(
                receipt.migration_receipt()
            )
            self.assertEqual(rolled_back["state"], "rolled_back")
            self.assertEqual(settings.read_bytes(), before)
            self.assertEqual(old_preview.read_bytes(), old_preview_bytes)
            self.assertFalse(restored_preview.exists())

    def test_cross_school_newer_version_wrong_key_and_ciphertext_tamper_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            _source, destination, _source_service, prepared, key_hex = self._create_backup(base)
            settings = destination / "data/csm_survey/control_center_settings.json"
            before = settings.read_bytes()
            destination_service = GatewayBackupService(
                base / "destination-install",
                data_root=destination,
            )
            attempts = (
                ({"expected_school_id": "999999", "key": key_hex}, "different School ID"),
                (
                    {
                        "expected_school_id": SCHOOL_ID,
                        "current_application_version": "0.4.9",
                        "key": key_hex,
                    },
                    "incompatible or newer",
                ),
                (
                    {"expected_school_id": SCHOOL_ID, "key": "00" * 32},
                    "authentication check failed",
                ),
            )
            for overrides, message in attempts:
                values = {
                    "key": key_hex,
                    "expected_school_id": SCHOOL_ID,
                    "destination_installation_id": DESTINATION_ID,
                    "current_application_version": "0.5.0",
                    "replace_existing": True,
                }
                values.update(overrides)
                with self.subTest(message=message), self.assertRaisesRegex(
                    MigrationPackageValidationError, message
                ):
                    destination_service.restore_backup(prepared.package_path, **values)
                self.assertEqual(settings.read_bytes(), before)

            tampered = base / "tampered.mossbak"
            tampered.write_bytes(prepared.package_path.read_bytes())
            content = bytearray(tampered.read_bytes())
            content[-17] ^= 1
            tampered.write_bytes(content)
            with self.assertRaisesRegex(
                MigrationPackageValidationError,
                "authentication check failed",
            ):
                destination_service.restore_backup(
                    tampered,
                    key=key_hex,
                    expected_school_id=SCHOOL_ID,
                    destination_installation_id=DESTINATION_ID,
                    current_application_version="0.5.0",
                    replace_existing=True,
                )
            self.assertEqual(settings.read_bytes(), before)
            self.assertEqual(list(destination_service.transactions_root.iterdir()), [])

    def test_backup_manifest_hash_counts_and_unsafe_paths_are_rejected(self) -> None:
        mutations = {}

        def invalid_manifest(entries: dict[str, bytes]) -> None:
            manifest = json.loads(entries["manifest.json"])
            manifest["record_counts"]["survey_responses"] = 2
            entries["manifest.json"] = json.dumps(manifest).encode("utf-8")

        mutations["manifest integrity"] = invalid_manifest

        def invalid_hash(entries: dict[str, bytes]) -> None:
            name = "payload/data/csm_survey/control_center_settings.json"
            entries[name] += b" "

        mutations["integrity verification"] = invalid_hash

        def invalid_counts(entries: dict[str, bytes]) -> None:
            manifest = json.loads(entries["manifest.json"])
            manifest["record_counts"]["survey_responses"] = 2
            manifest["manifest_sha256"] = _manifest_digest(manifest)
            entries["manifest.json"] = json.dumps(manifest).encode("utf-8")

        mutations["record counts"] = invalid_counts

        def unsafe_path(entries: dict[str, bytes]) -> None:
            entries["../escaped.txt"] = b"unsafe"

        mutations["unsafe path"] = unsafe_path

        for expected_message, mutation in mutations.items():
            with self.subTest(expected_message=expected_message), tempfile.TemporaryDirectory() as temporary:
                base = Path(temporary)
                _source, destination, _source_service, prepared, key_hex = self._create_backup(base)
                package = prepared.package_path
                before = (
                    destination / "data/csm_survey/control_center_settings.json"
                ).read_bytes()
                _rewrite_backup_archive(
                    package,
                    key=bytes.fromhex(key_hex),
                    mutate=mutation,
                )
                destination_service = GatewayBackupService(
                    base / "destination-install",
                    data_root=destination,
                )
                with self.assertRaisesRegex(MigrationPackageValidationError, expected_message):
                    destination_service.restore_backup(
                        package,
                        key=key_hex,
                        expected_school_id=SCHOOL_ID,
                        destination_installation_id=DESTINATION_ID,
                        current_application_version="0.5.0",
                        replace_existing=True,
                    )
                self.assertEqual(
                    (destination / "data/csm_survey/control_center_settings.json").read_bytes(),
                    before,
                )
                self.assertFalse((base / "escaped.txt").exists())

    def test_creation_rejects_redirected_portable_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            source, _destination = _make_roots(base)
            redirected = source / "data/csm_survey/dashboard_snapshots/redirect.json"
            redirected.parent.mkdir(parents=True)
            redirected.write_text("redirect target bytes", encoding="utf-8")
            service = GatewayBackupService(base / "source-install", data_root=source)
            from school_csm_control_center.services import gateway_migration

            original = gateway_migration._is_reparse
            with patch.object(
                gateway_migration,
                "_is_reparse",
                side_effect=lambda path: Path(path) == redirected or original(Path(path)),
            ):
                with self.assertRaisesRegex(MigrationPackageValidationError, "redirected"):
                    service.create_backup(
                        base / "unsafe-backup.mossbak",
                        school_id=SCHOOL_ID,
                        source_installation_id=SOURCE_ID,
                        application_version="0.5.0",
                        source_data_root=source,
                    )
            self.assertFalse((base / "unsafe-backup.mossbak").exists())

    def test_creation_rejects_a_redirected_nested_portable_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            source, destination = _make_roots(base)
            redirected = source / "data/csm_survey/scanner_previews/redirected-directory"
            redirected.mkdir(parents=True)
            service = GatewayBackupService(base / "source-install", data_root=source)
            migration = GatewayMigrationService(base / "migration-install", data_root=destination)
            from school_csm_control_center.services import gateway_migration

            original = gateway_migration._is_reparse
            with patch.object(
                gateway_migration,
                "_is_reparse",
                side_effect=lambda path: Path(path) == redirected or original(Path(path)),
            ):
                with self.assertRaisesRegex(MigrationPackageValidationError, "redirected object"):
                    service.create_backup(
                        base / "unsafe-nested-directory.mossbak",
                        school_id=SCHOOL_ID,
                        source_installation_id=SOURCE_ID,
                        application_version="0.5.0",
                        source_data_root=source,
                    )
                with self.assertRaisesRegex(MigrationPackageValidationError, "redirected object"):
                    migration.create_package(
                        base / "unsafe-nested-directory.mossmig",
                        key=secrets.token_bytes(32),
                        school_id=SCHOOL_ID,
                        source_installation_id=SOURCE_ID,
                        destination_installation_id=DESTINATION_ID,
                        application_version="0.5.0",
                        source_data_root=source,
                    )
            self.assertFalse((base / "unsafe-nested-directory.mossbak").exists())
            self.assertFalse((base / "unsafe-nested-directory.mossmig").exists())

    def test_creation_rejects_a_reparse_ancestor_in_destination_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            source, _destination = _make_roots(base)
            redirected_ancestor = base / "redirected-export-location"
            redirected_ancestor.mkdir()
            selected = redirected_ancestor / "recovery.mossbak"
            service = GatewayBackupService(base / "source-install", data_root=source)
            from school_csm_control_center.services import gateway_backup_adapter

            original = gateway_backup_adapter._path_component_is_redirected
            with patch.object(
                gateway_backup_adapter,
                "_path_component_is_redirected",
                side_effect=lambda path: Path(path) == redirected_ancestor
                or original(Path(path)),
            ):
                with self.assertRaisesRegex(ValueError, "redirected or unsafe"):
                    service.create_backup(
                        selected,
                        school_id=SCHOOL_ID,
                        source_installation_id=SOURCE_ID,
                        application_version="0.5.0",
                        source_data_root=source,
                    )
            self.assertFalse(selected.exists())

    def test_restore_rejects_a_reparse_ancestor_before_reading_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            _source, destination, _source_service, prepared, key_hex = self._create_backup(base)
            redirected_ancestor = base / "redirected-import-location"
            redirected_ancestor.mkdir()
            selected = redirected_ancestor / prepared.package_path.name
            selected.write_bytes(prepared.package_path.read_bytes())
            before = (
                destination / "data/csm_survey/control_center_settings.json"
            ).read_bytes()
            service = GatewayBackupService(base / "destination-install", data_root=destination)
            from school_csm_control_center.services import gateway_backup_adapter

            original = gateway_backup_adapter._path_component_is_redirected
            with patch.object(
                gateway_backup_adapter,
                "_path_component_is_redirected",
                side_effect=lambda path: Path(path) == redirected_ancestor
                or original(Path(path)),
            ):
                with self.assertRaisesRegex(
                    MigrationPackageValidationError,
                    "redirected",
                ):
                    service.restore_backup(
                        selected,
                        key=key_hex,
                        expected_school_id=SCHOOL_ID,
                        destination_installation_id=DESTINATION_ID,
                        current_application_version="0.5.0",
                        replace_existing=True,
                    )
            self.assertEqual(
                (destination / "data/csm_survey/control_center_settings.json").read_bytes(),
                before,
            )
            self.assertEqual(list(service.transactions_root.iterdir()), [])


class GatewayMigrationCompletenessTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            import cryptography  # noqa: F401
        except ImportError:
            self.skipTest("cryptography is installed by the release dependency set")

    def test_package_is_bound_to_exact_destination_and_failure_changes_no_data(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            key = secrets.token_bytes(32)
            service, _source, destination, package, _manifest = _make_package(base, key=key)
            settings = destination / "data/csm_survey/control_center_settings.json"
            before = settings.read_bytes()
            with self.assertRaisesRegex(
                MigrationPackageValidationError,
                "another installation|intended for",
            ):
                service.stage_import(
                    package,
                    key=key,
                    expected_school_id=SCHOOL_ID,
                    destination_installation_id=OTHER_DESTINATION_ID,
                    current_application_version="0.5.0",
                    replace_existing=True,
                )
            self.assertEqual(settings.read_bytes(), before)
            self.assertTrue(package.exists())
            for transaction in service.transactions_root.glob("stage-*"):
                self.assertFalse((transaction / "decrypted.zip").exists())
                self.assertFalse((transaction / "extracted").exists())

    def test_direct_scanner_preview_requires_explicit_replacement_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            key = secrets.token_bytes(32)
            service, _source, destination, package, _manifest = _make_package(base, key=key)
            existing_preview = (
                destination / "data/csm_survey/scanner_previews/scan-before.png"
            )
            before = existing_preview.read_bytes()
            with self.assertRaisesRegex(
                MigrationPackageValidationError,
                "Explicit replacement confirmation",
            ):
                service.stage_import(
                    package,
                    key=key,
                    expected_school_id=SCHOOL_ID,
                    destination_installation_id=DESTINATION_ID,
                    current_application_version="0.5.0",
                    replace_existing=False,
                )
            self.assertEqual(existing_preview.read_bytes(), before)
            self.assertTrue(package.exists())

    def test_direct_mrs_audit_requires_explicit_replacement_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            key = secrets.token_bytes(32)
            service, _source, destination, package, _manifest = _make_package(base, key=key)
            previews = destination / "data/csm_survey/scanner_previews"
            for preview in previews.glob("*"):
                preview.unlink()
            _write_json(
                destination / "data/csm_survey/mrs_registry.mossjson",
                {
                    "file_type": "School CSM MRS Registry",
                    "schema_version": "1.0",
                    "forms": [],
                    "print_attempts": [{"control_number": "MRS-EXISTING-AUDIT"}],
                    "print_batches": [],
                    "scan_attempts": [],
                },
            )
            with self.assertRaisesRegex(
                MigrationPackageValidationError,
                "Explicit replacement confirmation",
            ):
                service.stage_import(
                    package,
                    key=key,
                    expected_school_id=SCHOOL_ID,
                    destination_installation_id=DESTINATION_ID,
                    current_application_version="0.5.0",
                    replace_existing=False,
                )
            self.assertTrue(package.exists())

    def test_migration_creation_rejects_a_reparse_destination_ancestor(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            source, destination = _make_roots(base)
            service = GatewayMigrationService(base / "install", data_root=destination)
            redirected_ancestor = base / "redirected-migration-export"
            redirected_ancestor.mkdir()
            selected = redirected_ancestor / "transfer.mossmig"
            from school_csm_control_center.services import gateway_migration

            original = gateway_migration._is_reparse
            with patch.object(
                gateway_migration,
                "_is_reparse",
                side_effect=lambda path: Path(path) == redirected_ancestor
                or original(Path(path)),
            ):
                with self.assertRaisesRegex(
                    MigrationPackageValidationError,
                    "destination is redirected or unsafe",
                ):
                    service.create_package(
                        selected,
                        key=secrets.token_bytes(32),
                        school_id=SCHOOL_ID,
                        source_installation_id=SOURCE_ID,
                        destination_installation_id=DESTINATION_ID,
                        application_version="0.5.0",
                        source_data_root=source,
                    )
            self.assertFalse(selected.exists())

    def test_migration_import_rejects_a_reparse_package_ancestor(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            key = secrets.token_bytes(32)
            service, _source, destination, package, _manifest = _make_package(base, key=key)
            redirected_ancestor = base / "redirected-migration-import"
            redirected_ancestor.mkdir()
            selected = redirected_ancestor / package.name
            selected.write_bytes(package.read_bytes())
            existing_preview = (
                destination / "data/csm_survey/scanner_previews/scan-before.png"
            )
            before = existing_preview.read_bytes()
            from school_csm_control_center.services import gateway_migration

            original = gateway_migration._is_reparse
            with patch.object(
                gateway_migration,
                "_is_reparse",
                side_effect=lambda path: Path(path) == redirected_ancestor
                or original(Path(path)),
            ):
                with self.assertRaisesRegex(
                    MigrationPackageValidationError,
                    "unavailable or redirected",
                ):
                    service.stage_import(
                        selected,
                        key=key,
                        expected_school_id=SCHOOL_ID,
                        destination_installation_id=DESTINATION_ID,
                        current_application_version="0.5.0",
                        replace_existing=True,
                    )
            self.assertEqual(existing_preview.read_bytes(), before)
            self.assertEqual(list(service.transactions_root.glob("stage-*")), [])

    def test_creation_refuses_a_source_that_changes_during_capture(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            source, destination = _make_roots(base)
            service = GatewayMigrationService(base / "install", data_root=destination)
            package = base / "inconsistent.mossmig"
            from school_csm_control_center.services import gateway_migration

            original = gateway_migration._record_counts
            calls = 0

            def inconsistent_counts(data_root, *, settings):
                nonlocal calls
                result = original(data_root, settings=settings)
                if calls == 0:
                    result["survey_responses"] += 1
                calls += 1
                return result

            with patch.object(gateway_migration, "_record_counts", side_effect=inconsistent_counts):
                with self.assertRaisesRegex(
                    MigrationPackageValidationError,
                    "record counts",
                ):
                    service.create_package(
                        package,
                        key=secrets.token_bytes(32),
                        school_id=SCHOOL_ID,
                        source_installation_id=SOURCE_ID,
                        destination_installation_id=DESTINATION_ID,
                        application_version="0.5.0",
                        source_data_root=source,
                    )
            self.assertGreaterEqual(calls, 2)
            self.assertFalse(package.exists())

    def test_commit_rejects_a_redirected_staging_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            key = secrets.token_bytes(32)
            service, _source, destination, package, _manifest = _make_package(base, key=key)
            before = (
                destination / "data/csm_survey/control_center_settings.json"
            ).read_bytes()
            staged = service.stage_import(
                package,
                key=key,
                expected_school_id=SCHOOL_ID,
                destination_installation_id=DESTINATION_ID,
                current_application_version="0.5.0",
                replace_existing=True,
            )
            redirected = (
                staged.staged_root / "payload/data/csm_survey/scanner_previews"
            )
            from school_csm_control_center.services import gateway_migration

            original = gateway_migration._is_reparse
            with patch.object(
                gateway_migration,
                "_is_reparse",
                side_effect=lambda path: Path(path) == redirected or original(Path(path)),
            ):
                with self.assertRaisesRegex(MigrationCommitError, "changed after validation"):
                    service.commit_import(staged)
            self.assertEqual(
                (destination / "data/csm_survey/control_center_settings.json").read_bytes(),
                before,
            )

    def test_one_time_key_is_256_bit_unique_and_never_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            key = secrets.token_bytes(32)
            second_key = secrets.token_bytes(32)
            self.assertEqual(len(key), 32)
            self.assertEqual(len(second_key), 32)
            self.assertNotEqual(key, second_key)
            service, source, destination, package, _manifest = _make_package(base, key=key)
            self.assertNotIn(key, package.read_bytes())
            self.assertNotIn(key.hex().encode("ascii"), package.read_bytes())

            staged = service.stage_import(
                package,
                key=key,
                expected_school_id=SCHOOL_ID,
                destination_installation_id=DESTINATION_ID,
                current_application_version="0.5.0",
                replace_existing=True,
            )
            for root in (source, destination, service.paths.migration_dir):
                for path in root.rglob("*"):
                    if path.is_file():
                        content = path.read_bytes()
                        self.assertNotIn(key, content, str(path))
                        self.assertNotIn(key.hex().encode("ascii"), content, str(path))
            service.discard_staged_import(staged)

    def test_manifest_file_hash_and_record_count_tampering_are_rejected(self) -> None:
        mutations = {}

        def invalid_manifest_digest(entries: dict[str, bytes]) -> None:
            manifest = json.loads(entries["manifest.json"])
            manifest["record_counts"]["survey_responses"] = 99
            entries["manifest.json"] = json.dumps(manifest).encode("utf-8")

        mutations["manifest integrity"] = invalid_manifest_digest

        def invalid_file_hash(entries: dict[str, bytes]) -> None:
            target = "payload/data/csm_survey/control_center_settings.json"
            entries[target] = entries[target] + b" "

        mutations["integrity verification"] = invalid_file_hash

        def invalid_record_count(entries: dict[str, bytes]) -> None:
            manifest = json.loads(entries["manifest.json"])
            manifest["record_counts"]["survey_responses"] = 99
            manifest["manifest_sha256"] = _manifest_digest(manifest)
            entries["manifest.json"] = json.dumps(manifest).encode("utf-8")

        mutations["record counts"] = invalid_record_count

        for expected_message, mutation in mutations.items():
            with self.subTest(expected_message=expected_message), tempfile.TemporaryDirectory() as temporary:
                base = Path(temporary)
                key = secrets.token_bytes(32)
                service, _source, destination, package, _manifest = _make_package(base, key=key)
                before = (
                    destination / "data/csm_survey/control_center_settings.json"
                ).read_bytes()
                _rewrite_encrypted_archive(package, key=key, mutate=mutation)
                with self.assertRaisesRegex(MigrationPackageValidationError, expected_message):
                    service.stage_import(
                        package,
                        key=key,
                        expected_school_id=SCHOOL_ID,
                        destination_installation_id=DESTINATION_ID,
                        current_application_version="0.5.0",
                        replace_existing=True,
                    )
                self.assertEqual(
                    (destination / "data/csm_survey/control_center_settings.json").read_bytes(),
                    before,
                )

    def test_success_consumes_package_and_verified_rollback_restores_previous_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            key = secrets.token_bytes(32)
            service, _source, destination, package, manifest = _make_package(base, key=key)
            settings = destination / "data/csm_survey/control_center_settings.json"
            before = settings.read_bytes()
            old_preview = (
                destination / "data/csm_survey/scanner_previews/scan-before.png"
            )
            old_preview_bytes = old_preview.read_bytes()
            staged = service.stage_import(
                package,
                key=key,
                expected_school_id=SCHOOL_ID,
                destination_installation_id=DESTINATION_ID,
                current_application_version="0.5.0",
                replace_existing=True,
            )
            receipt = service.commit_import(staged)
            self.assertFalse(package.exists())
            self.assertEqual(receipt.transaction_id, manifest["transaction_id"])
            self.assertEqual(json.loads(settings.read_text(encoding="utf-8"))["school_id"], SCHOOL_ID)
            imported_preview = (
                destination / "data/csm_survey/scanner_previews/scan-source.png"
            )
            self.assertEqual(
                imported_preview.read_bytes(),
                b"\x89PNG\r\nsource-scanner-preview",
            )
            self.assertEqual(manifest["record_counts"]["scanner_previews"], 1)
            self.assertFalse(old_preview.exists())
            rollback_receipt = receipt.backup_root.parent.parent / "migration-receipt.json"
            self.assertTrue(rollback_receipt.is_file())
            self.assertEqual(
                json.loads(rollback_receipt.read_text(encoding="utf-8"))["active_tree_sha256"],
                receipt.active_tree_sha256,
            )

            journal = service.rollback_import(receipt)
            self.assertEqual(journal["state"], "rolled_back")
            self.assertEqual(settings.read_bytes(), before)
            self.assertEqual(old_preview.read_bytes(), old_preview_bytes)
            self.assertFalse(imported_preview.exists())

    def test_tampered_rollback_copy_is_refused_and_imported_data_remains_active(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            key = secrets.token_bytes(32)
            service, _source, destination, package, _manifest = _make_package(base, key=key)
            staged = service.stage_import(
                package,
                key=key,
                expected_school_id=SCHOOL_ID,
                destination_installation_id=DESTINATION_ID,
                current_application_version="0.5.0",
                replace_existing=True,
            )
            receipt = service.commit_import(staged)
            active_settings = destination / "data/csm_survey/control_center_settings.json"
            imported = active_settings.read_bytes()
            (receipt.backup_root / f"tampered-{uuid4()}.txt").write_text(
                "changed rollback evidence",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(MigrationCommitError, "[Rr]ollback failed"):
                service.rollback_import(receipt)
            self.assertEqual(active_settings.read_bytes(), imported)
            self.assertEqual(json.loads(active_settings.read_text(encoding="utf-8"))["school_id"], SCHOOL_ID)


if __name__ == "__main__":
    unittest.main()
