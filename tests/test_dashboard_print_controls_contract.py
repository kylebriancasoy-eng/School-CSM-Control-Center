from __future__ import annotations

from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class DashboardPrintControlsContractTests(unittest.TestCase):
    def test_interactive_buttons_are_excluded_and_restored(self) -> None:
        source = (ROOT / "school_csm_control_center/ui/dashboard_printing.py").read_text(encoding="utf-8")
        self.assertIn("QAbstractButton", source)
        self.assertIn("board.findChildren(QAbstractButton)", source)
        self.assertIn("button.setHidden(True)", source)
        self.assertIn("button.setHidden(was_hidden)", source)


if __name__ == "__main__":
    unittest.main()
