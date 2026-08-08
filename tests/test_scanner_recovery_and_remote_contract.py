from __future__ import annotations

import base64
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from PIL import Image

from school_csm_control_center.web_server.scanner_jobs import ScannerJobManager


class ScannerRecoveryTests(unittest.TestCase):
    def _data_url(self) -> str:
        image = Image.new("RGB", (900, 1200), "white")
        buffer = BytesIO()
        image.save(buffer, "JPEG")
        return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")

    def test_job_metadata_survives_manager_restart(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            operator = {"user_id": "SCOP-0001", "username": "scanner01", "display_name": "Operator One"}
            first = ScannerJobManager(root, processing_limit_provider=lambda: 1, worker_count=1)
            first.close()
            created = first.create_job(image_data_url=self._data_url(), operator=operator, session_id="SCNS-ONE")
            metadata = root / "data" / "csm_survey" / "scanner_jobs" / created["job_id"] / "job.json"
            self.assertTrue(metadata.is_file())

            second = ScannerJobManager(root, processing_limit_provider=lambda: 1, worker_count=1)
            try:
                recovered = second.list_jobs(operator["user_id"])
                self.assertTrue(any(item["job_id"] == created["job_id"] for item in recovered))
            finally:
                second.close()


class ScannerRemoteContractTests(unittest.TestCase):
    def test_remote_has_recovery_queue_and_manual_corner_controls(self) -> None:
        root = Path(__file__).parents[1]
        html = (root / "school_csm_control_center" / "web_server" / "static" / "scanner_remote.html").read_text(encoding="utf-8")
        for token in (
            "/api/scanner/jobs",
            "recoverLatestJob",
            "schoolCsmScannerJobId",
            "adjustCornersButton",
            "cornerCanvas",
            "set_corners",
            "clear_corners",
            "queue position",
        ):
            self.assertIn(token, html)


if __name__ == "__main__":
    unittest.main()
