from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class NarrativeHistoryContractTests(unittest.TestCase):
    def test_print_history_exposes_both_snapshot_actions(self) -> None:
        source = (ROOT / "school_csm_control_center" / "ui" / "history_board.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('"Actions"', source)
        self.assertIn("narrative_requested = Signal(object)", source)
        self.assertIn("reprint_requested = Signal(object)", source)
        self.assertIn('"Narrative Report"', source)
        self.assertIn('"Reprint Dashboard"', source)
        self.assertIn("PrintHistoryStore.narrative_eligibility", source)

    def test_reprint_path_never_reserves_a_new_control_number(self) -> None:
        source = (
            ROOT
            / "school_csm_control_center"
            / "ui"
            / "dashboard_print_overlay.py"
        ).read_text(encoding="utf-8")
        start = source.index("    def open_reprint(")
        end = source.index("    def close_overlay(", start)
        reprint_method = source[start:end]
        self.assertNotIn(".reserve(", reprint_method)
        self.assertIn("original control number", reprint_method)
        self.assertIn("record_reprint_attempt", source)


if __name__ == "__main__":
    unittest.main()
