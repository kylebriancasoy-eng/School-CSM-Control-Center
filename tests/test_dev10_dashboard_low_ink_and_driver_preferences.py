from __future__ import annotations

from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class DashboardLowInkAndDriverPreferencesContractTests(unittest.TestCase):
    def test_overlay_exposes_system_preferences_and_low_ink_mode(self) -> None:
        source = (ROOT / "school_csm_control_center/ui/dashboard_print_overlay.py").read_text(encoding="utf-8")
        for token in (
            "Open selected printer's Windows preferences",
            "printui.dll,PrintUIEntry",
            "Use Windows driver preferences",
            "Low-ink light",
            "appearance_mode",
            "use_windows_driver_preferences",
            "QPrinter(QPrinter.PrinterMode.HighResolution)",
        ):
            self.assertIn(token, source)

    def test_renderer_has_non_destructive_low_ink_transform(self) -> None:
        source = (ROOT / "school_csm_control_center/ui/dashboard_printing.py").read_text(encoding="utf-8")
        self.assertIn("def apply_ink_mode", source)
        self.assertIn('normalized not in {"low_ink", "light", "light_ink"}', source)
        self.assertIn("local_mean", source)
        self.assertIn("dark_detail", source)
        self.assertIn("snapshot.report_metadata", source)


if __name__ == "__main__":
    unittest.main()
