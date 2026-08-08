from __future__ import annotations

import base64
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from PIL import Image

from school_csm_control_center.mrs_printing import (
    BARCODE_CONTROL_TEXT_Y_PX,
    BARCODE_LEGACY_REGION_PX,
    BARCODE_PRINT_REGION_PX,
    render_official_mrs_form,
)
from school_csm_control_center.web_server.scanner_engine import process_mrs_image
from school_csm_control_center.web_server.scanner_jobs import ScannerJobManager


ROOT = Path(__file__).parents[1]
CONTROL = "CSM-MRS-123627-2026-07-0001"


class ScannerRescanContractTests(unittest.TestCase):
    def test_remote_exposes_rescan_from_status_and_review(self) -> None:
        html = (ROOT / "school_csm_control_center" / "web_server" / "static" / "scanner_remote.html").read_text(encoding="utf-8")
        for token in (
            'id="rescanButton"',
            'id="reviewRescanButton"',
            "Rescan / Take New Photo",
            "discardAndRescan",
            "operator_rescan",
            "resetScan(true)",
        ):
            self.assertIn(token, html)
        self.assertNotIn('id="newScanButton"', html)

    def test_ready_for_review_job_can_be_discarded_for_rescan(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            image = Image.new("RGB", (900, 1200), "white")
            buffer = BytesIO()
            image.save(buffer, "JPEG")
            data_url = "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
            operator = {"user_id": "SCOP-0001", "username": "scanner01", "display_name": "Operator One"}
            manager = ScannerJobManager(root, processing_limit_provider=lambda: 1, worker_count=1)
            manager.close()
            created = manager.create_job(image_data_url=data_url, operator=operator, session_id="SCNS-ONE")
            with manager._condition:
                job = manager._jobs[created["job_id"]]
                try:
                    manager._queue.remove(created["job_id"])
                except ValueError:
                    pass
                job["status"] = "ready_for_review"
                job["processing_stage"] = "ready_for_review"
                manager._persist_job_locked(job)
            cancelled = manager.cancel(created["job_id"], operator["user_id"])
            self.assertEqual(cancelled["status"], "cancelled")
            self.assertIn("rescan", cancelled["message"].casefold())


class RaisedBarcodeRecognitionTests(unittest.TestCase):
    def test_barcode_and_control_number_are_raised_inside_cell(self) -> None:
        self.assertLess(BARCODE_PRINT_REGION_PX[1], BARCODE_LEGACY_REGION_PX[1])
        self.assertLess(BARCODE_CONTROL_TEXT_Y_PX, 742)

    def test_coordinate_map_accepts_new_and_legacy_barcode_geometry(self) -> None:
        path = ROOT / "assets" / "mrs_v0.4" / "CSM_MRS_Coordinate_Map_v0.4.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["version"], "0.4.2")
        for template in data["templates"].values():
            self.assertEqual(len(template["barcode_regions_px"]), 2)
            self.assertLess(template["barcode_regions_px"][0][1], template["barcode_regions_px"][1][1])

    def test_scanner_round_trips_new_sheet_in_all_languages(self) -> None:
        for language in ("English", "Filipino", "Waray-Waray"):
            with self.subTest(language=language), TemporaryDirectory() as temporary:
                source = Path(temporary) / "official.png"
                page = render_official_mrs_form(ROOT, CONTROL, school_name="Calapi Elementary School", language=language)
                page.save(source)
                result = process_mrs_image(source, Path(temporary) / "recognized", ROOT)
                self.assertEqual(result["barcode"]["value"], CONTROL)
                self.assertEqual(result["barcode"]["status"], "Normal")
                self.assertGreaterEqual(result["barcode"]["region_candidates_checked"], 2)

    def test_scanner_still_reads_legacy_preview_sheet(self) -> None:
        preview = ROOT / "assets" / "mrs_v0.4" / "CSM_MRS_Form_Preview_EN_v0.4.png"
        with TemporaryDirectory() as temporary:
            result = process_mrs_image(preview, Path(temporary), ROOT)
        self.assertEqual(result["barcode"]["value"], CONTROL)
        self.assertEqual(result["barcode"]["status"], "Normal")


if __name__ == "__main__":
    unittest.main()
