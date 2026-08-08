from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).parents[1]


class MRSUISourceContractTests(unittest.TestCase):
    def test_main_window_integrates_mrs_printing_board(self) -> None:
        source = (ROOT / "school_csm_control_center" / "ui" / "main_window.py").read_text("utf-8")
        self.assertIn("MRSPrintingBoard", source)
        self.assertIn("mrs_printing_board", source)

    def test_print_actions_use_icon_buttons_and_no_pdf_export_action(self) -> None:
        source = (ROOT / "school_csm_control_center" / "ui" / "mrs_printing_board.py").read_text("utf-8")
        self.assertIn("TooltipIconButton", source)
        for prohibited in ("Generate PDF", "Export PDF", "Save Printable Form", "Download Blank Form"):
            self.assertNotIn(prohibited, source)

    def test_post_print_overlay_is_scrollable_and_reachable(self) -> None:
        source = (ROOT / "school_csm_control_center" / "ui" / "mrs_printing_board.py").read_text("utf-8")
        self.assertIn("QScrollArea", source)
        self.assertIn("CONFIRM MRS PRINTING RESULTS", source)
        self.assertIn("Select all control numbers as failed", source)
        self.assertIn("None failed", source)
        self.assertIn("Return to failed-form selection", source)

    def test_mrs_board_scrolls_instead_of_compressing_cards(self) -> None:
        source = (ROOT / "school_csm_control_center" / "ui" / "mrs_printing_board.py").read_text("utf-8")
        self.assertIn('self.board_scroll.setWidgetResizable(True)', source)
        self.assertIn('ScrollBarAlwaysOff', source)
        self.assertIn('ScrollBarAsNeeded', source)
        self.assertIn('print_card.setMinimumHeight(325)', source)
        self.assertIn('reprint_card.setMinimumHeight(325)', source)

    def test_print_configuration_uses_non_overlapping_compact_grid(self) -> None:
        source = (ROOT / "school_csm_control_center" / "ui" / "mrs_printing_board.py").read_text("utf-8")
        self.assertIn('config.addWidget(self.language_combo, 1, 0)', source)
        self.assertIn('config.addWidget(self.count_spin, 1, 1)', source)
        self.assertIn('config.addWidget(self.template_label, 1, 2)', source)
        self.assertIn('config.addWidget(self.printer_combo, 3, 0, 1, 3)', source)
        self.assertIn('QLabel#mrs_form_label', source)


if __name__ == "__main__":
    unittest.main()
