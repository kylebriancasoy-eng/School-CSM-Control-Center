from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from registration_service.core import RegistrationRepository, RegistrationService, ServiceError
from registration_service.directory_requests import DirectoryRegistrationRequests


def submission(**changes):
    return {
        "request_id": str(uuid4()), "request_secret": "a" * 64,
        "installation_id": "11111111-1111-4111-8111-111111111111",
        "school_id": "123456", "school_name": "Example Elementary School",
        "school_district": "Schools District of Motiong", "school_division": "Schools Division of Samar",
        **changes,
    }


class RegistrationRequestCoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "district.sqlite3"
        self.repository = RegistrationRepository(self.path)
        self.requests = DirectoryRegistrationRequests(self.repository)
        self.service = RegistrationService(self.repository, managed_domain="district.example.test")

    def review(self, payload, decision="approve", *, revision=None):
        if revision is None:
            revision = next(row["revision"] for row in self.requests.list("all")
                if row["request_id"] == payload["request_id"])
        return self.requests.review(payload["request_id"], decision, expected_revision=revision)

    def test_submit_is_pending_and_does_not_enroll_or_add_official_directory(self):
        payload = submission()
        receipt = self.requests.submit(payload)
        self.assertEqual(receipt, {"request_id": payload["request_id"], "school_id": "123456", "status": "pending"})
        with self.repository.read() as conn:
            for table in ("schools", "district_schools", "installations", "activation_codes", "passkeys"):
                self.assertEqual(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)
            stored = conn.execute("SELECT * FROM school_registration_requests").fetchone()
            self.assertEqual(stored["request_secret_sha256"], hashlib.sha256(payload["request_secret"].encode()).hexdigest())
            self.assertNotIn(payload["request_secret"], str(tuple(stored)))
        self.assertNotIn("request_secret_sha256", self.requests.list()[0])
        with self.assertRaisesRegex(ServiceError, "Add and approve"):
            self.service.issue_directory_activation_code("123456")

    def test_retries_are_idempotent_and_pending_corrections_are_audited_once(self):
        payload = submission()
        self.requests.submit(payload)
        self.requests.submit(payload)
        payload["school_name"] = "Corrected Elementary School"
        self.requests.submit(payload)
        self.requests.submit(payload)
        self.assertEqual(self.requests.list()[0]["school_name"], payload["school_name"])
        with self.repository.read() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM school_registration_requests").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0], 2)
            self.assertNotIn(payload["request_secret"], str(conn.execute("SELECT detail_json FROM audit_events").fetchall()))

    def test_receipt_and_identity_collision_cannot_modify_request(self):
        payload = submission()
        self.requests.submit(payload)
        for changes in ({"request_secret": "b" * 64}, {"school_id": "234567"}, {"installation_id": str(uuid4())}):
            with self.assertRaisesRegex(ServiceError, "could not be verified"):
                self.requests.submit({**payload, **changes})
        self.assertEqual(self.requests.list()[0]["status"], "pending")

    def test_approval_adds_official_identity_but_no_activation_or_gateway_authority(self):
        payload = submission()
        self.requests.submit(payload)
        approved = self.review(payload)
        self.assertEqual(approved["status"], "approved")
        school = self.service.district_school_detail("123456")
        self.assertEqual(school["directory_status"], "approved")
        self.assertEqual(school["registration_state"], "not_registered")
        with self.repository.read() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM activation_codes").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM installations").fetchone()[0], 0)
        self.assertEqual(self.review(payload), approved)
        self.requests.submit({**payload, "school_name": "Attacker Name"})
        self.assertEqual(self.service.district_school_detail("123456")["school_name"], "Example Elementary School")
        with self.assertRaises(ServiceError):
            self.review(payload, "reject")

    def test_existing_directory_and_suspension_are_never_overwritten(self):
        self.service.save_directory_school(school_id="123456", school_name="Official School")
        payload = submission()
        self.requests.submit(payload)
        with self.assertRaisesRegex(ServiceError, "conflict"):
            self.review(payload)
        self.assertEqual(self.service.district_school_detail("123456")["school_name"], "Official School")
        self.service.set_directory_school_status("123456", "suspended")
        with self.assertRaisesRegex(ServiceError, "suspended"):
            self.review(payload)
        self.assertEqual(self.review(payload, "reject")["status"], "rejected")
        self.assertEqual(self.requests.submit(payload)["status"], "rejected")
        self.assertEqual(self.service.district_school_detail("123456")["directory_status"], "suspended")

    def test_bounds_and_invalid_fields_do_not_write(self):
        for changes in (
            {"school_id": "１２３４５６"}, {"school_name": "<bad>\x00"}, {"school_name": "x" * 201},
            {"school_district": ""}, {"school_division": "x" * 161}, {"request_secret": "invalid"},
            {"request_id": "no"}, {"installation_id": "no"}, {"school_email": "should-stay-local@example.test"},
        ):
            with self.assertRaises(ServiceError):
                self.requests.submit(submission(**changes))
        for _ in range(5):
            self.requests.submit(submission())
        with self.assertRaisesRegex(ServiceError, "queue is full"):
            self.requests.submit(submission())
        with patch("registration_service.directory_requests.MAX_PENDING_REQUESTS", 5):
            with self.assertRaisesRegex(ServiceError, "queue is full"):
                self.requests.submit(submission(school_id="234567"))

    def test_additive_migration_preserves_old_roster(self):
        self.service.save_directory_school(school_id="123456", school_name="Existing School")
        with self.repository.transaction() as conn:
            conn.execute("DROP TABLE school_registration_requests")
            conn.execute("UPDATE service_meta SET value='2' WHERE key='schema_version'")
        migrated = RegistrationRepository(self.path)
        with migrated.read() as conn:
            self.assertEqual(conn.execute("SELECT school_name FROM district_schools").fetchone()[0], "Existing School")
            self.assertEqual(conn.execute("SELECT value FROM service_meta WHERE key='schema_version'").fetchone()[0], "4")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM school_registration_requests").fetchone()[0], 0)

    def test_changed_identity_cannot_be_approved_from_stale_review_even_in_same_second(self):
        payload = submission()
        with patch("registration_service.directory_requests.timestamp", return_value="2026-10-10T00:00:00+00:00"):
            self.requests.submit(payload)
            displayed = self.requests.list()[0]
            self.requests.submit({**payload, "school_name": "Changed School", "school_district": "Different District"})
        self.assertEqual(displayed["updated_at"], self.requests.list()[0]["updated_at"])
        self.assertNotEqual(displayed["revision"], self.requests.list()[0]["revision"])
        for decision in ("approve", "reject"):
            with self.assertRaises(ServiceError) as caught:
                self.review(payload, decision, revision=displayed["revision"])
            self.assertEqual(caught.exception.code, "directory_request_stale")
        with self.repository.read() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM district_schools").fetchone()[0], 0)
        self.assertEqual(self.requests.list()[0]["status"], "pending")
        self.review(payload)
        self.assertEqual(self.service.district_school_detail("123456")["school_name"], "Changed School")

    def test_pending_requests_are_not_hidden_by_reviewed_history(self):
        payload = submission()
        self.requests.submit(payload)
        with self.repository.transaction() as conn:
            for _ in range(1000):
                conn.execute("""INSERT INTO school_registration_requests(
                    request_id,request_secret_sha256,installation_id,school_id,
                    school_name,school_district,school_division,status,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,'rejected',?,?)""", (
                    str(uuid4()), "f" * 64, payload["installation_id"], "234567", "History School",
                    payload["school_district"], payload["school_division"],
                    "2099-01-01T00:00:00+00:00", "2099-01-01T00:00:00+00:00",
                ))
        all_requests = self.requests.list("all")
        self.assertEqual(len(all_requests), 1000)
        self.assertEqual(all_requests[0]["request_id"], payload["request_id"])
        self.assertEqual(len(self.requests.list("pending")), 1)
        self.assertEqual(self.requests.counts(), {"pending": 1, "approved": 0, "rejected": 1000})


API_AVAILABLE = all(importlib.util.find_spec(name) for name in ("fastapi", "webauthn", "httpx"))


@unittest.skipUnless(API_AVAILABLE, "District API test dependencies are not installed")
class RegistrationRequestApiTests(RegistrationRequestCoreTests):
    def setUp(self):
        super().setUp()
        from fastapi.testclient import TestClient
        from registration_service.app import create_app
        self.token = "test-district-administrator-token-" + "x" * 24
        app = create_app(self.service, district_admin_token_sha256=hashlib.sha256(self.token.encode()).hexdigest())
        self.client = TestClient(app, base_url="https://testserver")
        self.addCleanup(self.client.close)

    def login(self):
        result = self.client.post("/v1/admin/session", json={"access_token": self.token})
        self.assertEqual(result.status_code, 200)
        return {"X-CSM-CSRF-Token": result.json()["csrf_token"]}

    def test_public_submit_and_private_review_require_csrf(self):
        payload = submission()
        sent = self.client.post("/v1/directory/registration-requests", json=payload)
        self.assertEqual(sent.status_code, 200, sent.text)
        self.assertEqual(sent.headers["cache-control"], "no-store")
        route = f'/v1/admin/registration-requests/{payload["request_id"]}/review'
        self.assertEqual(self.client.get("/v1/admin/registration-requests").status_code, 401)
        self.assertEqual(self.client.post(route, json={"decision": "approve"}).status_code, 401)
        csrf = self.login()
        self.assertEqual(self.client.post(route, json={"decision": "approve"}).status_code, 403)
        listed = self.client.get("/v1/admin/registration-requests").json()["requests"]
        self.assertNotIn(payload["request_secret"], json.dumps(listed))
        self.assertEqual(self.client.post(route, json={"decision": "approve"}, headers=csrf).status_code, 422)
        body = {"decision": "approve", "expected_revision": listed[0]["revision"]}
        self.client.post("/v1/directory/registration-requests", json={**payload, "school_name": "Updated School"})
        stale = self.client.post(route, json=body, headers=csrf)
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertEqual(stale.json()["code"], "directory_request_stale")
        body["expected_revision"] = self.client.get("/v1/admin/registration-requests").json()["requests"][0]["revision"]
        approved = self.client.post(route, json=body, headers=csrf)
        self.assertEqual(approved.status_code, 200, approved.text)
        self.assertEqual(approved.json()["status"], "approved")

    def test_public_input_body_and_rate_limits_ignore_forged_forwarding_headers(self):
        payload = submission()
        self.assertEqual(self.client.get("/v1/directory/registration-requests").status_code, 405)
        for changes in ({"school_email": "private@example.test"}, {"status": "approved"}):
            self.assertEqual(self.client.post("/v1/directory/registration-requests", json={**payload, **changes}).status_code, 422)
        huge = json.dumps({**payload, "school_name": "x" * 9000})
        for length in ("9000", "1"):
            result = self.client.post("/v1/directory/registration-requests", content=huge,
                headers={"Content-Type": "application/json", "Content-Length": length})
            self.assertEqual(result.status_code, 413)
        for index in range(30):
            response = self.client.post("/v1/directory/registration-requests", json=payload,
                headers={"X-Forwarded-For": f"192.0.2.{index}"})
        self.assertEqual(response.status_code, 429)


if __name__ == "__main__":
    unittest.main()
