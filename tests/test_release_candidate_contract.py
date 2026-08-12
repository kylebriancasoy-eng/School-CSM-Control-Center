from __future__ import annotations

from pathlib import Path
import unittest

import school_csm_control_center


class ReleaseContractTests(unittest.TestCase):
    def test_package_version_is_v041(self) -> None:
        self.assertEqual(school_csm_control_center.__version__, "0.4.1")

    def test_manual_corner_editor_starts_without_marker_center_prefill(self) -> None:
        root = Path(__file__).parents[1]
        html = (root / "school_csm_control_center" / "web_server" / "static" / "scanner_remote.html").read_text(
            encoding="utf-8"
        )
        self.assertIn("cornerPoints=[];drawCorners();updateCornerMessage()", html)
        self.assertNotIn("const suggested=currentJob?.recognition?.source_corners", html)


if __name__ == "__main__":
    unittest.main()
