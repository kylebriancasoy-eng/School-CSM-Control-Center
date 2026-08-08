from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from school_csm_control_center.ui.history_board import HistoryBoard
from school_csm_control_center.ui.widgets import TooltipIconButton
from tests.test_analysis_service import sample_records


def print_records() -> list[dict]:
    return [
        {
            "id": "print-two",
            "control_number": "CSMS-PRN-2026-07-0002",
            "generated_at": "2026-07-18T10:30:00+08:00",
            "printed_at": "2026-07-18T10:31:00+08:00",
            "scope": "10 of 15 responses · Onsite",
            "page_count": 2,
            "printer_name": "Records Office Printer",
            "copies": 1,
            "selected_pages": [1, 2],
            "survey_count": 10,
            "barcode_value": "CSMS-PRN-2026-07-0002",
            "settings": {
                "paper_size": "A4",
                "orientation": "landscape",
                "resolution_dpi": 600,
            },
        },
        {
            "id": "print-one",
            "control_number": "CSMS-PRN-2026-07-0001",
            "generated_at": "2026-07-17T09:00:00+08:00",
            "printed_at": "2026-07-17T09:01:00+08:00",
            "scope": "15 of 15 responses · All dates",
            "page_count": 3,
            "printer_name": "DepEd Office Printer",
            "barcode_value": "CSMS-PRN-2026-07-0001",
            "settings": {
                "paper_size": "Letter",
                "orientation": "portrait",
            },
        },
    ]


class HistoryPrintsUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.board = HistoryBoard()
        self.board.resize(1200, 700)
        self.board.refresh(sample_records())
        self.board.refresh_prints(print_records())
        self.board.show()
        self.app.processEvents()

    def tearDown(self) -> None:
        self.board.close()
        self.board.deleteLater()
        self.app.processEvents()

    def test_icon_toggles_switch_sections_and_keep_survey_actions_safe(self) -> None:
        self.assertEqual(self.board.current_section, "surveys")
        self.assertTrue(self.board.table.isVisible())
        for button in (
            self.board.survey_results_button,
            self.board.prints_button,
        ):
            self.assertIsInstance(button, TooltipIconButton)
            self.assertEqual(button.text(), "")
            self.assertFalse(button.icon().isNull())
            self.assertTrue(button.toolTip())

        QTest.mouseClick(self.board.prints_button, Qt.MouseButton.LeftButton)
        self.app.processEvents()
        self.assertEqual(self.board.current_section, "prints")
        self.assertTrue(self.board.prints_table.isVisible())
        self.assertFalse(self.board.table.isVisible())
        self.assertFalse(self.board.edit_button.isVisible())
        self.assertFalse(self.board.delete_button.isVisible())
        self.assertFalse(self.board.export_button.isVisible())
        self.assertIn("Dashboard print", self.board.view_button.toolTip())

        survey_edit: list[object] = []
        self.board.edit_requested.connect(survey_edit.append)
        self.board.prints_table.selectRow(0)
        self.app.processEvents()
        self.assertTrue(self.board.view_button.isEnabled())
        self.board._emit_edit()
        self.assertEqual(survey_edit, [])

        QTest.mouseClick(
            self.board.survey_results_button, Qt.MouseButton.LeftButton
        )
        self.app.processEvents()
        self.assertEqual(self.board.current_section, "surveys")
        self.assertTrue(self.board.edit_button.isVisible())
        self.assertTrue(self.board.export_button.isVisible())

    def test_prints_show_barcode_identity_and_emit_print_details_only(self) -> None:
        self.board.show_section("prints")
        self.app.processEvents()
        self.assertEqual(self.board.prints_table.rowCount(), 2)
        self.assertEqual(
            self.board.prints_table.horizontalHeaderItem(7).text(), "Barcode"
        )
        barcode_item = self.board.prints_table.item(0, 7)
        self.assertTrue(barcode_item.text().startswith("CSMS-PRN-2026-07-"))
        self.assertFalse(barcode_item.icon().isNull())
        self.assertIn("Copies:", barcode_item.toolTip())

        survey_views: list[object] = []
        print_views: list[object] = []
        self.board.view_requested.connect(survey_views.append)
        self.board.print_view_requested.connect(print_views.append)
        self.board.prints_table.selectRow(0)
        self.app.processEvents()
        selected = self.board.selected_print_record()
        self.assertIsNotNone(selected)
        QTest.mouseClick(self.board.view_button, Qt.MouseButton.LeftButton)
        self.app.processEvents()
        self.assertEqual(survey_views, [])
        self.assertEqual(print_views, [selected])
        self.assertIsNone(self.board.selected_record())

    def test_print_search_is_independent_from_survey_filters(self) -> None:
        self.board.search_input.setText("government")
        survey_count = self.board.table.rowCount()
        self.board.show_section("prints")
        self.board.print_search_input.setText("Records Office")
        self.app.processEvents()
        self.assertEqual(self.board.prints_table.rowCount(), 1)
        self.assertEqual(
            self.board.prints_table.item(0, 4).text(),
            "Records Office Printer",
        )
        self.board.show_section("surveys")
        self.app.processEvents()
        self.assertEqual(self.board.search_input.text(), "government")
        self.assertEqual(self.board.table.rowCount(), survey_count)


if __name__ == "__main__":
    unittest.main()
