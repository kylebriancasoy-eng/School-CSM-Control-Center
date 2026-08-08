from __future__ import annotations

import base64
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from time import monotonic, sleep
import unittest

from PIL import Image

from school_csm_control_center.web_server.scanner_jobs import ScannerJobManager


class ScannerQueueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.limit = 1
        self.manager = ScannerJobManager(
            Path(self.temp.name),
            processing_limit_provider=lambda: self.limit,
            worker_count=2,
        )
        image = Image.new("RGB", (800, 1000), "white")
        buffer = BytesIO()
        image.save(buffer, "JPEG")
        self.data_url = "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
        self.operator = {"user_id": "SCOP-0001", "username": "scanner01", "display_name": "Operator One"}

    def tearDown(self) -> None:
        self.manager.close()
        self.temp.cleanup()

    def test_limit_one_queues_second_job_and_reports_position(self) -> None:
        processing_started = Event()
        release_processing = Event()

        def blocked_process(job_id: str) -> None:
            processing_started.set()
            release_processing.wait(timeout=5)
            with self.manager._lock:  # test-only deterministic worker boundary
                job = self.manager._jobs[job_id]
                job["status"] = "ready_for_review"
                job["processing_stage"] = "image_prepared"
                job["message"] = "Prepared by deterministic queue test."

        self.manager._process = blocked_process  # type: ignore[method-assign]
        first = self.manager.create_job(
            image_data_url=self.data_url,
            operator=self.operator,
            session_id="SCNS-ONE",
        )
        self.assertTrue(processing_started.wait(timeout=2))
        second = self.manager.create_job(
            image_data_url=self.data_url,
            operator=self.operator,
            session_id="SCNS-ONE",
        )
        second_status = self.manager.status(second["job_id"], self.operator["user_id"])
        self.assertEqual(second_status["status"], "queued")
        self.assertEqual(second_status["queue_position"], 1)
        self.assertEqual(second_status["active_jobs"], 1)
        self.assertEqual(second_status["processing_limit"], 1)
        self.assertEqual(second_status["waiting_jobs"], 1)

        release_processing.set()
        deadline = monotonic() + 3
        while monotonic() < deadline:
            status = self.manager.status(second["job_id"], self.operator["user_id"])
            if status["status"] != "queued":
                break
            sleep(0.02)
        self.assertIn(status["status"], {"processing", "ready_for_review"})

    def test_queued_job_can_be_cancelled_without_control_number(self) -> None:
        processing_started = Event()
        release_processing = Event()

        def blocked_process(job_id: str) -> None:
            processing_started.set()
            release_processing.wait(timeout=5)
            with self.manager._lock:
                job = self.manager._jobs[job_id]
                job["status"] = "ready_for_review"

        self.manager._process = blocked_process  # type: ignore[method-assign]
        self.manager.create_job(image_data_url=self.data_url, operator=self.operator, session_id="SCNS-ONE")
        self.assertTrue(processing_started.wait(timeout=2))
        queued = self.manager.create_job(image_data_url=self.data_url, operator=self.operator, session_id="SCNS-ONE")
        cancelled = self.manager.cancel(queued["job_id"], self.operator["user_id"])
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(cancelled["queue_position"], 0)
        self.assertNotIn("control_number", cancelled)
        release_processing.set()


if __name__ == "__main__":
    unittest.main()
