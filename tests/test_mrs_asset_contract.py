from __future__ import annotations

import hashlib
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).parents[1]
CANONICAL = ROOT / "assets" / "mrs_v0.4" / "CSM_MRS_Coordinate_Map_v0.4.json"
WEB_COPY = (
    ROOT
    / "school_csm_control_center"
    / "web_server"
    / "static"
    / "CSM_MRS_Coordinate_Map_v0.4.json"
)


class MRSAssetContractTests(unittest.TestCase):
    def test_packaged_coordinate_maps_are_identical_v042_copies(self) -> None:
        self.assertTrue(CANONICAL.is_file())
        self.assertTrue(WEB_COPY.is_file())
        canonical_bytes = CANONICAL.read_bytes()
        self.assertEqual(
            hashlib.sha256(canonical_bytes).hexdigest(),
            hashlib.sha256(WEB_COPY.read_bytes()).hexdigest(),
        )
        self.assertEqual(json.loads(canonical_bytes)["version"], "0.4.2")

    def test_scanner_and_printing_load_only_the_canonical_asset_directory(self) -> None:
        scanner = (
            ROOT
            / "school_csm_control_center"
            / "web_server"
            / "scanner_engine.py"
        ).read_text(encoding="utf-8")
        printing = (ROOT / "school_csm_control_center" / "mrs_printing.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('"assets"', scanner)
        self.assertIn('"mrs_v0.4"', scanner)
        self.assertNotIn('"web_server" / "static" / "CSM_MRS_Coordinate_Map', scanner)
        self.assertIn('"assets"', printing)
        self.assertIn('"mrs_v0.4"', printing)


if __name__ == "__main__":
    unittest.main()
