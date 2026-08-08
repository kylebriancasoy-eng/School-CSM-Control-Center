from __future__ import annotations

from pathlib import Path
import unittest

from school_csm_control_center.mrs_diagnostics import run_mrs_software_self_check


ROOT = Path(__file__).parents[1]


class MRSMultilingualAndFieldTestTests(unittest.TestCase):
    def test_scanner_remote_offers_all_languages(self) -> None:
        html = (ROOT / "school_csm_control_center" / "web_server" / "static" / "scanner_remote.html").read_text(encoding="utf-8")
        self.assertIn('value="en">English', html)
        self.assertIn('value="fil">Filipino', html)
        self.assertIn('value="war">Waray-Waray', html)

    def test_field_test_assets_are_packaged(self) -> None:
        self.assertTrue((ROOT / "RUN_MRS_FIELD_TEST_CHECK.cmd").is_file())
        self.assertTrue((ROOT / "MRS_FIELD_TEST_GUIDE.md").is_file())
        self.assertTrue((ROOT / "run_mrs_field_test_check.py").is_file())

    def test_software_self_check_verifies_language_round_trips(self) -> None:
        result = run_mrs_software_self_check(ROOT)
        failures = [row for row in result["checks"] if not row["passed"] and row["name"] != "Connected physical printers"]
        self.assertEqual(failures, [])
        names = {row["name"] for row in result["checks"]}
        for language in ("English", "Filipino", "Waray-Waray"):
            self.assertIn(f"{language} Code 128 round trip", names)


if __name__ == "__main__":
    unittest.main()
