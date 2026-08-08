from __future__ import annotations

import base64
from http.cookiejar import CookieJar
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from time import monotonic, sleep
import unittest
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener

from PIL import Image

from school_csm_control_center.storage.mrs_registry import MRSRegistryStore
from school_csm_control_center.storage.scanner_security import ScannerOperatorStore
from school_csm_control_center.storage.survey_store import SurveyStore
from school_csm_control_center.web_server.server import SurveyHTTPServer


class ScannerIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = SurveyStore(self.root)
        self.operator_store = ScannerOperatorStore(self.root)
        self.operator = self.operator_store.save_account(
            username="scanner01",
            display_name="Juan Dela Cruz",
            password="secret12",
            enabled=True,
        )
        static_root = Path(__file__).parents[1] / "school_csm_control_center" / "web_server" / "static"
        self.received: list[dict] = []
        self.settings = {
            "active_mode": "onsite",
            "survey_status": "offline",
            "survey_date": "2026-07-22",
            "school_id": "123627",
            "scanner_remote_enabled": True,
            "scanner_intake_enabled": True,
            "scanner_store_preview": False,
            "scanner_processing_limit": 1,
            "scanner_session_inactivity_seconds": 1800,
            "scanner_session_max_seconds": 28800,
            "scanner_failed_login_limit": 5,
            "scanner_lockout_seconds": 900,
            "local_ip": "127.0.0.1",
            "named_hostname": "csm.calapies.home.arpa",
        }
        self.registry = MRSRegistryStore(self.root)
        batch = self.registry.create_print_batch(
            school_id="123627",
            language="English",
            template_version="CSM-MRS-A4-2026-04-EN",
            new_form_count=1,
            printer={"name": "Test Printer", "status": "Ready", "connection_type": "USB", "a4_capable": True},
            printed_by="Control Center Operator",
            generated_at="2026-07-22T07:30:00+08:00",
        )
        self.registered_control = batch["page_control_numbers"][0]
        self.registry.mark_batch_submitted(batch["print_batch_id"], accepted=True)
        self.registry.confirm_print_results(batch["print_batch_id"], [])
        self.server = SurveyHTTPServer(
            ("127.0.0.1", 0),
            store=self.store,
            static_root=static_root,
            settings_provider=lambda: dict(self.settings),
            project_root=self.root,
            response_callback=lambda record: self.received.append(record),
            scanner_operator_store=self.operator_store,
        )
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.opener = build_opener(HTTPCookieProcessor(CookieJar()))

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    @staticmethod
    def payload(scanner_id: str = "SCN-2026-000001", control_number: str | None = "CSM-MRS-123627-2026-07-0001") -> dict:
        responses = {
            "control_number": control_number,
            "survey_date": "2026-07-22",
            "age_bracket": "35_49",
            "region": "region_08",
            "comments": "Verified from hardcopy",
            "client_type": "1",
            "sex": "2",
            "service_availed": "S03",
            "cc1": "4",
            "cc2": "5",
            "cc3": "4",
            **{f"sqd{number}": "5" for number in range(9)},
        }
        return {
            "source": "scanned_hardcopy",
            "scanner_submission_id": scanner_id,
            "verification_status": "verified",
            "verified_at": "2026-07-22T08:00:00+08:00",
            "scanner_app": {"name": "CSM MRS Sheet Scanner", "version": "0.2.0"},
            "template": {
                "template_id": "CSM-MRS-A4-2026-04-EN",
                "language": "English",
                "coordinate_map_version": "0.4",
            },
            "recognition": {"overall_confidence": 0.97},
            "responses": responses,
            "verification": {"operator_review_completed": True},
            "corrected_preview": None,
        }

    def request(self, path: str, *, payload: dict | None = None, method: str = "GET", opener=None) -> tuple[int, dict, object]:
        client = opener or self.opener
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = Request(
            f"{self.base}{path}",
            data=data,
            headers={"Content-Type": "application/json"} if data is not None else {},
            method=method,
        )
        try:
            response = client.open(request, timeout=8)
        except HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8")), exc.headers
        with response:
            body = response.read()
            parsed = json.loads(body.decode("utf-8")) if body else {}
            return response.status, parsed, response.headers

    def login(self) -> dict:
        status, result, _ = self.request(
            "/api/scanner/auth/login",
            payload={"username": "scanner01", "password": "secret12"},
            method="POST",
        )
        self.assertEqual(status, 200)
        self.assertTrue(result["authenticated"])
        return result

    def test_scanner_remote_requires_registered_credentials(self) -> None:
        status, result, _ = self.request(
            "/api/scanner/auth/login",
            payload={"username": "scanner01", "password": "wrong-password"},
            method="POST",
        )
        self.assertEqual(status, 401)
        self.assertFalse(result["authenticated"])
        self.assertEqual(result["code"], "invalid_credentials")

        result = self.login()
        self.assertEqual(result["operator"]["user_id"], self.operator["user_id"])
        status, session, _ = self.request("/api/scanner/auth/session")
        self.assertEqual(status, 200)
        self.assertEqual(session["operator"]["display_name"], "Juan Dela Cruz")

    def test_verified_response_uses_mrs_number_and_session_operator(self) -> None:
        login = self.login()
        status, result, _ = self.request(
            "/api/scanner/submissions",
            payload=self.payload(control_number=self.registered_control),
            method="POST",
        )
        self.assertEqual(status, 201)
        self.assertEqual(result["control_number"], self.registered_control)
        record = self.store.list()[0]
        self.assertEqual(record["source_code"], "MRS")
        self.assertEqual(record["meta"]["submission_source"], "scanned_hardcopy")
        self.assertEqual(record["meta"]["source_form_control_number"], self.registered_control)
        self.assertEqual(record["meta"]["scanner_operator_user_id"], self.operator["user_id"])
        self.assertEqual(record["meta"]["scanner_operator_username"], "scanner01")
        self.assertEqual(record["meta"]["scanner_operator_display_name"], "Juan Dela Cruz")
        self.assertEqual(record["meta"]["scanner_session_id"], login["session_id"])
        self.assertEqual(len(self.received), 1)

    def test_duplicate_scanner_submission_returns_existing_record_idempotently(self) -> None:
        self.login()
        self.assertEqual(self.request("/api/scanner/submissions", payload=self.payload(control_number=self.registered_control), method="POST")[0], 201)
        status, result, _ = self.request("/api/scanner/submissions", payload=self.payload(control_number=self.registered_control), method="POST")
        self.assertEqual(status, 200)
        self.assertTrue(result["accepted"])
        self.assertTrue(result["duplicate"])
        self.assertEqual(result["control_number"], self.registered_control)
        self.assertEqual(len(self.store.list()), 1)

    def test_scanner_intake_is_independent_from_public_survey_status(self) -> None:
        self.settings["survey_status"] = "maintenance"
        self.login()
        status, result, _ = self.request(
            "/api/scanner/submissions",
            payload=self.payload("SCN-MAINTENANCE", self.registered_control),
            method="POST",
        )
        self.assertEqual(status, 201)
        self.assertTrue(result["accepted"])

    def test_image_job_is_processed_by_server_and_returns_preview(self) -> None:
        self.login()
        image = Image.new("RGB", (800, 1000), "white")
        buffer = BytesIO()
        image.save(buffer, "JPEG")
        data_url = "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
        status, job, _ = self.request(
            "/api/scanner/jobs",
            payload={"image_data_url": data_url},
            method="POST",
        )
        self.assertEqual(status, 202)
        self.assertIn(job["status"], {"queued", "processing", "ready_for_review"})
        deadline = monotonic() + 8
        while monotonic() < deadline:
            status, job, _ = self.request(f"/api/scanner/jobs/{job['job_id']}/status")
            self.assertEqual(status, 200)
            if job["status"] in {"ready_for_review", "failed"}:
                break
            sleep(0.05)
        self.assertEqual(job["status"], "ready_for_review")
        self.assertEqual(job["processing_limit"], 1)
        self.assertIn("preview_url", job)
        preview_request = Request(f"{self.base}{job['preview_url']}")
        with self.opener.open(preview_request, timeout=5) as response:
            self.assertEqual(response.headers.get_content_type(), "image/jpeg")
            self.assertGreater(len(response.read()), 100)

    def test_unauthenticated_job_access_is_rejected(self) -> None:
        separate = build_opener(HTTPCookieProcessor(CookieJar()))
        status, result, _ = self.request(
            "/api/scanner/jobs",
            payload={"image_data_url": "data:image/jpeg;base64,AA=="},
            method="POST",
            opener=separate,
        )
        self.assertEqual(status, 401)
        self.assertEqual(result["code"], "scanner_session_expired")


class ScannerUIContractTests(unittest.TestCase):
    def test_server_board_exposes_queue_limit_and_operator_accounts(self) -> None:
        source = (Path(__file__).parents[1] / "school_csm_control_center" / "ui" / "server_board.py").read_text("utf-8")
        self.assertIn("Maximum simultaneous scan processing", source)
        self.assertIn("scanner_processing_slider.setRange(1, 10)", source)
        self.assertIn("Very hot · Not recommended", source)
        self.assertIn("Scanner Operator accounts", source)
        self.assertIn("save_scanner_operator", source)

    def test_survey_form_has_scan_icon_and_sign_in_overlay(self) -> None:
        source = (Path(__file__).parents[1] / "school_csm_control_center" / "web_server" / "static" / "index.html").read_text("utf-8")
        self.assertIn('id="scannerLaunch"', source)
        self.assertIn('id="scannerLoginOverlay"', source)
        self.assertIn("/api/scanner/auth/login", source)

    def test_scanner_remote_displays_queue_position(self) -> None:
        source = (Path(__file__).parents[1] / "school_csm_control_center" / "web_server" / "static" / "scanner_remote.html").read_text("utf-8")
        self.assertIn("Queued for Processing", source)
        self.assertIn("Your queue position", source)
        self.assertIn("waiting_jobs", source)
        self.assertIn("/api/scanner/jobs", source)


if __name__ == "__main__":
    unittest.main()
