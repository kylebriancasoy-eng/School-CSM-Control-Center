from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from school_csm_control_center.web_server.scanner_engine import process_mrs_image


ROOT = Path(__file__).parents[1]
PREVIEW = ROOT / "assets" / "mrs_v0.4" / "CSM_MRS_Form_Preview_EN_v0.4.png"


class MRSV04RecognitionTests(unittest.TestCase):
    def test_uploaded_preview_template_barcode_and_date(self) -> None:
        with TemporaryDirectory() as temporary:
            result = process_mrs_image(PREVIEW, Path(temporary), ROOT)
        self.assertEqual(result["template_id"], "CSM-MRS-A4-2026-04-EN")
        self.assertEqual(result["marker_ids"], [10, 11, 12, 13])
        self.assertEqual(result["barcode"]["value"], "CSM-MRS-123627-2026-07-0001")
        self.assertEqual(result["barcode"]["status"], "Normal")
        self.assertEqual(result["date_recognition"]["value"], "2026-07-16")
        self.assertTrue(result["date_recognition"]["requires_operator_review"])


if __name__ == "__main__":
    unittest.main()
