from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from http.cookiejar import CookieJar
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
import unittest
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen

from school_csm_control_center.storage.survey_store import SurveyStore
from school_csm_control_center.web_server.server import SESSION_COOKIE, SurveyHTTPServer


class LocalSurveyServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = SurveyStore(self.root)
        static_root = Path(__file__).parents[1] / "school_csm_control_center" / "web_server" / "static"
        self.settings = {
            "active_mode": "onsite",
            "survey_status": "online",
            "status_message": "",
            "survey_date": "2026-07-21",
            "school_name": "Calapi Elementary School",
            "school_id": "123627",
            "school_division": "Schools Division of Samar",
            "school_district": "Motiong Schools District",
            "school_logo_path": "",
            "public_access_key": "test-key",
            "session_duration_seconds": 300,
            "local_ip": "127.0.0.1",
            "named_hostname": "csm.calapies.home.arpa",
            "network_mode": "hotspot",
            "access_mode": "captive_portal",
            "captive_portal_enabled": True,
        }
        self.server = SurveyHTTPServer(
            ("127.0.0.1", 0),
            store=self.store,
            static_root=static_root,
            settings_provider=lambda: dict(self.settings),
            project_root=self.root,
        )
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def _payload(self, token: str) -> dict:
        return {
            "mode": "onsite",
            "submission_token": token,
            "meta": {
                "client_type": "Citizen",
                "sex": "Male",
                "age": 30,
                "region": "Region VIII",
                "service_availed": ["Enrollment (Walk-in)"],
            },
            "cc": {"cc1": 4, "cc2": 5, "cc3": 4},
            "sqd": {f"sqd{number}": 5 for number in range(9)},
            "feedback": {"comments": "Good service", "email": ""},
            "privacy_acknowledgement": {
                "accepted": True,
                "notice_version": "2026-07-01",
            },
        }

    def _session_cookie(self) -> str:
        token, _ = self.server.create_session()
        return f"{SESSION_COOKIE}={token}"

    def _post(self, payload: dict, cookie: str | None = None) -> tuple[int, dict]:
        request = Request(
            f"{self.base}/api/submit",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", **({"Cookie": cookie} if cookie else {})},
            method="POST",
        )
        try:
            response = urlopen(request, timeout=5)
        except HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))
        with response:
            return response.status, json.loads(response.read().decode("utf-8"))

    def test_access_route_creates_session_and_config(self) -> None:
        opener = build_opener(HTTPCookieProcessor(CookieJar()))
        with opener.open(f"{self.base}/access/test-key", timeout=5) as response:
            self.assertEqual(response.status, 200)
        with opener.open(f"{self.base}/api/config", timeout=5) as response:
            config = json.loads(response.read().decode("utf-8"))
        self.assertTrue(config["session_valid"])
        self.assertEqual(config["survey_status"], "online")
        self.assertIn("sqd0", config["questionnaire"]["sqd_questions"])
        self.assertEqual(len(config["service_catalog"]), 18)
        self.assertNotIn("region_04", {item["code"] for item in config["regions"]})


    def test_captive_portal_route_creates_session_without_public_key(self) -> None:
        opener = build_opener(HTTPCookieProcessor(CookieJar()))
        with opener.open(f"{self.base}/portal", timeout=5) as response:
            self.assertEqual(response.status, 200)
        with opener.open(f"{self.base}/api/config", timeout=5) as response:
            config = json.loads(response.read().decode("utf-8"))
        self.assertTrue(config["session_valid"])
        self.assertEqual(config["access_mode"], "captive_portal")

    def test_school_information_is_exposed_and_logo_is_served(self) -> None:
        logo_path = self.root / "data" / "csm_survey" / "school_logo.png"
        logo_path.parent.mkdir(parents=True, exist_ok=True)
        logo_bytes = b"\x89PNG\r\n\x1a\n" + b"test-school-logo"
        logo_path.write_bytes(logo_bytes)
        self.settings["school_logo_path"] = "data/csm_survey/school_logo.png"
        with urlopen(f"{self.base}/api/status", timeout=5) as response:
            config = json.loads(response.read().decode("utf-8"))
        self.assertEqual(config["school_id"], "123627")
        self.assertEqual(config["school_district"], "Motiong Schools District")
        self.assertEqual(config["school_logo_url"], "/school-logo.png")
        with urlopen(f"{self.base}/school-logo.png", timeout=5) as response:
            self.assertEqual(response.headers.get_content_type(), "image/png")
            self.assertEqual(response.read(), logo_bytes)

    def test_submission_expires_session_and_refreshes_store(self) -> None:
        cookie = self._session_cookie()
        status, result = self._post(self._payload("one"), cookie)
        self.assertEqual(status, 201)
        self.assertEqual(result["control_number"], "2026-07-0001")
        self.assertEqual(len(self.store.list()), 1)
        status2, result2 = self._post(self._payload("two"), cookie)
        self.assertEqual(status2, 401)
        self.assertEqual(result2["code"], "session_expired")

    def test_duplicate_submission_token_is_idempotent(self) -> None:
        payload = self._payload("same-token")
        status1, first = self._post(payload, self._session_cookie())
        status2, second = self._post(payload)
        self.assertEqual(status1, 201)
        self.assertEqual(status2, 201)
        self.assertEqual(first["record_id"], second["record_id"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(len(self.store.list()), 1)

    def test_concurrent_sessions_get_unique_control_numbers(self) -> None:
        def submit(number: int) -> tuple[int, dict]:
            return self._post(self._payload(f"token-{number}"), self._session_cookie())
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(submit, range(12)))
        self.assertTrue(all(status == 201 for status, _ in results))
        controls = [payload["control_number"] for _, payload in results]
        self.assertEqual(len(set(controls)), 12)
        self.assertEqual(sorted(controls)[0], "2026-07-0001")
        self.assertEqual(sorted(controls)[-1], "2026-07-0012")

    def test_missing_school_id_blocks_browser_submission(self) -> None:
        self.settings["school_id"] = ""
        status, result = self._post(self._payload("missing-school-id"), self._session_cookie())
        self.assertEqual(status, 400)
        self.assertIn("School ID is required", result["error"])

    def test_offline_and_maintenance_reject_submissions(self) -> None:
        for status_name, code in (("offline", "offline"), ("maintenance", "maintenance")):
            self.settings["survey_status"] = status_name
            status, result = self._post(self._payload(status_name), self._session_cookie())
            self.assertEqual(status, 503)
            self.assertEqual(result["code"], code)


if __name__ == "__main__":
    unittest.main()
