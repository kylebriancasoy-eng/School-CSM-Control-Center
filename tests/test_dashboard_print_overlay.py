from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QKeySequence, QPageLayout, QPageSize
from PySide6.QtPrintSupport import QPrinter, QPrintPreviewWidget
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QVBoxLayout, QWidget

from school_csm_control_center.ui.dashboard_board import DashboardBoard
from school_csm_control_center.ui.dashboard_print_overlay import DashboardPrintOverlay
from school_csm_control_center.ui.dashboard_printing import (
    DashboardPrintRenderer,
    DashboardSnapshot,
    PrintPage,
    PrintReportMetadata,
)
from school_csm_control_center.ui.widgets import TooltipIconButton
from tests.test_analysis_service import sample_records


class _FakePrinterInfo:
    """Small capability object matching the QPrinterInfo API used by the overlay."""

    def __init__(self, name: str = "CSMS Test Printer") -> None:
        self._name = name
        self._a4 = QPageSize(QPageSize.PageSizeId.A4)
        self._letter = QPageSize(QPageSize.PageSizeId.Letter)

    def printerName(self) -> str:
        return self._name

    def supportedPageSizes(self) -> list[QPageSize]:
        return [self._a4, self._letter]

    def defaultPageSize(self) -> QPageSize:
        return self._a4

    def supportedResolutions(self) -> list[int]:
        return [300, 600]

    def supportedColorModes(self) -> list[QPrinter.ColorMode]:
        return [QPrinter.ColorMode.Color, QPrinter.ColorMode.GrayScale]

    def defaultColorMode(self) -> QPrinter.ColorMode:
        return QPrinter.ColorMode.Color

    def supportedDuplexModes(self) -> list[QPrinter.DuplexMode]:
        return [
            QPrinter.DuplexMode.DuplexNone,
            QPrinter.DuplexMode.DuplexLongSide,
        ]

    def defaultDuplexMode(self) -> QPrinter.DuplexMode:
        return QPrinter.DuplexMode.DuplexNone


class _RecordingExecutor:
    def __init__(
        self,
        *,
        failure: Exception | None = None,
        accepted_pages: int | None = None,
    ) -> None:
        self.failure = failure
        self.accepted_pages = accepted_pages
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
        return (
            self.accepted_pages
            if self.accepted_pages is not None
            else len(options.get("selected_pages", []))
        )


class DashboardPrintOverlayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.host = QWidget()
        self.host.resize(1200, 720)
        host_layout = QVBoxLayout(self.host)
        host_layout.setContentsMargins(0, 0, 0, 0)
        self.dashboard = DashboardBoard()
        host_layout.addWidget(self.dashboard)
        self.dashboard.refresh(sample_records())
        self.host.show()
        self.app.processEvents()

        image = QImage(1200, 3000, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QColor("#07192A"))
        self.snapshot = DashboardSnapshot(
            image=image,
            logical_width=600,
            logical_height=1500,
            render_scale=2.0,
            component_bounds={
                "header": QRectF(14, 12, 572, 64),
                "filters": QRectF(14, 85, 572, 54),
                "body": QRectF(14, 148, 572, 1340),
            },
        )
        self.plan = [
            PrintPage(1, QRectF(0, 0, 1200, 1000), QRectF(0, 0, 900, 750)),
            PrintPage(2, QRectF(0, 1000, 1200, 1000), QRectF(0, 0, 900, 750)),
            PrintPage(3, QRectF(0, 2000, 1200, 1000), QRectF(0, 0, 900, 750)),
        ]
        self.capture_patcher = patch.object(
            DashboardPrintRenderer,
            "capture",
            return_value=self.snapshot,
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
        self.overlays: list[DashboardPrintOverlay] = []

    def tearDown(self) -> None:
        for overlay in self.overlays:
            overlay.close_overlay()
            overlay.deleteLater()
        self.host.close()
        self.host.deleteLater()
        self.app.processEvents()
        self.plan_patcher.stop()
        self.paint_patcher.stop()
        self.capture_patcher.stop()

    def _overlay(
        self,
        *,
        printers: list[_FakePrinterInfo] | None = None,
        executor: _RecordingExecutor | None = None,
    ) -> DashboardPrintOverlay:
        overlay = DashboardPrintOverlay(
            self.dashboard,
            self.host,
            printer_provider=lambda: list(printers or []),
            print_executor=executor or _RecordingExecutor(),
        )
        self.overlays.append(overlay)
        return overlay

    def _assert_no_dialogs(self) -> None:
        self.assertEqual(self.host.findChildren(QDialog), [])
        self.assertEqual(
            [widget for widget in QApplication.topLevelWidgets() if isinstance(widget, QDialog)],
            [],
        )

    def test_dashboard_print_control_is_icon_only_accessible_and_emits(self) -> None:
        button = self.dashboard.print_button
        self.assertIsInstance(button, TooltipIconButton)
        self.assertEqual(button.text(), "")
        self.assertFalse(button.icon().isNull())
        self.assertEqual(button.toolTip(), "Print complete Dashboard")
        self.assertEqual(button.accessibleName(), "Print complete Dashboard")
        self.assertEqual(
            button.shortcut().toString(QKeySequence.SequenceFormat.PortableText),
            "Ctrl+P",
        )

        requests: list[bool] = []
        self.dashboard.print_requested.connect(lambda: requests.append(True))
        QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        self.app.processEvents()
        self.assertEqual(requests, [True])

    def test_no_printer_still_shows_complete_child_preview_without_dialogs(self) -> None:
        overlay = self._overlay(printers=[])
        overlay.open_overlay()
        self.app.processEvents()

        self.assertTrue(overlay.isVisible())
        self.assertIs(overlay.parentWidget(), self.host)
        self.assertEqual(overlay.geometry(), self.host.rect())
        self.assertIsInstance(overlay.preview, QPrintPreviewWidget)
        self.assertTrue(overlay.preview.isVisible())
        self.assertIs(overlay._snapshot, self.snapshot)
        self.assertFalse(overlay.printer_combo.isEnabled())
        self.assertEqual(overlay.printer_combo.itemText(0), "No printers detected")
        self.assertFalse(overlay.print_button.isEnabled())
        self.assertIn("No physical printer is available", overlay.printer_status.text())
        self.assertIn("preview remains available", overlay.printer_status.text())
        self.assertIn("2 of 2 responses", overlay.scope_label.text())
        self.assertEqual(overlay.page_label.text(), "Page 1 of 3")
        self.capture_mock.assert_called_once()
        capture_args, capture_kwargs = self.capture_mock.call_args
        self.assertEqual(capture_args, (self.dashboard,))
        self.assertEqual(capture_kwargs["render_scale"], 2.0)
        self.assertIsInstance(capture_kwargs["metadata"], PrintReportMetadata)

        buttons = overlay.findChildren(TooltipIconButton)
        self.assertGreaterEqual(len(buttons), 9)
        for button in buttons:
            with self.subTest(button=button.toolTip()):
                self.assertEqual(button.text(), "")
                self.assertFalse(button.icon().isNull())
                self.assertTrue(button.toolTip().strip())
                self.assertTrue(button.accessibleName().strip())
        self._assert_no_dialogs()

    def test_advanced_printer_preferences_are_embedded_and_apply(self) -> None:
        overlay = self._overlay(printers=[_FakePrinterInfo()])
        overlay.open_overlay()
        self.app.processEvents()

        self.assertFalse(overlay.advanced_body.isVisible())
        QTest.mouseClick(overlay.advanced_button, Qt.MouseButton.LeftButton)
        self.app.processEvents()
        self.assertTrue(overlay.advanced_body.isVisible())
        self.assertIn("Hide advanced", overlay.advanced_button.toolTip())
        self.assertEqual(overlay.paper_combo.count(), 2)
        self.assertEqual(
            [overlay.resolution_combo.itemData(index) for index in range(overlay.resolution_combo.count())],
            [300, 600],
        )
        self.assertTrue(overlay.color_combo.isEnabled())
        self.assertTrue(overlay.duplex_combo.isEnabled())
        self.assertIn("duplex available", overlay.capability_label.text())

        overlay.copies_spin.setValue(3)
        overlay.orientation_combo.setCurrentIndex(
            overlay.orientation_combo.findData("landscape")
        )
        overlay.color_combo.setCurrentIndex(
            overlay.color_combo.findData(int(QPrinter.ColorMode.GrayScale.value))
        )
        overlay.duplex_combo.setCurrentIndex(
            overlay.duplex_combo.findData(int(QPrinter.DuplexMode.DuplexLongSide.value))
        )
        overlay.resolution_combo.setCurrentIndex(overlay.resolution_combo.findData(600))
        overlay.scale_mode_combo.setCurrentIndex(
            overlay.scale_mode_combo.findData("custom")
        )
        overlay.width_percent_spin.setValue(80)
        for key, value in {
            "left": 10.0,
            "top": 11.0,
            "right": 12.0,
            "bottom": 13.0,
        }.items():
            overlay.margin_spins[key].setValue(value)
        self.app.processEvents()

        self.assertTrue(overlay.width_percent_spin.isVisible())
        self.assertEqual(overlay.printer.copyCount(), 3)
        self.assertEqual(
            overlay.printer.pageLayout().orientation(),
            QPageLayout.Orientation.Landscape,
        )
        self.assertEqual(overlay.printer.colorMode(), QPrinter.ColorMode.GrayScale)
        # A synthetic printer has no Windows driver to accept duplex state, so
        # verify the embedded preference itself rather than a driver round-trip.
        self.assertEqual(
            overlay.duplex_combo.currentData(),
            int(QPrinter.DuplexMode.DuplexLongSide.value),
        )
        self.assertEqual(overlay.printer.resolution(), 600)
        margins = overlay.printer.pageLayout().margins(QPageLayout.Unit.Millimeter)
        # Horizontal margins are intentionally synchronized so the report
        # stays centered even when a driver reports asymmetric printable
        # bounds. The larger requested side wins.
        self.assertAlmostEqual(margins.left(), 12.0, delta=0.2)
        self.assertAlmostEqual(margins.top(), 11.0, delta=0.2)
        self.assertAlmostEqual(margins.right(), 12.0, delta=0.2)
        self.assertAlmostEqual(margins.bottom(), 13.0, delta=0.2)
        self.assertAlmostEqual(overlay.margin_spins["left"].value(), 12.0, delta=0.2)
        self._assert_no_dialogs()

        QTest.mouseClick(overlay.advanced_button, Qt.MouseButton.LeftButton)
        self.app.processEvents()
        self.assertFalse(overlay.advanced_body.isVisible())
        self.assertIn("Show advanced", overlay.advanced_button.toolTip())

    def test_injected_executor_receives_selected_pages_without_native_spooling(self) -> None:
        executor = _RecordingExecutor()
        overlay = self._overlay(
            printers=[_FakePrinterInfo("Injected Test Printer")],
            executor=executor,
        )
        completed: list[str] = []
        overlay.print_completed.connect(completed.append)
        overlay.open_overlay()
        self.app.processEvents()

        overlay.page_range_combo.setCurrentIndex(
            overlay.page_range_combo.findData("custom")
        )
        overlay.from_page_spin.setValue(2)
        overlay.to_page_spin.setValue(3)
        overlay.scale_mode_combo.setCurrentIndex(
            overlay.scale_mode_combo.findData("custom")
        )
        overlay.width_percent_spin.setValue(75)
        self.app.processEvents()
        QTest.mouseClick(overlay.print_button, Qt.MouseButton.LeftButton)
        self.app.processEvents()

        self.assertEqual(len(executor.calls), 1)
        printer, snapshot, options = executor.calls[0]
        self.assertIs(printer, overlay.printer)
        self.assertIs(snapshot, self.snapshot)
        self.assertEqual(options["selected_pages"], [2, 3])
        self.assertEqual(options["scale_mode"], "custom")
        self.assertEqual(options["width_percent"], 75)
        self.assertEqual(len(completed), 1)
        self.assertIn("Injected Test Printer", completed[0])
        self.assertIn("2 pages", completed[0])
        self.assertFalse(overlay.isVisible())
        self._assert_no_dialogs()

    def test_executor_failure_is_reported_in_overlay_and_never_opens_dialog(self) -> None:
        executor = _RecordingExecutor(failure=RuntimeError("offline"))
        overlay = self._overlay(printers=[_FakePrinterInfo()], executor=executor)
        failures: list[str] = []
        overlay.print_failed.connect(failures.append)
        overlay.open_overlay()
        self.app.processEvents()

        QTest.mouseClick(overlay.print_button, Qt.MouseButton.LeftButton)
        self.app.processEvents()

        self.assertEqual(len(executor.calls), 1)
        self.assertTrue(overlay.isVisible())
        self.assertEqual(len(failures), 1)
        self.assertIn("offline", failures[0])
        self.assertIn("Dashboard printing failed", overlay.status_label.text())
        self._assert_no_dialogs()

    def test_partial_output_is_not_reported_as_a_successful_dashboard_print(self) -> None:
        executor = _RecordingExecutor(accepted_pages=1)
        overlay = self._overlay(printers=[_FakePrinterInfo()], executor=executor)
        completed: list[str] = []
        failures: list[str] = []
        overlay.print_completed.connect(completed.append)
        overlay.print_failed.connect(failures.append)
        overlay.open_overlay()
        self.app.processEvents()

        QTest.mouseClick(overlay.print_button, Qt.MouseButton.LeftButton)
        self.app.processEvents()

        self.assertEqual(len(executor.calls), 1)
        self.assertEqual(completed, [])
        self.assertEqual(len(failures), 1)
        self.assertIn("did not accept all 3 selected pages", failures[0])
        self.assertTrue(overlay.isVisible())

    def test_close_button_and_escape_close_the_same_child_overlay(self) -> None:
        overlay = self._overlay(printers=[])
        overlay.open_overlay()
        self.app.processEvents()
        QTest.mouseClick(overlay.close_button, Qt.MouseButton.LeftButton)
        self.app.processEvents()
        self.assertFalse(overlay.isVisible())

        overlay.open_overlay()
        overlay.activateWindow()
        overlay.close_button.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
        self.app.processEvents()
        QTest.keyClick(overlay.close_button, Qt.Key.Key_Escape)
        self.app.processEvents()
        self.assertFalse(overlay.isVisible())
        self._assert_no_dialogs()


if __name__ == "__main__":
    unittest.main()
