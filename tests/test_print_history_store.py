from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from school_csm_control_center.storage.print_history_store import (
    PrintHistoryStore,
    PrintHistoryStoreError,
)


class PrintHistoryStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = PrintHistoryStore(self.root)
        self.generated_at = "2026-07-18T09:15:00+08:00"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _record(self, control_number: str, **changes) -> dict:
        record = {
            "control_number": control_number,
            "generated_at": self.generated_at,
            "printed_at": "2026-07-18T09:16:02+08:00",
            "scope": "15 of 15 responses · All dates",
            "page_count": 3,
            "printer_name": "DepEd Office Printer",
            "copies": 2,
            "selected_pages": [1, 2, 3],
            "survey_count": 15,
            "settings": {
                "paper_size": "A4",
                "orientation": "landscape",
                "color_mode": "Color",
                "duplex": "Long edge",
                "resolution_dpi": 600,
                "copies": 2,
            },
        }
        record.update(changes)
        return record

    def test_success_is_atomic_formal_and_preserves_printed_identity(self) -> None:
        self.assertFalse(self.store.path.exists())
        control = self.store.next_control_number(self.generated_at)
        self.assertEqual(control, "CSMS-PRN-2026-07-0001")
        self.assertFalse(self.store.path.exists())

        saved = self.store.record_success(self._record(control))

        self.assertEqual(saved["control_number"], control)
        self.assertEqual(saved["barcode_value"], control)
        self.assertEqual(saved["generated_at"], self.generated_at)
        self.assertEqual(saved["printed_at"], "2026-07-18T09:16:02+08:00")
        self.assertEqual(saved["page_count"], 3)
        self.assertEqual(saved["copies"], 2)
        self.assertEqual(saved["selected_pages"], [1, 2, 3])
        self.assertEqual(saved["survey_count"], 15)
        self.assertEqual(saved["settings"]["paper_size"], "A4")
        self.assertEqual(len(self.store.list()), 1)
        self.assertEqual(
            self.store.next_control_number(self.generated_at),
            "CSMS-PRN-2026-07-0002",
        )
        document = json.loads(self.store.path.read_text(encoding="utf-8"))
        self.assertEqual(document["file_type"], PrintHistoryStore.FILE_TYPE)
        self.assertEqual(len(document["records"]), 1)
        self.assertEqual(list(self.store.path.parent.glob("*.tmp")), [])

    def test_wrong_control_is_rejected_and_duplicate_success_is_idempotent(self) -> None:
        with self.assertRaisesRegex(PrintHistoryStoreError, "next available"):
            self.store.record_success(
                self._record("CSMS-PRN-2026-07-0002")
            )
        self.assertFalse(self.store.path.exists())

        first = self.store.next_control_number(self.generated_at)
        saved = self.store.record_success(self._record(first))
        before = self.store.path.read_bytes()
        repeated = self.store.record_success(self._record(first))
        self.assertEqual(self.store.path.read_bytes(), before)
        self.assertEqual(repeated, saved)
        self.assertEqual(len(self.store.list()), 1)

    def test_reservation_lifecycle_keeps_cancelled_failed_and_submitted_audits(self) -> None:
        cancelled = self.store.reserve(
            {"scope": "Current Dashboard", "survey_count": 15},
            generated_at=self.generated_at,
        )
        self.assertEqual(cancelled["status"], "reserved")
        cancelled = self.store.mark_cancelled(cancelled["id"], "Preview closed.")
        self.assertEqual(cancelled["status"], "cancelled")

        failed = self.store.reserve(generated_at=self.generated_at)
        failed = self.store.mark_submitting(failed["id"])
        failed = self.store.mark_failed(failed["id"], "Printer unavailable.")
        self.assertEqual(failed["status"], "failed")

        submitted = self.store.reserve(generated_at=self.generated_at)
        submitted = self.store.mark_submitting(submitted["id"])
        submitted = self.store.mark_submitted(
            submitted["id"],
            self._record(submitted["control_number"]),
        )
        self.assertEqual(submitted["status"], "submitted")
        self.assertEqual(
            {row["status"] for row in self.store.list()},
            {"submitted", "failed", "cancelled"},
        )

    def test_incomplete_reservations_are_interrupted_on_restart(self) -> None:
        reserved = self.store.reserve(generated_at=self.generated_at)
        submitting = self.store.reserve(generated_at=self.generated_at)
        self.store.mark_submitting(submitting["id"])
        interrupted = self.store.interrupt_incomplete()
        self.assertEqual({row["id"] for row in interrupted}, {reserved["id"], submitting["id"]})
        self.assertEqual(
            {self.store.get(reserved["id"])["status"], self.store.get(submitting["id"])["status"]},
            {"interrupted"},
        )

    def test_preview_or_failed_job_shapes_cannot_create_history(self) -> None:
        control = self.store.next_control_number(self.generated_at)
        with self.assertRaisesRegex(ValueError, "positive whole number"):
            self.store.record_success(self._record(control, page_count=0))
        self.assertFalse(self.store.path.exists())

        with self.assertRaisesRegex(ValueError, "printer name"):
            self.store.record_success(
                self._record(control, printer_name="", settings={})
            )
        self.assertFalse(self.store.path.exists())

        with self.assertRaisesRegex(ValueError, "barcode value"):
            self.store.record_success(
                self._record(control, barcode_value="CSMS-PRN-2026-07-9999")
            )
        self.assertFalse(self.store.path.exists())

    def test_unreadable_existing_history_is_never_overwritten(self) -> None:
        self.store.path.parent.mkdir(parents=True)
        self.store.path.write_text("{not-json", encoding="utf-8")
        before = self.store.path.read_bytes()
        control = "CSMS-PRN-2026-07-0001"
        with self.assertRaisesRegex(PrintHistoryStoreError, "left unchanged"):
            self.store.record_success(self._record(control))
        self.assertEqual(self.store.path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
