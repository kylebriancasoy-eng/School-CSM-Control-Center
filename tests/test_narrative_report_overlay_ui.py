from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QWidget

from school_csm_control_center.storage.narrative_report_store import NarrativeReportStore
from school_csm_control_center.ui.narrative_report_overlay import NarrativeReportOverlay
from school_csm_control_center.ui.narrative_report_printing import NarrativeReportRenderer
from tests.test_narrative_report_core import save_snapshot


class _NoCredential:
    def load(self):
        return None

    def exists(self):
        return False

    def save(self, _value):
        raise AssertionError("The local narrative flow must not request an API key.")

    def delete(self):
        return False


class _FakePrinter:
    class PrinterMode:
        HighResolution = object()

    def __init__(self, *_args):
        self.document_name = ""
        self.copies = 1

    def setDocName(self, value):
        self.document_name = str(value)

    def setCopyCount(self, value):
        self.copies = int(value)


class NarrativeReportOverlayUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.snapshot_store, self.reference, _manifest = save_snapshot(self.root)
        self.report_store = NarrativeReportStore(
            self.root,
            snapshot_store=self.snapshot_store,
        )
        self.parent = QWidget()
        self.parent.resize(1280, 760)
        self.parent.show()
        self.overlay = NarrativeReportOverlay(
            self.parent,
            project_root=self.root,
            snapshot_store=self.snapshot_store,
            report_store=self.report_store,
            credential_store=_NoCredential(),
            printer_provider=lambda: [],
        )
        self.print_record = {
            "id": "print-record-001",
            "control_number": "CSMS-PRN-2026-07-0001",
            "status": "submitted",
            "dashboard_snapshot": self.reference,
        }

    def tearDown(self) -> None:
        self.overlay.close_overlay()
        self.parent.close()
        self.overlay.deleteLater()
        self.parent.deleteLater()
        self.app.processEvents()
        self.temporary.cleanup()

    def test_local_edit_approval_print_and_approval_invalidation(self) -> None:
        self.assertTrue(
            self.overlay.open_for_print_record(self.print_record, operator="Test Operator")
        )
        self.app.processEvents()
        self.assertEqual(len(self.overlay._pages), 7)
        page_specs = NarrativeReportRenderer.build_page_specs(
            self.overlay._print_document()
        )
        self.assertIn("Dashboard summarizes 12 survey responses", page_specs[0]["body"])
        self.assertNotIn("No narrative was recorded", "\n".join(row["body"] for row in page_specs))

        self.overlay.approver_input.setText("School Head")
        self.overlay._approve()
        self.assertTrue(self.overlay._is_approved())

        self.overlay.printer_combo.clear()
        self.overlay.printer_combo.addItem("Test Printer", "Test Printer")
        self.overlay._printer_infos["Test Printer"] = object()
        self.overlay._print_executor = lambda _printer, pages: len(pages)
        with patch(
            "school_csm_control_center.ui.narrative_report_overlay.QPrinter",
            _FakePrinter,
        ):
            self.overlay._print()
        printed = self.report_store.get(str(self.overlay._report.get("id") or ""))
        self.assertEqual(len(printed["print_audits"]), 1)
        self.assertEqual(printed["print_audits"][0]["page_count"], 7)

        editor = self.overlay.section_editors["conclusion"]
        editor.setPlainText(editor.toPlainText() + "\n\nReviewed by the operator.")
        self.overlay._save_revision()
        revised = self.report_store.get(str(self.overlay._report.get("id") or ""))
        self.assertEqual(revised["current_revision"], 2)
        self.assertIsNone(revised["approval"])
        self.assertEqual(revised["approval_history"][0]["approved_by"], "School Head")

    def test_partial_report_output_is_never_recorded_as_a_successful_print(self) -> None:
        self.assertTrue(
            self.overlay.open_for_print_record(self.print_record, operator="Test Operator")
        )
        self.overlay.approver_input.setText("School Head")
        self.overlay._approve()
        self.overlay.printer_combo.clear()
        self.overlay.printer_combo.addItem("Test Printer", "Test Printer")
        self.overlay._printer_infos["Test Printer"] = object()
        self.overlay._print_executor = lambda _printer, _pages: 1

        with patch(
            "school_csm_control_center.ui.narrative_report_overlay.QPrinter",
            _FakePrinter,
        ):
            self.overlay._print()

        report = self.report_store.get(str(self.overlay._report.get("id") or ""))
        self.assertEqual(report["print_audits"], [])
        self.assertIn("complete Narrative Report", self.overlay.status_label.text())


if __name__ == "__main__":
    unittest.main()
