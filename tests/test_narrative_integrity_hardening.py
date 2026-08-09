from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from school_csm_control_center.services.narrative_report import LocalNarrativeGenerator
from school_csm_control_center.storage.narrative_report_store import (
    NarrativeReportStore,
    NarrativeReportStoreError,
)
from school_csm_control_center.storage.print_history_store import (
    PrintHistoryStore,
    PrintHistoryStoreError,
)
from tests.test_narrative_report_core import save_snapshot


REFERENCE = {
    "type": "school-csm-dashboard-snapshot",
    "schema_version": "1.0",
    "snapshot_id": "snapshot-integrity-test",
    "manifest_path": "dashboard_snapshots/snapshot-integrity-test/manifest.mossjson",
    "digest_sha256": "a" * 64,
}


class PrintHistoryIntegrityHardeningTests(unittest.TestCase):
    def test_snapshot_reference_is_validated_before_it_enters_audit_history(self) -> None:
        with TemporaryDirectory() as folder:
            store = PrintHistoryStore(Path(folder), data_root=folder)
            reserved = store.reserve(generated_at="2026-08-09T08:00:00+08:00")
            unsafe = {**REFERENCE, "manifest_path": "../outside/manifest.mossjson"}

            with self.assertRaisesRegex(ValueError, "relative and safe"):
                store.mark_submitting(
                    reserved["id"],
                    {"dashboard_snapshot": unsafe},
                )

            unchanged = store.get(reserved["id"])
            self.assertEqual(unchanged["status"], "reserved")
            self.assertNotIn("dashboard_snapshot", unchanged)

    def test_successful_duplicate_cannot_downgrade_or_replace_evidence(self) -> None:
        with TemporaryDirectory() as folder:
            store = PrintHistoryStore(Path(folder), data_root=folder)
            reserved = store.reserve(generated_at="2026-08-09T08:00:00+08:00")
            printed = {
                "control_number": reserved["control_number"],
                "generated_at": reserved["generated_at"],
                "printed_at": "2026-08-09T08:05:00+08:00",
                "scope": "Complete Dashboard",
                "page_count": 3,
                "printer_name": "Office Printer",
                "copies": 1,
                "dashboard_snapshot": REFERENCE,
            }
            submitted = store.mark_submitted(reserved["id"], printed)
            confirmed = store.mark_confirmed(submitted["id"])

            repeated = store.record_success(printed)

            self.assertEqual(repeated["status"], "confirmed")
            self.assertEqual(store.get(confirmed["id"])["status"], "confirmed")
            changed = {**printed, "printer_name": "Different Printer"}
            with self.assertRaisesRegex(PrintHistoryStoreError, "different print evidence"):
                store.record_success(changed)

            stale_reference = {**REFERENCE, "digest_sha256": "b" * 64}
            with self.assertRaisesRegex(PrintHistoryStoreError, "changed during the reprint"):
                store.record_reprint_attempt(
                    confirmed["id"],
                    "submitted",
                    {"printer_name": "Office Printer"},
                    expected_snapshot_reference=stale_reference,
                )
            self.assertNotIn("reprint_attempts", store.get(confirmed["id"]))

    def test_unrecognized_history_envelope_is_preserved_not_migrated(self) -> None:
        for field, value in (("file_type", "Different Product"), ("schema_version", "99.0")):
            with self.subTest(field=field), TemporaryDirectory() as folder:
                store = PrintHistoryStore(Path(folder), data_root=folder)
                store.path.parent.mkdir(parents=True, exist_ok=True)
                payload = {
                    "file_type": store.FILE_TYPE,
                    "schema_version": store.SCHEMA_VERSION,
                    "records": [],
                }
                payload[field] = value
                store.path.write_text(json.dumps(payload), encoding="utf-8")
                before = store.path.read_bytes()

                with self.assertRaisesRegex(PrintHistoryStoreError, "left unchanged"):
                    store.reserve(generated_at="2026-08-09T08:00:00+08:00")

                self.assertEqual(store.path.read_bytes(), before)
                self.assertIsNotNone(store.last_recovery_copy)


class NarrativeOptimisticLockTests(unittest.TestCase):
    def test_revision_approval_and_print_writes_reject_stale_evidence(self) -> None:
        with TemporaryDirectory() as folder:
            root = Path(folder)
            snapshot_store, reference, manifest = save_snapshot(root)
            store = NarrativeReportStore(root, snapshot_store=snapshot_store)
            document = LocalNarrativeGenerator().generate(manifest)
            created = store.create(reference, document, operator="Operator")
            revision = created["revisions"][0]

            with self.assertRaisesRegex(NarrativeReportStoreError, "revision changed"):
                store.append_revision(
                    created["id"],
                    {**document, "title": "Changed"},
                    expected_current_revision_id="stale-revision",
                    expected_content_sha256=revision["content_sha256"],
                    expected_approval_id=None,
                )
            self.assertEqual(store.get(created["id"])["current_revision"], 1)

            approved = store.approve(
                created["id"],
                "School Head",
                expected_current_revision_id=revision["revision_id"],
                expected_content_sha256=revision["content_sha256"],
                expected_approval_id=None,
            )
            with self.assertRaisesRegex(NarrativeReportStoreError, "approval changed"):
                store.record_print(
                    created["id"],
                    printer_name="Office Printer",
                    page_count=7,
                    expected_current_revision_id=revision["revision_id"],
                    expected_content_sha256=revision["content_sha256"],
                    expected_approval_id="stale-approval",
                    expected_snapshot_sha256=approved["source"]["snapshot_sha256"],
                )
            self.assertEqual(store.get(created["id"])["print_audits"], [])


if __name__ == "__main__":
    unittest.main()
