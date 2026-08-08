from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from school_csm_control_center.storage.mrs_registry import MRSRegistryStore


PRINTER = {
    "name": "Test USB Printer",
    "status": "Ready",
    "connection_type": "USB",
    "driver": "Test Driver",
    "port": "USB001",
    "computer_name": "TEST-PC",
    "a4_capable": True,
}
OPERATOR = {"user_id": "USR-1", "username": "scanner01", "display_name": "Juan Dela Cruz"}


class MRSRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.registry = MRSRegistryStore(self.root)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _batch(self, *, month: int, count: int = 1, include_reprints: bool = True):
        stamp = datetime(2026, month, 10, 8, 0, tzinfo=timezone.utc)
        return self.registry.create_print_batch(
            school_id="123627",
            language="English",
            template_version="CSM-MRS-A4-2026-04-EN",
            new_form_count=count,
            printer=PRINTER,
            printed_by="Control Center Operator",
            generated_at=stamp,
            include_pending_reprints=include_reprints,
        )

    def _confirm(self, batch, failed=()):
        self.registry.mark_batch_submitted(batch["print_batch_id"], accepted=True)
        return self.registry.confirm_print_results(batch["print_batch_id"], failed)

    def test_sequence_continues_across_months_and_resets_by_year(self) -> None:
        july = self._batch(month=7, count=2)
        self._confirm(july)
        august = self._batch(month=8, count=1)
        self.assertEqual(august["new_control_number_start"], "CSM-MRS-123627-2026-08-0003")
        self._confirm(august)
        january = self.registry.create_print_batch(
            school_id="123627",
            language="English",
            template_version="CSM-MRS-A4-2026-04-EN",
            new_form_count=1,
            printer=PRINTER,
            printed_by="Control Center Operator",
            generated_at=datetime(2027, 1, 2, tzinfo=timezone.utc),
        )
        self.assertEqual(january["new_control_number_start"], "CSM-MRS-123627-2027-01-0001")

    def test_successful_print_is_red_until_accounted_by_scan(self) -> None:
        batch = self._batch(month=7)
        control = batch["page_control_numbers"][0]
        self._confirm(batch)
        printed = self.registry.get_form(control)
        self.assertEqual(printed["current_font_state"], "red")
        self.assertEqual(printed["printed_copy_status"], "Awaiting Scan")
        attempt = self.registry.record_scan_attempt(
            control_number=control,
            barcode_status="normal",
            scanner_submission_id="SCN-1",
            scanner_operator=OPERATOR,
            validity="valid",
            analysis_status="included",
            response_record_id="RESP-1",
        )
        self.assertEqual(attempt["response_validity"], "Valid")
        accounted = self.registry.get_form(control)
        self.assertEqual(accounted["current_font_state"], "regular")
        self.assertTrue(accounted["valid_response_received"])
        self.assertEqual(accounted["analysis_status"], "Included")

    def test_failed_print_is_queued_and_reprint_keeps_control_number(self) -> None:
        first = self._batch(month=7, count=2)
        failed_control = first["page_control_numbers"][0]
        self._confirm(first, failed=(failed_control,))
        failed = self.registry.get_form(failed_control)
        self.assertTrue(failed["pending_reprint"])
        self.assertEqual(failed["current_print_status"], "Print Failed")
        second = self._batch(month=8, count=1)
        self.assertEqual(second["pages"][0]["control_number"], failed_control)
        self.assertTrue(second["pages"][0]["is_reprint"])
        self.assertEqual(second["pages"][1]["control_number"], "CSM-MRS-123627-2026-08-0003")

    def test_crossed_out_scan_is_excluded_and_queued_for_reprint(self) -> None:
        batch = self._batch(month=7)
        control = batch["page_control_numbers"][0]
        self._confirm(batch)
        attempt = self.registry.record_scan_attempt(
            control_number=control,
            barcode_status="crossed_out",
            scanner_submission_id="SCN-SPOILED",
            scanner_operator=OPERATOR,
            validity="invalid",
            invalid_reason="Respondent mistake or spoiled form",
            analysis_status="excluded",
        )
        self.assertEqual(attempt["analysis_status"], "Excluded")
        form = self.registry.get_form(control)
        self.assertEqual(form["current_font_state"], "regular")
        self.assertEqual(form["current_print_status"], "Pending Reprint")
        self.assertTrue(form["pending_reprint"])
        self.assertEqual(form["number_of_invalid_scans"], 1)

    def test_only_one_valid_response_is_accepted_per_control_number(self) -> None:
        batch = self._batch(month=7)
        control = batch["page_control_numbers"][0]
        self._confirm(batch)
        self.registry.record_scan_attempt(
            control_number=control,
            barcode_status="normal",
            scanner_submission_id="SCN-VALID",
            scanner_operator=OPERATOR,
            validity="valid",
            analysis_status="included",
        )
        duplicate = self.registry.record_scan_attempt(
            control_number=control,
            barcode_status="normal",
            scanner_submission_id="SCN-DUPLICATE",
            scanner_operator=OPERATOR,
            validity="valid",
            analysis_status="included",
        )
        self.assertEqual(duplicate["response_validity"], "Duplicate")
        self.assertEqual(duplicate["analysis_status"], "Excluded")


if __name__ == "__main__":
    unittest.main()
