from __future__ import annotations

from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class DashboardPrintLayoutContractTests(unittest.TestCase):
    def test_half_centimeter_margin_contract(self) -> None:
        source = (ROOT / "school_csm_control_center/ui/dashboard_print_overlay.py").read_text(encoding="utf-8")
        self.assertIn("MINIMUM_PRINT_MARGIN_MM = 5.0", source)
        self.assertIn("spin.setRange(MINIMUM_PRINT_MARGIN_MM, 50.0)", source)
        self.assertIn("max(MINIMUM_PRINT_MARGIN_MM", source)

    def test_white_canvas_and_section_fill_contract(self) -> None:
        source = (ROOT / "school_csm_control_center/ui/dashboard_printing.py").read_text(encoding="utf-8")
        self.assertIn('PRINT_PAGE_BACKGROUND = QColor("#FFFFFF")', source)
        self.assertIn('body.setStyleSheet("QWidget#dashboard_body { background: transparent; }")', source)
        self.assertIn("body.setStyleSheet(body_print_stylesheet)", source)
        self.assertNotIn("image.fill(QColor(theme.WINDOW_BG))", source)

    def test_footer_and_printer_origin_contract(self) -> None:
        source = (ROOT / "school_csm_control_center/ui/dashboard_printing.py").read_text(encoding="utf-8")
        self.assertIn("def _printer_printable_rect", source)
        self.assertIn("return QRectF(0.0, 0.0", source)
        self.assertIn("FOOTER_HEIGHT_MM = 22.0", source)
        self.assertIn("metadata.deped_logo_path", source)
        self.assertIn("metadata.mosslab_logo_path", source)
        self.assertIn("metadata.mosslab_seal_path", source)
        self.assertIn("TextSingleLine", source)

    def test_uniform_horizontal_margin_contract(self) -> None:
        source = (ROOT / "school_csm_control_center/ui/dashboard_print_overlay.py").read_text(encoding="utf-8")
        self.assertIn("horizontal_margin = max(", source)
        self.assertIn("float(minimums.left())", source)
        self.assertIn("float(minimums.right())", source)
        self.assertIn("horizontal_margin,\n            top_margin,\n            horizontal_margin", source)

    def test_philippine_standard_time_contract(self) -> None:
        source = (ROOT / "school_csm_control_center/ui/dashboard_printing.py").read_text(encoding="utf-8")
        self.assertIn('PHILIPPINE_STANDARD_TIME = timezone(timedelta(hours=8), name="PHT")', source)
        self.assertIn('strftime("%d %B %Y, %I:%M:%S %p PHT")', source)


if __name__ == "__main__":
    unittest.main()
