from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from school_csm_control_center.storage.print_history_store import PrintHistoryStore


SNAPSHOT_REFERENCE = {
    "schema_version": "1.0",
    "snapshot_id": "snapshot-test",
    "manifest_path": "data/csm_survey/dashboard_print_snapshots/snapshot-test/manifest.json",
    "digest_sha256": "a" * 64,
}


class NarrativePrintHistoryTests(unittest.TestCase):
    def test_legacy_success_without_snapshot_is_ineligible(self) -> None:
        eligible, reason = PrintHistoryStore.narrative_eligibility(
            {"status": "confirmed", "control_number": "CSMS-PRN-2026-08-0001"}
        )
        self.assertFalse(eligible)
        self.assertIn("legacy", reason.casefold())

    def test_submitted_and_confirmed_snapshots_are_eligible(self) -> None:
        for status in ("submitted", "confirmed"):
            with self.subTest(status=status):
                eligible, reason = PrintHistoryStore.narrative_eligibility(
                    {"status": status, "dashboard_snapshot": SNAPSHOT_REFERENCE}
                )
                self.assertTrue(eligible, reason)

    def test_reprint_keeps_original_control_and_appends_audit(self) -> None:
        with TemporaryDirectory() as folder:
            root = Path(folder)
            store = PrintHistoryStore(root, data_root=root)
            reserved = store.reserve(generated_at="2026-08-08T09:00:00+08:00")
            submitted = store.mark_submitted(
                reserved["id"],
                {
                    "control_number": reserved["control_number"],
                    "generated_at": reserved["generated_at"],
                    "printed_at": "2026-08-08T09:05:00+08:00",
                    "scope": "Date range: August 2026",
                    "page_count": 3,
                    "printer_name": "Test Printer",
                    "copies": 1,
                    "dashboard_snapshot": SNAPSHOT_REFERENCE,
                },
            )
            attempt = store.record_reprint_attempt(
                submitted["id"],
                "submitted",
                {"printer_name": "Repair Printer", "page_count": 3},
            )

            self.assertEqual(
                attempt["original_control_number"], submitted["control_number"]
            )
            refreshed = store.get(submitted["id"])
            self.assertIsNotNone(refreshed)
            assert refreshed is not None
            self.assertEqual(refreshed["control_number"], submitted["control_number"])
            self.assertEqual(len(refreshed["reprint_attempts"]), 1)
            self.assertEqual(refreshed["reprint_attempts"][0]["outcome"], "submitted")

    def test_failed_print_with_snapshot_is_ineligible(self) -> None:
        eligible, reason = PrintHistoryStore.narrative_eligibility(
            {"status": "failed", "dashboard_snapshot": SNAPSHOT_REFERENCE}
        )
        self.assertFalse(eligible)
        self.assertIn("failed", reason.casefold())


if __name__ == "__main__":
    unittest.main()
