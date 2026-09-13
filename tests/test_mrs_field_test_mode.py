from __future__ import annotations

import base64
from http.cookiejar import CookieJar
from io import BytesIO
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
from threading import Thread
from time import monotonic, sleep
import unittest
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener

from PIL import ImageDraw

from school_csm_control_center.mrs_printing import render_official_mrs_form
from school_csm_control_center.storage.mrs_field_tests import MRSFieldTestStore
from school_csm_control_center.storage.mrs_registry import MRSRegistryStore
from school_csm_control_center.storage.scanner_security import ScannerOperatorStore
from school_csm_control_center.storage.survey_store import SurveyStore
from school_csm_control_center.web_server.server import SurveyHTTPServer


ROOT = Path(__file__).parents[1]
MAP_PATH = ROOT / "assets" / "mrs_v0.4" / "CSM_MRS_Coordinate_Map_v0.4.json"
CONTROL = "CSM-MRS-123627-2026-07-7777"
EXPECTED = {
    "age_bracket": "35_49",
    "client_type": "1",
    "sex": "2",
    "region": "region_08",
    "service_availed": "S03",
    "cc1": "4",
    "cc2": "5",
    "cc3": "4",
    **{f"sqd{number}": "5" for number in range(9)},
}


def field_test_image():
    coordinate_map = json.loads(MAP_PATH.read_text(encoding="utf-8"))
    template = coordinate_map["templates"]["en"]
    image = render_official_mrs_form(ROOT, CONTROL, school_name="Calapi Elementary School")
    draw = ImageDraw.Draw(image)
    for field_name, code in EXPECTED.items():
        center_x, center_y = template["fields"][field_name][code]["center_px"]
        draw.ellipse((center_x - 18, center_y - 18, center_x + 18, center_y + 18), fill="black")
    return image


class MRSFieldTestStoreTests(unittest.TestCase):
    def test_store_isolated_record_and_summary(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = MRSFieldTestStore(root)
            recorded = store.record(
                context={
                    "scanner_job_id": "SCNJ-TEST",
                    "barcode_status": "Normal",
                    "template": {"template_id": "T", "language": "English", "coordinate_map_version": "0.4"},
                    "recognition": {"engine_version": "E", "overall_confidence": 0.91, "detected_fields": 17, "ambiguous_fields": 0, "blank_fields": 0},
                    "responses": {"control_number": CONTROL},
                    "scanner_job": {"image_sha256": "abc"},
                },
                operator={"user_id": "SCOP-1", "username": "scanner", "display_name": "Scanner One"},
                session_id="SCNS-1",
                test_context={"outcome": "pass", "capture_condition": "Straight / normal light"},
            )
            self.assertTrue(recorded["test_result_id"].startswith("MRS-FT-"))
            self.assertFalse(recorded["official_response_created"])
            self.assertEqual(recorded["analysis_status"], "excluded_test")
            self.assertEqual(store.summary()["outcomes"]["pass"], 1)


class MRSFieldTestRemoteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        installed_map = self.root / "assets" / "mrs_v0.4" / MAP_PATH.name
        installed_map.parent.mkdir(parents=True)
        shutil.copy2(MAP_PATH, installed_map)
        self.store = SurveyStore(self.root)
        self.operator_store = ScannerOperatorStore(self.root)
        self.operator_store.save_account(
            username="scanner01", display_name="Field Tester", password="secret12", enabled=True
        )
        self.settings = {
            "active_mode": "onsite",
            "survey_status": "offline",
            "survey_date": "2026-07-23",
            "school_id": "123627",
            "scanner_remote_enabled": True,
            "scanner_intake_enabled": True,
            "scanner_processing_limit": 1,
            "scanner_session_inactivity_seconds": 1800,
            "scanner_session_max_seconds": 28800,
            "scanner_failed_login_limit": 5,
            "scanner_lockout_seconds": 900,
        }
        static_root = ROOT / "school_csm_control_center" / "web_server" / "static"
        self.server = SurveyHTTPServer(
            ("127.0.0.1", 0),
            store=self.store,
            static_root=static_root,
            settings_provider=lambda: dict(self.settings),
            project_root=self.root,
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

    def request(self, path: str, payload: dict | None = None, method: str = "GET") -> tuple[int, dict]:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = Request(
            f"{self.base}{path}", data=data,
            headers={"Content-Type": "application/json"} if data is not None else {}, method=method,
        )
        try:
            response = self.opener.open(request, timeout=15)
        except HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))
        with response:
            body = response.read()
            return response.status, json.loads(body.decode("utf-8")) if body else {}

    def test_field_test_finalize_does_not_create_official_response_or_registry_record(self) -> None:
        status, _login = self.request(
            "/api/scanner/auth/login", {"username": "scanner01", "password": "secret12"}, "POST"
        )
        self.assertEqual(status, 200)
        image = field_test_image()
        buffer = BytesIO()
        image.save(buffer, "JPEG", quality=95)
        data_url = "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
        status, job = self.request(
            "/api/scanner/jobs", {"image_data_url": data_url, "test_mode": True}, "POST"
        )
        self.assertEqual(status, 202)
        self.assertTrue(job["test_mode"])
        # Full-suite print/recognition tests can leave OpenCV workers under
        # short-lived CPU pressure.  Wait for the real terminal state rather
        # than treating a still-progressing job as a recognition failure.
        deadline = monotonic() + 120
        while monotonic() < deadline:
            status, job = self.request(f"/api/scanner/jobs/{job['job_id']}/status")
            self.assertEqual(status, 200)
            if job["status"] in {"ready_for_review", "failed"}:
                break
            sleep(0.05)
        self.assertEqual(job["status"], "ready_for_review", job)
        status, finalized = self.request(
            f"/api/scanner/jobs/{job['job_id']}/finalize",
            {
                "operator_review_completed": True,
                "barcode_status_confirmed": True,
                "responses": EXPECTED,
                "manual": {"survey_date": "2026-07-23", "barcode_status": "Normal", "control_number": CONTROL},
                "test_context": {
                    "printer_or_source": "Epson L3210 · USB",
                    "device": "Test Phone",
                    "capture_condition": "Angled camera",
                    "expected_result": "All marked fields detected",
                    "outcome": "pass",
                    "notes": "Synthetic integration test",
                },
            },
            "POST",
        )
        self.assertEqual(status, 201, finalized)
        self.assertTrue(finalized["test_mode"])
        self.assertFalse(finalized["included_in_analysis"])
        self.assertTrue(finalized["test_result_id"].startswith("MRS-FT-"))
        self.assertEqual(self.store.list(), [])
        self.assertEqual(MRSRegistryStore(self.root).list_forms(), [])
        tests = MRSFieldTestStore(self.root).list()
        self.assertEqual(len(tests), 1)
        self.assertEqual(tests[0]["outcome"], "pass")
        self.assertEqual(tests[0]["capture_condition"], "Angled camera")

    def test_remote_contract_exposes_field_test_mode(self) -> None:
        html = (ROOT / "school_csm_control_center" / "web_server" / "static" / "scanner_remote.html").read_text(encoding="utf-8")
        for token in ("fieldTestMode", "FIELD TEST MODE", "test_context", "testOutcome", "testPrinter"):
            self.assertIn(token, html)


if __name__ == "__main__":
    unittest.main()
