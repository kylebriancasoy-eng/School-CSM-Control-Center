from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from school_csm_control_center.internet_gateway.config import parse_gateway_provider_config
from school_csm_control_center.services.gateway_migration import (
    AesGcmMigrationCipher,
    GatewayMigrationService,
    MigrationCommitError,
    MigrationPackageValidationError,
    PORTABLE_SETTINGS_KEYS,
)
from school_csm_control_center.storage.gateway_credentials import GatewayCredentialStore
from school_csm_control_center.storage.gateway_state import (
    GatewayRetirementError,
    GatewayStateStore,
)


SCHOOL_ID = "123456"
SOURCE_ID = "11111111-1111-4111-8111-111111111111"
DESTINATION_ID = "22222222-2222-4222-8222-222222222222"
TRANSACTION_ID = "33333333-3333-4333-8333-333333333333"


class _MemoryCredentialManager:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def write(self, target: str, value: str) -> None:
        self.values[target] = value

    def read(self, target: str) -> str | None:
        return self.values.get(target)

    def delete(self, target: str) -> bool:
        return self.values.pop(target, None) is not None


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def _source_settings() -> dict:
    return {
        "school_name": "Calapi Elementary School",
        "school_id": SCHOOL_ID,
        "school_identifier": "calapi-es",
        "survey_status": "online",
        "preferred_port": 9123,
        "server_ip": "192.168.50.10",
        "network_password": "must-not-transfer",
        "background_server_startup_enabled": True,
        "unknown_private_setting": "must-not-transfer",
    }


def _destination_settings() -> dict:
    return {
        "school_name": "Old Destination School",
        "school_id": "654321",
        "school_identifier": "old-school",
        "preferred_port": 8444,
        "server_ip": "10.1.2.3",
        "network_password": "destination-device-value",
        "background_server_startup_enabled": True,
    }


class GatewayStateEnvelopeTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        except ImportError:
            self.skipTest("cryptography is installed by the release dependency set")
        self.private_key = Ed25519PrivateKey.generate()
        public_key = self.private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        self.config = parse_gateway_provider_config(
            {
                "schema_version": "1.0",
                "provider_name": "Division Gateway",
                "registration_service_url": "https://register.csm.example.gov.ph",
                "managed_domain": "csm.example.gov.ph",
                "authorization_signing_keys": [
                    {
                        "key_id": "provider-2026",
                        "public_key_base64": base64.b64encode(public_key).decode("ascii"),
                    }
                ],
                "trusted_proxy_addresses": ["127.0.0.1"],
            }
        )

    def test_missing_retirement_marker_ignores_corrupt_convenience_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gateway = root / "gateway"
            gateway.mkdir(parents=True)
            state_path = gateway / "gateway_state.json"
            state_path.write_text("{not-json", encoding="utf-8")
            store = GatewayStateStore(root, data_root=root, provider_config=self.config)

            self.assertIsNone(store.retirement_record())
            self.assertEqual(state_path.read_text(encoding="utf-8"), "{not-json")
            self.assertEqual(list(gateway.glob("*.corrupt-*")), [])

    def _envelope(self, state: str, *, installation_id: str = SOURCE_ID) -> dict:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        payload = {
            "school_id": SCHOOL_ID,
            "installation_id": installation_id,
            "registration_id": "registration-1",
            "authorization_state": state,
            "public_host": f"{SCHOOL_ID}.csm.example.gov.ph",
            "tunnel_id": "tunnel-2",
            "transaction_id": TRANSACTION_ID,
            "issued_at": (now - timedelta(minutes=1)).isoformat(),
            "expires_at": (now + timedelta(hours=1)).isoformat(),
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return {
            "schema_version": "1.0",
            "algorithm": "Ed25519",
            "key_id": "provider-2026",
            "payload": payload,
            "signature": base64.b64encode(self.private_key.sign(canonical)).decode("ascii"),
        }

    def _store(self, root: Path) -> GatewayStateStore:
        store = GatewayStateStore(root, data_root=root, provider_config=self.config)
        store.save_registration(
            school_id=SCHOOL_ID,
            registration_id="registration-1",
            public_hostname=f"{SCHOOL_ID}.csm.example.gov.ph",
            installation_id=SOURCE_ID,
        )
        return store

    def test_signed_transferred_envelope_is_persisted_and_reverified(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = self._store(Path(temporary))
            record = store.retire(self._envelope("transferred"))
            self.assertEqual(record["retirement_status"], "TRANSFERRED")
            marker = json.loads(store.retirement_path.read_text(encoding="utf-8"))
            self.assertEqual(marker["authorization_envelope"]["payload"]["school_id"], SCHOOL_ID)
            self.assertEqual(len(marker["provider_binding"]["signing_public_key_sha256"]), 64)
            self.assertTrue(store.is_retired())

            marker["record"]["reason"] = "tampered"
            store.retirement_path.write_text(json.dumps(marker), encoding="utf-8")
            with self.assertRaises(GatewayRetirementError):
                store.retirement_record()

    def test_retirement_envelope_is_bound_to_the_local_uuid(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = self._store(Path(temporary))
            with self.assertRaises((GatewayRetirementError, ValueError)):
                store.retire(self._envelope("transferred", installation_id=DESTINATION_ID))
            self.assertFalse(store.retirement_path.exists())

    def test_active_envelope_only_clears_matching_retirement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = self._store(Path(temporary))
            store.retire(self._envelope("revoked"))
            state = store.clear_for_authorized_transfer(self._envelope("active"))
            self.assertEqual(state["authorization_status"], "ACTIVE")
            self.assertFalse(store.retirement_path.exists())

    def test_legacy_signed_record_api_remains_compatible(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = GatewayStateStore(
                root,
                data_root=root,
                signature_verifier=lambda record: record.get("authority_signature") == "legacy-signature",
            )
            store.save_registration(
                school_id=SCHOOL_ID,
                registration_id="registration-1",
                public_hostname=f"{SCHOOL_ID}.csm.example.gov.ph",
                installation_id=SOURCE_ID,
            )
            record = {
                "schema_version": "1.0",
                "school_id": SCHOOL_ID,
                "retired_installation_id": SOURCE_ID,
                "transfer_transaction_id": TRANSACTION_ID,
                "confirmation_timestamp": datetime.now(timezone.utc).isoformat(),
                "retirement_status": "TRANSFERRED",
                "registration_service_key_id": "legacy-key",
                "signature_algorithm": "Ed25519",
                "authority_signature": "legacy-signature",
                "reason": "Legacy transfer",
            }
            store.retire(record)
            self.assertEqual(store.retirement_record()["reason"], "Legacy transfer")

    def test_legacy_marker_without_a_signature_verifier_never_authenticates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = self._store(root)
            record = {
                "schema_version": "1.0",
                "school_id": SCHOOL_ID,
                "retired_installation_id": SOURCE_ID,
                "transfer_transaction_id": TRANSACTION_ID,
                "confirmation_timestamp": datetime.now(timezone.utc).isoformat(),
                "retirement_status": "TRANSFERRED",
                "registration_service_key_id": "untrusted-legacy-key",
                "signature_algorithm": "Ed25519",
                "authority_signature": "not-verified",
                "reason": "Untrusted legacy marker",
            }
            store.retirement_path.parent.mkdir(parents=True, exist_ok=True)
            store.retirement_path.write_text(
                json.dumps(
                    {
                        "file_type": store.RETIREMENT_FILE_TYPE,
                        "schema_version": "1.0",
                        "persisted_at": datetime.now(timezone.utc).isoformat(),
                        "record": record,
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(GatewayRetirementError):
                store.retirement_record()


class GatewayCredentialTests(unittest.TestCase):
    def test_device_secrets_have_independent_lifecycle(self) -> None:
        manager = _MemoryCredentialManager()
        store = GatewayCredentialStore(manager)
        store.save_tunnel_credential("tunnel-secret")
        store.save_installation_secret("installation-secret")
        store.save_device_private_key("device-private-key")
        self.assertEqual(
            store.exists(),
            {
                "tunnel_credential": True,
                "installation_secret": True,
                "device_private_key": True,
            },
        )
        self.assertEqual(store.delete_all(), {key: True for key in store.exists()})
        self.assertFalse(any(store.exists().values()))


class GatewayMigrationIntegrityTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            import cryptography  # noqa: F401
        except ImportError:
            self.skipTest("cryptography is installed by the release dependency set")
        self.key = b"K" * 32

    def _roots(self, base: Path) -> tuple[Path, Path]:
        source = base / "source"
        destination = base / "destination"
        _write_json(source / "data/csm_survey/control_center_settings.json", _source_settings())
        _write_json(
            source / "data/csm_survey/survey_results.mossjson",
            {"file_type": "School CSM Control Center Results", "schema_version": "2.0", "records": []},
        )
        (source / "gateway").mkdir(parents=True)
        (source / "gateway/retirement.mossjson").write_text("secret-marker", encoding="utf-8")
        (source / "logs").mkdir(parents=True)
        (source / "logs/control-center.log").write_text("private log", encoding="utf-8")
        _write_json(destination / "data/csm_survey/control_center_settings.json", _destination_settings())
        (destination / "gateway").mkdir(parents=True)
        (destination / "gateway/device.json").write_text("keep gateway state", encoding="utf-8")
        return source, destination

    def _package_and_stage(self, base: Path, *, replace_existing: bool = False):
        source, destination = self._roots(base)
        service = GatewayMigrationService(base / "install", data_root=destination)
        package = base / "transfer.mossmig"
        manifest = service.create_package(
            package,
            key=self.key,
            school_id=SCHOOL_ID,
            source_installation_id=SOURCE_ID,
            destination_installation_id=DESTINATION_ID,
            application_version="0.5.0",
            transaction_id=TRANSACTION_ID,
            source_data_root=source,
        )
        staged = service.stage_import(
            package,
            key=self.key,
            expected_school_id=SCHOOL_ID,
            destination_installation_id=DESTINATION_ID,
            current_application_version="0.5.0",
            replace_existing=replace_existing,
        )
        return source, destination, service, package, manifest, staged

    def test_package_filters_settings_and_never_includes_machine_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source, destination, service, package, manifest, staged = self._package_and_stage(
                Path(temporary), replace_existing=True
            )
            settings = json.loads(
                (staged.staged_root / "payload/data/csm_survey/control_center_settings.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertLessEqual(set(settings), set(PORTABLE_SETTINGS_KEYS))
            self.assertNotIn("preferred_port", settings)
            self.assertNotIn("network_password", settings)
            self.assertNotIn("background_server_startup_enabled", settings)
            paths = {entry["logical_path"] for entry in manifest["files"]}
            self.assertFalse(any(path.startswith(("gateway/", "logs/")) for path in paths))

            receipt = service.commit_import(staged)
            active = json.loads(
                (destination / "data/csm_survey/control_center_settings.json").read_text(encoding="utf-8")
            )
            self.assertEqual(active["school_id"], SCHOOL_ID)
            self.assertEqual(active["preferred_port"], 8444)
            self.assertEqual(active["network_password"], "destination-device-value")
            self.assertTrue((destination / "gateway/device.json").is_file())
            self.assertTrue(receipt.backup_root.is_dir())
            self.assertTrue(
                (receipt.backup_root.parent.parent / "migration-receipt.json").is_file()
            )
            self.assertFalse(package.exists())

    def test_aes_gcm_rejects_tampering_without_plaintext_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plaintext = root / "plain.zip"
            package = root / "encrypted.mossmig"
            decrypted = root / "decrypted.zip"
            plaintext.write_bytes(b"school records" * 100)
            cipher = AesGcmMigrationCipher()
            cipher.encrypt_file(
                plaintext,
                package,
                self.key,
                context={
                    "transaction_id": TRANSACTION_ID,
                    "destination_installation_id": DESTINATION_ID,
                },
            )
            altered = bytearray(package.read_bytes())
            altered[-17] ^= 1
            package.write_bytes(altered)
            with self.assertRaises(MigrationPackageValidationError):
                cipher.decrypt_file(package, decrypted, self.key)
            self.assertFalse(decrypted.exists())

    def test_staged_payload_tamper_is_detected_before_commit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source, destination, service, package, manifest, staged = self._package_and_stage(
                Path(temporary), replace_existing=True
            )
            settings = staged.staged_root / "payload/data/csm_survey/control_center_settings.json"
            settings.write_bytes(settings.read_bytes() + b" ")
            with self.assertRaises(MigrationCommitError):
                service.commit_import(staged)
            current = json.loads(
                (destination / "data/csm_survey/control_center_settings.json").read_text(encoding="utf-8")
            )
            self.assertEqual(current["school_id"], "654321")

    def test_commit_failure_after_backup_move_restores_previous_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source, destination, service, package, manifest, staged = self._package_and_stage(
                Path(temporary), replace_existing=True
            )
            settings_path = destination / "data/csm_survey/control_center_settings.json"
            before = settings_path.read_bytes()
            with patch(
                "school_csm_control_center.services.gateway_migration._write_backup_receipt",
                side_effect=OSError("simulated receipt failure"),
            ):
                with self.assertRaises(MigrationCommitError):
                    service.commit_import(staged)
            self.assertEqual(settings_path.read_bytes(), before)

    def test_missing_source_store_replaces_destination_store_with_empty(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            source, destination = self._roots(base)
            _write_json(
                destination / "data/csm_survey/narrative_reports.mossjson",
                {
                    "file_type": "School CSM Narrative Reports",
                    "schema_version": "1.0",
                    "records": [{"id": "old-other-school-record"}],
                },
            )
            service = GatewayMigrationService(base / "install", data_root=destination)
            package = base / "transfer.mossmig"
            service.create_package(
                package,
                key=self.key,
                school_id=SCHOOL_ID,
                source_installation_id=SOURCE_ID,
                destination_installation_id=DESTINATION_ID,
                application_version="0.5.0",
                transaction_id=TRANSACTION_ID,
                source_data_root=source,
            )
            staged = service.stage_import(
                package,
                key=self.key,
                expected_school_id=SCHOOL_ID,
                destination_installation_id=DESTINATION_ID,
                current_application_version="0.5.0",
                replace_existing=True,
            )
            service.commit_import(staged)
            self.assertFalse(
                (destination / "data/csm_survey/narrative_reports.mossjson").exists()
            )

    def test_commit_receipt_supports_verified_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            source, destination, service, package, manifest, staged = self._package_and_stage(
                base, replace_existing=True
            )
            settings_path = destination / "data/csm_survey/control_center_settings.json"
            before = settings_path.read_bytes()
            receipt = service.commit_import(staged)
            self.assertEqual(
                json.loads(settings_path.read_text(encoding="utf-8"))["school_id"],
                SCHOOL_ID,
            )
            journal = service.rollback_import(receipt)
            self.assertEqual(journal["state"], "rolled_back")
            self.assertEqual(settings_path.read_bytes(), before)
            self.assertTrue((destination / "gateway/device.json").is_file())

    def test_rollback_refuses_to_discard_post_import_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            source, destination, service, package, manifest, staged = self._package_and_stage(
                base, replace_existing=True
            )
            receipt = service.commit_import(staged)
            marker = destination / "data/csm_survey/post-import-record.txt"
            marker.write_text("new data", encoding="utf-8")
            with self.assertRaises(MigrationCommitError):
                service.rollback_import(receipt)
            self.assertEqual(marker.read_text(encoding="utf-8"), "new data")

    def test_wrong_key_leaves_no_decrypted_failed_stage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            source, destination = self._roots(base)
            service = GatewayMigrationService(base / "install", data_root=destination)
            package = base / "transfer.mossmig"
            service.create_package(
                package,
                key=self.key,
                school_id=SCHOOL_ID,
                source_installation_id=SOURCE_ID,
                destination_installation_id=DESTINATION_ID,
                application_version="0.5.0",
                transaction_id=TRANSACTION_ID,
                source_data_root=source,
            )
            with self.assertRaises(MigrationPackageValidationError):
                service.stage_import(
                    package,
                    key=b"W" * 32,
                    expected_school_id=SCHOOL_ID,
                    destination_installation_id=DESTINATION_ID,
                    current_application_version="0.5.0",
                    replace_existing=True,
                )
            failed = list(service.transactions_root.glob("stage-*"))
            self.assertEqual(len(failed), 1)
            self.assertFalse((failed[0] / "extracted").exists())
            self.assertFalse((failed[0] / "decrypted.zip").exists())


if __name__ == "__main__":
    unittest.main()
