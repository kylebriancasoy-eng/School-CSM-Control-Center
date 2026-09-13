from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from school_csm_control_center.services.direct_worker_hostname_migration import (
    DirectWorkerHostnameMigration,
    DirectWorkerHostnameMigrationError,
)
from school_csm_control_center.storage.gateway_state import GatewayStateStore


SCHOOL_ID = "123627"
INSTALLATION_ID = "11111111-1111-4111-8111-111111111111"
TUNNEL_ID = "22222222-2222-4222-8222-222222222222"
OLD_HOST = f"{SCHOOL_ID}.calapi-es-csm-survey.workers.dev"
NEW_HOST = f"{SCHOOL_ID}.motiong-district-csm-survey.workers.dev"


def _configured_store(root: Path) -> GatewayStateStore:
    store = GatewayStateStore(root, data_root=root)
    store.save_registration(
        school_id=SCHOOL_ID,
        registration_id=f"direct-worker-vpc-{TUNNEL_ID}",
        public_hostname=OLD_HOST,
        tunnel_id=TUNNEL_ID,
        provisioning_state="direct_worker_vpc",
        gateway_status="disconnected",
        authorization_status="ACTIVE",
        installation_id=INSTALLATION_ID,
    )
    return store


class DirectWorkerHostnameMigrationTests(unittest.TestCase):
    def test_changes_only_hostname_and_creates_verified_timestamped_backups(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = _configured_store(root)
            response_path = root / "data" / "csm_survey" / "survey_results.mossjson"
            response_path.parent.mkdir(parents=True, exist_ok=True)
            response_bytes = b'{"records":[{"response_id":"keep-me"}]}\n'
            response_path.write_bytes(response_bytes)
            credential_decoy = root / "gateway" / "credential-decoy.bin"
            credential_bytes = b"credential-must-remain-untouched"
            credential_decoy.write_bytes(credential_bytes)
            original_state = store.path.read_bytes()
            original_audit = store.audit_path.read_bytes()
            before = store.load()

            result = DirectWorkerHostnameMigration(
                root,
                data_root=root,
                state_store=store,
            ).migrate(
                expected_school_id=SCHOOL_ID,
                expected_old_hostname=OLD_HOST,
                new_hostname=NEW_HOST,
            )

            self.assertTrue(result.changed)
            self.assertIsNotNone(result.backup_root)
            self.assertRegex(result.backup_root.name, r"^\d{8}T\d{6}\.\d{6}Z-[0-9a-f]{8}$")
            self.assertEqual(
                (result.backup_root / "gateway_state.json").read_bytes(),
                original_state,
            )
            self.assertEqual(
                (result.backup_root / "gateway_audit.mossjson").read_bytes(),
                original_audit,
            )
            manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["status"], "completed")
            self.assertEqual(manifest["old_hostname"], OLD_HOST)
            self.assertEqual(manifest["new_hostname"], NEW_HOST)
            self.assertEqual(len(manifest["state_backup"]["sha256"]), 64)
            self.assertEqual(len(manifest["audit_backup"]["sha256"]), 64)

            after = store.load()
            self.assertEqual(after["registration"]["public_hostname"], NEW_HOST)
            self.assertEqual(
                after["registration"]["tunnel_id"],
                before["registration"]["tunnel_id"],
            )
            self.assertEqual(
                after["registration"]["registration_id"],
                before["registration"]["registration_id"],
            )
            self.assertEqual(response_path.read_bytes(), response_bytes)
            self.assertEqual(credential_decoy.read_bytes(), credential_bytes)
            audit = json.loads(store.audit_path.read_text(encoding="utf-8"))
            self.assertEqual(len(audit["events"]), 2)
            self.assertEqual(audit["events"][-1]["event_type"], "registration_saved")
            serialized_audit = json.dumps(audit)
            self.assertNotIn(credential_bytes.decode("ascii"), serialized_audit)

    def test_dry_run_validates_without_writing_or_backing_up(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = _configured_store(root)
            state_bytes = store.path.read_bytes()
            audit_bytes = store.audit_path.read_bytes()

            result = DirectWorkerHostnameMigration(
                root,
                data_root=root,
                state_store=store,
            ).migrate(
                expected_school_id=SCHOOL_ID,
                expected_old_hostname=OLD_HOST,
                new_hostname=NEW_HOST,
                dry_run=True,
            )

            self.assertFalse(result.changed)
            self.assertIsNone(result.backup_root)
            self.assertEqual(store.path.read_bytes(), state_bytes)
            self.assertEqual(store.audit_path.read_bytes(), audit_bytes)
            migration_root = root / "backups" / "gateway-hostname-migrations"
            self.assertFalse(migration_root.exists())

    def test_rejects_wrong_identity_mode_status_and_hostname_before_backup(self) -> None:
        cases = (
            ("school", {"expected_school_id": "999999"}),
            ("old", {"expected_old_hostname": f"{SCHOOL_ID}.other.workers.dev"}),
            ("scheme", {"new_hostname": f"https://{NEW_HOST}/"}),
            ("prefix", {"new_hostname": f"999999.motiong.workers.dev"}),
            ("same", {"new_hostname": OLD_HOST}),
        )
        for label, changes in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                store = _configured_store(root)
                arguments = {
                    "expected_school_id": SCHOOL_ID,
                    "expected_old_hostname": OLD_HOST,
                    "new_hostname": NEW_HOST,
                }
                arguments.update(changes)
                before = store.path.read_bytes()
                with self.assertRaises(DirectWorkerHostnameMigrationError):
                    DirectWorkerHostnameMigration(
                        root,
                        data_root=root,
                        state_store=store,
                    ).migrate(**arguments)
                self.assertEqual(store.path.read_bytes(), before)
                self.assertFalse(
                    (root / "backups" / "gateway-hostname-migrations").exists()
                )

        for label, state_change in (
            ("connected", {"gateway_status": "connected"}),
            ("inactive", {"authorization_status": "REVOKED"}),
        ):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                store = _configured_store(root)
                state = json.loads(store.path.read_text(encoding="utf-8"))
                state.update(state_change)
                store.path.write_text(json.dumps(state), encoding="utf-8")
                before = store.path.read_bytes()
                with self.assertRaises(DirectWorkerHostnameMigrationError):
                    DirectWorkerHostnameMigration(
                        root,
                        data_root=root,
                        state_store=store,
                    ).migrate(
                        expected_school_id=SCHOOL_ID,
                        expected_old_hostname=OLD_HOST,
                        new_hostname=NEW_HOST,
                    )
                self.assertEqual(store.path.read_bytes(), before)
                self.assertFalse(
                    (root / "backups" / "gateway-hostname-migrations").exists()
                )

    def test_failure_after_store_write_restores_exact_state_and_audit(self) -> None:
        class _FailAfterWriteStore(GatewayStateStore):
            def save_registration(self, **values):
                super().save_registration(**values)
                raise OSError("simulated failure after the audit write")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            base_store = _configured_store(root)
            original_state = base_store.path.read_bytes()
            original_audit = base_store.audit_path.read_bytes()
            failing_store = _FailAfterWriteStore(root, data_root=root)
            service = DirectWorkerHostnameMigration(
                root,
                data_root=root,
                state_store=failing_store,
            )

            with self.assertRaisesRegex(
                DirectWorkerHostnameMigrationError,
                "original Gateway state and audit were restored",
            ):
                service.migrate(
                    expected_school_id=SCHOOL_ID,
                    expected_old_hostname=OLD_HOST,
                    new_hostname=NEW_HOST,
                )

            self.assertEqual(failing_store.path.read_bytes(), original_state)
            self.assertEqual(failing_store.audit_path.read_bytes(), original_audit)
            manifests = list(
                (root / "backups" / "gateway-hostname-migrations").glob(
                    "*/manifest.json"
                )
            )
            self.assertEqual(len(manifests), 1)
            manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
            self.assertEqual(manifest["status"], "rolled_back")


if __name__ == "__main__":
    unittest.main()
