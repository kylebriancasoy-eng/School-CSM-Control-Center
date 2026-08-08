from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import os
from pathlib import Path
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRect, QRectF, QSize, QTemporaryDir
from PySide6.QtGui import QColor, QImage, QPageSize
from PySide6.QtPrintSupport import QPrinter
from PySide6.QtWidgets import QApplication

from school_csm_control_center.ui.dashboard_board import DashboardBoard
from school_csm_control_center.ui.dashboard_printing import (
    DashboardPrintRenderer,
    DashboardSnapshot,
    PrintReportMetadata,
    code39_modules,
)
from tests.test_analysis_service import sample_records


def many_service_records() -> list[dict]:
    templates = sample_records()
    records: list[dict] = []
    for index in range(12):
        record = deepcopy(templates[index % len(templates)])
        record["id"] = f"print-{index}"
        record["control_number"] = f"P-{index:03d}"
        record["survey_date"] = f"2026-07-{index + 1:02d}"
        record["meta"]["service_availed"] = f"Printable Service {index + 1}"
        record["feedback"] = {"comments": f"Printable feedback item {index + 1}."}
        records.append(record)
    return records


class DashboardPrintingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.board = DashboardBoard()
        self.board.resize(1200, 700)
        self.board.show()
        self.board.refresh(many_service_records())
        self.app.processEvents()

    def tearDown(self) -> None:
        self.board.close()
        self.board.deleteLater()
        self.app.processEvents()

    def test_capture_includes_full_nested_content_and_restores_live_dashboard(self) -> None:
        self.assertGreater(self.board.services_table.rowCount(), 6)
        self.board.scroll.verticalScrollBar().setValue(180)
        self.board.services_table.verticalScrollBar().setValue(40)
        self.board.insights_scroll.verticalScrollBar().setValue(30)
        self.app.processEvents()
        before = {
            "board_size": self.board.size(),
            "board_min_width": self.board.minimumWidth(),
            "board_max_width": self.board.maximumWidth(),
            "body_size": self.board.body.size(),
            "body_min": self.board.body.minimumHeight(),
            "body_max": self.board.body.maximumHeight(),
            "services_min": self.board.services_table.minimumHeight(),
            "services_max": self.board.services_table.maximumHeight(),
            "insights_min": self.board.insights_scroll.minimumHeight(),
            "insights_max": self.board.insights_scroll.maximumHeight(),
            "outer_scroll": self.board.scroll.verticalScrollBar().value(),
            "services_scroll": self.board.services_table.verticalScrollBar().value(),
            "insights_scroll": self.board.insights_scroll.verticalScrollBar().value(),
        }

        snapshot = DashboardPrintRenderer.capture(self.board, render_scale=2.0)

        self.assertFalse(snapshot.image.isNull())
        self.assertTrue({"header", "filters", "body"}.issubset(snapshot.component_bounds))
        self.assertEqual(
            len([name for name in snapshot.component_bounds if name.startswith("metric_")]),
            4,
        )
        self.assertEqual(
            len([name for name in snapshot.component_bounds if name.startswith("section_")]),
            9,
        )
        self.assertGreater(snapshot.logical_height, self.board.scroll.viewport().height())
        self.assertGreater(snapshot.component_bounds["body"].height(), before["body_size"].height())
        self.assertEqual(snapshot.image.width(), snapshot.logical_width * 2)
        self.assertEqual(snapshot.image.height(), snapshot.logical_height * 2)
        self.assertLessEqual(snapshot.component_bounds["body"].bottom(), snapshot.logical_height)

        self.assertEqual(self.board.size(), before["board_size"])
        self.assertEqual(self.board.minimumWidth(), before["board_min_width"])
        self.assertEqual(self.board.maximumWidth(), before["board_max_width"])
        self.assertEqual(self.board.body.size(), before["body_size"])
        self.assertEqual(self.board.body.minimumHeight(), before["body_min"])
        self.assertEqual(self.board.body.maximumHeight(), before["body_max"])
        self.assertEqual(self.board.services_table.minimumHeight(), before["services_min"])
        self.assertEqual(self.board.services_table.maximumHeight(), before["services_max"])
        self.assertEqual(self.board.insights_scroll.minimumHeight(), before["insights_min"])
        self.assertEqual(self.board.insights_scroll.maximumHeight(), before["insights_max"])
        self.assertEqual(self.board.scroll.verticalScrollBar().value(), before["outer_scroll"])
        self.assertEqual(self.board.services_table.verticalScrollBar().value(), before["services_scroll"])
        self.assertEqual(self.board.insights_scroll.verticalScrollBar().value(), before["insights_scroll"])

    def test_page_plan_covers_source_exactly_and_fit_page_uses_one_page(self) -> None:
        source = QSize(1200, 3100)
        printable = QRect(25, 35, 1000, 1400)
        pages = DashboardPrintRenderer.build_page_plan(source, printable, scale_mode="fit_width")
        self.assertGreater(len(pages), 1)
        self.assertEqual(pages[0].source_rect.top(), 0)
        for previous, current in zip(pages, pages[1:]):
            self.assertAlmostEqual(previous.source_rect.bottom(), current.source_rect.top())
            self.assertGreater(current.source_rect.height(), 0)
        self.assertAlmostEqual(pages[-1].source_rect.bottom(), source.height())
        self.assertTrue(all(page.target_rect.left() >= printable.left() for page in pages))
        printable_float = QRectF(printable)
        self.assertTrue(all(page.target_rect.right() <= printable_float.right() + 0.01 for page in pages))

        fitted = DashboardPrintRenderer.build_page_plan(source, printable, scale_mode="fit_page")
        self.assertEqual(len(fitted), 1)
        self.assertEqual(fitted[0].source_rect, QRectF(0, 0, source.width(), source.height()))
        self.assertLessEqual(fitted[0].target_rect.width(), printable.width())
        self.assertLessEqual(fitted[0].target_rect.height(), printable.height())

    def test_vertical_report_metadata_barcode_and_section_aware_pages(self) -> None:
        metadata = PrintReportMetadata(
            "CSMS-PRN-2026-07-0001",
            datetime(2026, 7, 18, 9, 15, tzinfo=timezone.utc),
        )
        before_kpis = [
            self.board.kpi_grid.getItemPosition(self.board.kpi_grid.indexOf(widget))
            for widget in self.board.metric_cards
        ]
        before_sections = [
            self.board.analysis_grid.getItemPosition(self.board.analysis_grid.indexOf(widget))
            for widget in self.board.analysis_widgets
        ]

        snapshot = DashboardPrintRenderer.capture(
            self.board,
            render_scale=2.0,
            metadata=metadata,
        )

        self.assertIs(snapshot.report_metadata, metadata)
        self.assertIn("report_header", snapshot.component_bounds)
        metric_bounds = [snapshot.component_bounds[f"metric_{index}"] for index in range(1, 5)]
        section_bounds = [snapshot.component_bounds[f"section_{index}"] for index in range(1, 10)]
        self.assertEqual(metric_bounds, sorted(metric_bounds, key=lambda rect: rect.top()))
        self.assertEqual(section_bounds, sorted(section_bounds, key=lambda rect: rect.top()))
        self.assertTrue(
            all(first.bottom() <= second.top() for first, second in zip(metric_bounds, metric_bounds[1:]))
        )
        self.assertTrue(
            all(first.bottom() <= second.top() for first, second in zip(section_bounds, section_bounds[1:]))
        )
        self.assertEqual(
            before_kpis,
            [
                self.board.kpi_grid.getItemPosition(self.board.kpi_grid.indexOf(widget))
                for widget in self.board.metric_cards
            ],
        )
        self.assertEqual(
            before_sections,
            [
                self.board.analysis_grid.getItemPosition(self.board.analysis_grid.indexOf(widget))
                for widget in self.board.analysis_widgets
            ],
        )
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
        printer.setResolution(300)
        self.assertGreaterEqual(
            len(DashboardPrintRenderer.printer_page_plan(printer, snapshot)),
            2,
        )
        modules = code39_modules(metadata.control_number)
        self.assertGreater(len(modules), 100)
        self.assertIn(True, modules)
        self.assertIn(False, modules)
        with self.assertRaises(ValueError):
            code39_modules("INVALID_123")

        boundary_plan = DashboardPrintRenderer.build_page_plan(
            QSize(100, 600),
            QRect(0, 0, 100, 250),
            break_regions=[QRectF(0, 200, 100, 100)],
        )
        self.assertEqual(boundary_plan[0].source_rect.bottom(), 200)

    def test_renderer_can_write_paginated_pdf_without_a_physical_printer(self) -> None:
        temporary = QTemporaryDir()
        self.assertTrue(temporary.isValid())
        output = Path(temporary.path()) / "dashboard-print-test.pdf"
        image = QImage(1200, 3100, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QColor("#0A1D30"))
        metadata = PrintReportMetadata(
            "CSMS-PRN-2026-07-0001",
            datetime(2026, 7, 18, 9, 15, tzinfo=timezone.utc),
        )
        snapshot = DashboardSnapshot(image, 600, 1550, 2.0, {}, metadata)
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
        printer.setOutputFileName(str(output))
        printer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
        printer.setResolution(300)

        page_count = DashboardPrintRenderer.paint(printer, snapshot, scale_mode="fit_width")

        self.assertGreaterEqual(page_count, 1)
        self.assertTrue(output.exists())
        self.assertGreater(output.stat().st_size, 100)


if __name__ == "__main__":
    unittest.main()
