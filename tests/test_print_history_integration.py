from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPageSize
from PySide6.QtPrintSupport import QPrinter, QPrintPreviewWidget
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog

from school_csm_control_center.ui.dashboard_print_overlay import DashboardPrintOverlay
from school_csm_control_center.ui.dashboard_printing import (
    DashboardPrintRenderer,
    DashboardSnapshot,
    PrintPage,
    PrintReportMetadata,
    code39_modules,
)
from school_csm_control_center.ui.main_window import SchoolCSMControlCenterWindow


class _PrinterInfo:
    """Driver-free capability object for the in-app printer selector."""

    def __init__(self, name: str = "Injected Records Printer") -> None:
        self._name = name
        self._a4 = QPageSize(QPageSize.PageSizeId.A4)

    def printerName(self) -> str:
        return self._name

    def supportedPageSizes(self) -> list[QPageSize]:
        return [self._a4]

    def defaultPageSize(self) -> QPageSize:
        return self._a4

    def supportedResolutions(self) -> list[int]:
        return [300]

    def supportedColorModes(self) -> list[QPrinter.ColorMode]:
        return [QPrinter.ColorMode.Color]

    def defaultColorMode(self) -> QPrinter.ColorMode:
        return QPrinter.ColorMode.Color

    def supportedDuplexModes(self) -> list[QPrinter.DuplexMode]:
        return [QPrinter.DuplexMode.DuplexNone]

    def defaultDuplexMode(self) -> QPrinter.DuplexMode:
        return QPrinter.DuplexMode.DuplexNone


class _Executor:
    def __init__(self, *, failure: Exception | None = None) -> None:
        self.failure = failure
        self.calls: list[tuple[QPrinter, DashboardSnapshot, dict[str, object]]] = []

    def __call__(
        self,
        printer: QPrinter,
        snapshot: DashboardSnapshot,
        **options: object,
    ) -> int:
        self.calls.append((printer, snapshot, dict(options)))
        if self.failure is not None:
            raise self.failure
        return len(options.get("selected_pages", []))


class PrintHistoryLifecycleIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.fixed_time = datetime(
            2026,
            7,
            18,
            9,
            15,
            30,
            tzinfo=timezone(timedelta(hours=8)),
        )
        self.image = QImage(
            1200,
            3600,
            QImage.Format.Format_ARGB32_Premultiplied,
        )
        self.image.fill(QColor("#07192A"))
        self.plan = [
            PrintPage(1, QRectF(0, 0, 1200, 1200), QRectF(0, 0, 900, 900)),
            PrintPage(2, QRectF(0, 1200, 1200, 1200), QRectF(0, 0, 900, 900)),
            PrintPage(3, QRectF(0, 2400, 1200, 1200), QRectF(0, 0, 900, 900)),
        ]
        self.captured_metadata: list[PrintReportMetadata | None] = []

        def capture(_board, *, render_scale=2.0, metadata=None):
            self.captured_metadata.append(metadata)
            return DashboardSnapshot(
                self.image,
                600,
                1800,
                float(render_scale),
                {
                    "report_header": QRectF(14, 12, 572, 116),
                    "header": QRectF(14, 137, 572, 64),
                    "filters": QRectF(14, 210, 572, 54),
                    "body": QRectF(14, 273, 572, 1515),
                },
                report_metadata=metadata,
            )

        self.capture_patcher = patch.object(
            DashboardPrintRenderer,
            "capture",
            side_effect=capture,
        )
        self.paint_patcher = patch.object(
            DashboardPrintRenderer,
            "paint",
            return_value=len(self.plan),
        )
        self.plan_patcher = patch.object(
            DashboardPrintRenderer,
            "printer_page_plan",
            return_value=self.plan,
        )
        self.capture_mock = self.capture_patcher.start()
        self.paint_mock = self.paint_patcher.start()
        self.plan_mock = self.plan_patcher.start()

        self.window = SchoolCSMControlCenterWindow(self.root)
        self.window.resize(1280, 760)
        self.window.show()
        self.overlay = self.window.print_overlay
        self.overlay._printer_provider = lambda: [_PrinterInfo()]
        self.overlay._clock = lambda: self.fixed_time
        self.app.processEvents()

    def tearDown(self) -> None:
        self.overlay.close_overlay()
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.plan_patcher.stop()
        self.paint_patcher.stop()
        self.capture_patcher.stop()
        self.temporary.cleanup()

    def _open_preview(self) -> None:
        QTest.mouseClick(
            self.window.dashboard.print_button,
            Qt.MouseButton.LeftButton,
        )
        self.app.processEvents()
        self.assertTrue(self.overlay.isVisible())

    def _assert_no_history(self) -> None:
        self.assertEqual(self.window.print_store.list(), [])
        self.assertFalse(self.window.print_store.path.exists())
        self.assertEqual(self.window.history.prints_table.rowCount(), 0)

    def _assert_audit_statuses(
        self,
        expected: dict[str, str],
    ) -> dict[str, dict]:
        records = self.window.print_store.list()
        by_control = {
            str(record.get("control_number") or ""): record for record in records
        }
        self.assertEqual(
            {
                control_number: str(record.get("status") or "")
                for control_number, record in by_control.items()
            },
            expected,
        )
        self.assertTrue(self.window.print_store.path.exists())
        self.assertEqual(self.window.history.prints_table.rowCount(), len(records))
        return by_control

    def test_preview_cancel_failure_then_success_obey_one_way_audit_lifecycle(self) -> None:
        self._assert_no_history()

        self._open_preview()
        metadata = self.overlay.report_metadata
        self.assertIsNotNone(metadata)
        self.assertEqual(metadata.control_number, "CSMS-PRN-2026-07-0001")
        self.assertEqual(metadata.generated_at, self.fixed_time)
        self.assertIs(self.overlay._snapshot.report_metadata, metadata)
        self.assertIs(self.captured_metadata[-1], metadata)
        self.assertIn(metadata.control_number, self.overlay.scope_label.text())
        self.assertIn("18 July 2026", self.overlay.scope_label.text())
        self._assert_audit_statuses(
            {"CSMS-PRN-2026-07-0001": "reserved"}
        )

        QTest.mouseClick(self.overlay.refresh_button, Qt.MouseButton.LeftButton)
        self.app.processEvents()
        self.assertIs(self.overlay.report_metadata, metadata)
        self.assertIs(self.captured_metadata[-1], metadata)
        self._assert_audit_statuses(
            {"CSMS-PRN-2026-07-0001": "reserved"}
        )

        QTest.mouseClick(self.overlay.close_button, Qt.MouseButton.LeftButton)
        self.app.processEvents()
        self._assert_audit_statuses(
            {"CSMS-PRN-2026-07-0001": "cancelled"}
        )

        failed_executor = _Executor(failure=RuntimeError("printer offline"))
        self.overlay._print_executor = failed_executor
        self._open_preview()
        self.assertEqual(
            self.overlay.report_metadata.control_number,
            "CSMS-PRN-2026-07-0002",
        )
        QTest.mouseClick(self.overlay.print_button, Qt.MouseButton.LeftButton)
        self.app.processEvents()
        self.assertEqual(len(failed_executor.calls), 1)
        self.assertTrue(self.overlay.isVisible())
        self.assertIn("printer offline", self.overlay.status_label.text())
        self._assert_audit_statuses(
            {
                "CSMS-PRN-2026-07-0001": "cancelled",
                "CSMS-PRN-2026-07-0002": "failed",
            }
        )

        self.overlay.close_overlay()
        successful_executor = _Executor()
        self.overlay._print_executor = successful_executor
        self._open_preview()
        printed_metadata = self.overlay.report_metadata
        QTest.mouseClick(self.overlay.print_button, Qt.MouseButton.LeftButton)
        self.app.processEvents()

        self.assertEqual(len(successful_executor.calls), 1)
        self.assertFalse(self.overlay.isVisible())
        records = self._assert_audit_statuses(
            {
                "CSMS-PRN-2026-07-0001": "cancelled",
                "CSMS-PRN-2026-07-0002": "failed",
                "CSMS-PRN-2026-07-0003": "submitted",
            }
        )
        record = records["CSMS-PRN-2026-07-0003"]
        self.assertEqual(record["control_number"], printed_metadata.control_number)
        self.assertEqual(record["control_number"], "CSMS-PRN-2026-07-0003")
        self.assertEqual(record["generated_at"], self.fixed_time.isoformat())
        self.assertEqual(record["printed_at"], self.fixed_time.isoformat())
        self.assertEqual(record["barcode_value"], record["control_number"])
        self.assertEqual(record["printer_name"], "Injected Records Printer")
        self.assertEqual(record["page_count"], 3)
        self.assertEqual(record["selected_pages"], [1, 2, 3])
        self.assertEqual(self.window.history.prints_table.rowCount(), 3)

        self.window.show_board("history")
        self.window.history.show_section("prints")
        matching_row = next(
            row
            for row in range(self.window.history.prints_table.rowCount())
            if self.window.history.prints_table.item(row, 1).text()
            == record["control_number"]
        )
        self.window.history.prints_table.selectRow(matching_row)
        self.app.processEvents()
        selected = self.window.history.selected_print_record()
        self.assertEqual(selected["id"], record["id"])
        QTest.mouseClick(
            self.window.history.view_button,
            Qt.MouseButton.LeftButton,
        )
        self.app.processEvents()
        self.assertTrue(self.window.prompt.isVisible())
        self.assertEqual(self.window.prompt.title_label.text(), "Dashboard print record")
        self.assertEqual(self.window.prompt.message_label.text(), record["control_number"])
        self.assertIn("Barcode value", self.window.prompt.detail_label.text())
        self.assertIn(record["barcode_value"], self.window.prompt.detail_label.text())
        self.assertEqual(self.window.findChildren(QDialog), [])

        QTest.mouseClick(self.window.prompt.accept_button, Qt.MouseButton.LeftButton)
        self.window.show_board("dashboard")
        self._open_preview()
        self.assertEqual(
            self.overlay.report_metadata.control_number,
            "CSMS-PRN-2026-07-0004",
        )
        self.overlay.close_overlay()
        self._assert_audit_statuses(
            {
                "CSMS-PRN-2026-07-0001": "cancelled",
                "CSMS-PRN-2026-07-0002": "failed",
                "CSMS-PRN-2026-07-0003": "submitted",
                "CSMS-PRN-2026-07-0004": "cancelled",
            }
        )

    def test_preview_exposes_all_pages_in_one_multi_page_view(self) -> None:
        self._open_preview()
        self.assertEqual(len(self.overlay._current_plan()), 3)
        self.assertEqual(self.overlay.page_label.text(), "Page 1 of 3")
        self.assertEqual(
            self.overlay.preview.viewMode(),
            QPrintPreviewWidget.ViewMode.AllPagesView,
            "The preview should show the vertical multi-page report as one all-pages view.",
        )

    def test_snapshot_reprint_retains_control_and_does_not_consume_sequence(self) -> None:
        first_executor = _Executor()
        self.overlay._print_executor = first_executor
        self._open_preview()
        original_control = self.overlay.report_metadata.control_number
        QTest.mouseClick(self.overlay.print_button, Qt.MouseButton.LeftButton)
        self.app.processEvents()

        original = self.window.print_store.list()[0]
        self.assertEqual(original["status"], "submitted")
        self.assertIn("dashboard_snapshot", original)
        self.assertTrue(self.window.snapshot_store.verify(original["dashboard_snapshot"]))

        reprint_executor = _Executor()
        self.overlay._print_executor = reprint_executor
        self.window.open_dashboard_reprint(original)
        self.app.processEvents()
        self.assertTrue(self.overlay.isVisible())
        self.assertTrue(self.overlay._reprint_mode)
        self.assertEqual(self.overlay.report_metadata.control_number, original_control)
        QTest.mouseClick(self.overlay.print_button, Qt.MouseButton.LeftButton)
        self.app.processEvents()

        records = self.window.print_store.list()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["control_number"], original_control)
        self.assertEqual(len(records[0].get("reprint_attempts", [])), 1)
        self.assertEqual(records[0]["reprint_attempts"][0]["outcome"], "submitted")

        self._open_preview()
        self.assertEqual(
            self.overlay.report_metadata.control_number,
            "CSMS-PRN-2026-07-0002",
        )


class PrintReportDecorationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_code39_identity_and_footer_repeat_on_every_pdf_page(self) -> None:
        generated = datetime(
            2026,
            7,
            18,
            9,
            15,
            tzinfo=timezone(timedelta(hours=8)),
        )
        metadata = PrintReportMetadata(
            "CSMS-PRN-2026-07-0001",
            generated,
        )
        modules = code39_modules(metadata.control_number)
        symbol_count = len(metadata.control_number) + 2
        self.assertEqual(len(modules), symbol_count * 15 + symbol_count - 1)
        self.assertTrue(modules[0])
        self.assertTrue(modules[-1])
        self.assertIn(False, modules)
        with self.assertRaisesRegex(ValueError, "unsupported Code 39"):
            code39_modules("CSMS_PRINT_0001")

        image = QImage(
            1200,
            5200,
            QImage.Format.Format_ARGB32_Premultiplied,
        )
        image.fill(QColor("#F3F8FB"))
        snapshot = DashboardSnapshot(
            image,
            600,
            2600,
            2.0,
            {"report_header": QRectF(14, 12, 572, 116)},
            report_metadata=metadata,
        )
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "decorated-dashboard.pdf"
            printer = QPrinter(QPrinter.PrinterMode.HighResolution)
            printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
            printer.setOutputFileName(str(output))
            printer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
            printer.setResolution(150)
            with patch.object(
                DashboardPrintRenderer,
                "_paint_footer",
            ) as footer:
                page_count = DashboardPrintRenderer.paint(
                    printer,
                    snapshot,
                    scale_mode="fit_width",
                )

            self.assertGreater(page_count, 1)
            self.assertEqual(footer.call_count, page_count)
            self.assertEqual(
                [call.args[3] for call in footer.call_args_list],
                list(range(1, page_count + 1)),
            )
            self.assertTrue(
                all(call.args[2] is metadata for call in footer.call_args_list)
            )
            self.assertTrue(
                all(call.args[4] == page_count for call in footer.call_args_list)
            )
            self.assertTrue(output.exists())
            self.assertGreater(output.stat().st_size, 100)


if __name__ == "__main__":
    unittest.main()
