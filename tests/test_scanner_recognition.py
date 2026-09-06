from __future__ import annotations

import base64
from http.cookiejar import CookieJar
from io import BytesIO
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
from school_csm_control_center.storage.mrs_registry import MRSRegistryStore
from school_csm_control_center.storage.scanner_security import ScannerOperatorStore
from school_csm_control_center.storage.survey_store import SurveyStore
from school_csm_control_center.web_server.scanner_engine import ENGINE_VERSION, ScannerProcessingOptions, process_mrs_image
from school_csm_control_center.web_server.server import SurveyHTTPServer


ROOT = Path(__file__).parents[1]
MAP_PATH = ROOT / "assets" / "mrs_v0.4" / "CSM_MRS_Coordinate_Map_v0.4.json"
CONTROL = "CSM-MRS-123627-2026-07-0001"
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


def synthetic_mrs_image():
    coordinate_map = json.loads(MAP_PATH.read_text(encoding="utf-8"))
    template = coordinate_map["templates"]["en"]
    image = render_official_mrs_form(ROOT, CONTROL, school_name="Calapi Elementary School")
    draw = ImageDraw.Draw(image)
    for field_name, code in EXPECTED.items():
        center_x, center_y = template["fields"][field_name][code]["center_px"]
        radius = 18
        draw.ellipse(
            (center_x - radius, center_y - radius, center_x + radius, center_y + radius),
            fill="black",
        )
    return image


class ScannerRecognitionEngineTests(unittest.TestCase):
    def test_server_engine_detects_marked_mrs_fields(self) -> None:
        with TemporaryDirectory() as temporary:
            source = Path(temporary) / "mrs.png"
            output = Path(temporary) / "out"
            synthetic_mrs_image().save(source)
            result = process_mrs_image(source, output, ROOT)
            self.assertEqual(result["template_id"], "CSM-MRS-A4-2026-04-EN")
            self.assertEqual(result["marker_ids"], [10, 11, 12, 13])
            self.assertEqual(result["ambiguous_fields"], 0)
            self.assertEqual(result["blank_fields"], 0)
            self.assertEqual({key: value["value"] for key, value in result["fields"].items()}, EXPECTED)
            self.assertEqual(result["barcode"]["value"], CONTROL)
            self.assertEqual(result["barcode"]["status"], "Normal")
            self.assertTrue((output / "source.jpg").is_file())
            self.assertTrue((output / "canonical.jpg").is_file())
            self.assertTrue((output / "corrected.jpg").is_file())
            self.assertEqual(len(result["source_corners"]), 4)

    def test_manual_page_corners_are_ordered_and_processed(self) -> None:
        with TemporaryDirectory() as temporary:
            source = Path(temporary) / "mrs.png"
            output = Path(temporary) / "out"
            image = synthetic_mrs_image()
            image.save(source)
            width, height = image.size
            result = process_mrs_image(
                source,
                output,
                ROOT,
                options=ScannerProcessingOptions(
                    manual_corners=((width - 1, height - 1), (0, 0), (0, height - 1), (width - 1, 0)),
                    manual_language="en",
                ),
            )
            self.assertEqual(result["language_code"], "en")
            self.assertEqual(result["source_corners"][0], [0.0, 0.0])
            self.assertEqual(result["source_corners"][2], [float(width - 1), float(height - 1)])
            self.assertIn("operator-supplied page corners", " ".join(result["warnings"]))


class ScannerRemoteFinalizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        static_map = self.root / "school_csm_control_center" / "web_server" / "static" / MAP_PATH.name
        static_map.parent.mkdir(parents=True)
        shutil.copy2(MAP_PATH, static_map)
        self.store = SurveyStore(self.root)
        self.operator_store = ScannerOperatorStore(self.root)
        self.operator = self.operator_store.save_account(
            username="scanner01",
            display_name="Juan Dela Cruz",
            password="secret12",
            enabled=True,
        )
        registry = MRSRegistryStore(self.root)
        batch = registry.create_print_batch(
            school_id="123627",
            language="English",
            template_version="CSM-MRS-A4-2026-04-EN",
            new_form_count=1,
            printer={"name": "Test Printer", "status": "Ready", "connection_type": "USB", "a4_capable": True},
            printed_by="Control Center Operator",
            generated_at="2026-07-22T07:30:00+08:00",
        )
        self.control = batch["page_control_numbers"][0]
        registry.mark_batch_submitted(batch["print_batch_id"], accepted=True)
        registry.confirm_print_results(batch["print_batch_id"], [])
        static_root = ROOT / "school_csm_control_center" / "web_server" / "static"
        self.received: list[dict] = []
        self.settings = {
            "active_mode": "onsite",
            "survey_status": "offline",
            "survey_date": "2026-07-22",
            "school_id": "123627",
            "scanner_remote_enabled": True,
            "scanner_intake_enabled": True,
            "scanner_store_preview": True,
            "scanner_processing_limit": 1,
            "scanner_session_inactivity_seconds": 1800,
            "scanner_session_max_seconds": 28800,
            "scanner_failed_login_limit": 5,
            "scanner_lockout_seconds": 900,
            "local_ip": "127.0.0.1",
            "named_hostname": "csm.calapies.home.arpa",
        }
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

    def request(self, path: str, payload: dict | None = None, method: str = "GET") -> tuple[int, dict]:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = Request(
            f"{self.base}{path}",
            data=data,
            headers={"Content-Type": "application/json"} if data is not None else {},
            method=method,
        )
        try:
            response = self.opener.open(request, timeout=12)
        except HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))
        with response:
            body = response.read()
            return response.status, json.loads(body.decode("utf-8")) if body else {}

    def test_remote_job_review_finalizes_with_session_operator(self) -> None:
        status, login = self.request(
            "/api/scanner/auth/login",
            {"username": "scanner01", "password": "secret12"},
            "POST",
        )
        self.assertEqual(status, 200)
        image = synthetic_mrs_image()
        buffer = BytesIO()
        image.save(buffer, "JPEG", quality=95)
        data_url = "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
        status, job = self.request("/api/scanner/jobs", {"image_data_url": data_url}, "POST")
        self.assertEqual(status, 202)
        # Full marker, perspective, barcode, and field recognition is a
        # CPU-bound background operation. Allow slower deployment laptops to
        # complete without treating an actively processing job as a failure.
        deadline = monotonic() + 120
        while monotonic() < deadline:
            status, job = self.request(f"/api/scanner/jobs/{job['job_id']}/status")
            self.assertEqual(status, 200)
            if job["status"] in {"ready_for_review", "failed"}:
                break
            sleep(0.05)
        self.assertEqual(job["status"], "ready_for_review")
        self.assertTrue(
            job["recognition_available"],
            job.get("recognition_error") or job.get("message"),
        )
        self.assertEqual(job["recognition"]["fields"]["service_availed"]["value"], "S03")

        status, finalized = self.request(
            f"/api/scanner/jobs/{job['job_id']}/finalize",
            {
                "operator_review_completed": True,
                "responses": EXPECTED,
                "manual": {
                    "survey_date": "2026-07-22",
                    "control_number": self.control,
                    "barcode_status": "Normal",
                    "comments": "Reviewed on phone",
                },
            },
            "POST",
        )
        self.assertEqual(status, 201, finalized)
        self.assertEqual(finalized["control_number"], self.control)
        record = self.store.list()[0]
        self.assertEqual(record["meta"]["scanner_operator_user_id"], self.operator["user_id"])
        self.assertEqual(record["meta"]["scanner_session_id"], login["session_id"])
        self.assertEqual(record["meta"]["scanner_job_id"], job["job_id"])
        self.assertEqual(record["meta"]["scanner_processing_engine_version"], ENGINE_VERSION)
        self.assertEqual(record["meta"]["source_form_control_number"], self.control)
        self.assertTrue(record["meta"]["scanner_corrected_image_path"].endswith("/corrected.jpg"))
        self.assertEqual(len(self.received), 1)


if __name__ == "__main__":
    unittest.main()
