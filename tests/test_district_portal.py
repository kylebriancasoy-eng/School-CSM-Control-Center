from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from registration_service.core import (
    RegistrationRepository,
    RegistrationService,
    ServiceError,
)
from registration_service.district_auth import (
    CSRF_HEADER,
    SESSION_COOKIE,
    DistrictAdminSessions,
)


SERVICE_DEPS_AVAILABLE = bool(
    importlib.util.find_spec("fastapi") and importlib.util.find_spec("webauthn")
)


class _Provisioner:
    def __init__(self) -> None:
        self.revoked: list[str] = []

    def provision(self, *, school_id: str, public_host: str):
        return {
            "tunnel_id": f"tunnel-{school_id}",
            "tunnel_token": f"tunnel-token-{school_id}",
        }

    def rotate(self, *, tunnel_id: str, school_id: str, public_host: str):
        return self.provision(school_id=school_id, public_host=public_host)

    def revoke(self, *, tunnel_id: str):
        self.revoked.append(tunnel_id)

    def credential(self, *, tunnel_id: str, public_host: str, origin_port: int):
        return {"tunnel_id": tunnel_id, "tunnel_token": "existing-token"}


class DistrictAdminSessionTests(unittest.TestCase):
    ACCESS_TOKEN = "district-administrator-access-token-for-tests"

    def test_login_uses_only_the_configured_digest_and_logout_revokes_session(self) -> None:
        digest = hashlib.sha256(self.ACCESS_TOKEN.encode("utf-8")).hexdigest()
        sessions = DistrictAdminSessions(digest)

        self.assertIsNone(sessions.login("wrong-district-administrator-token"))
        session = sessions.login(self.ACCESS_TOKEN)
        self.assertIsNotNone(session)
        assert session is not None
        self.assertNotEqual(session.session_id, self.ACCESS_TOKEN)
        self.assertNotEqual(session.csrf_token, self.ACCESS_TOKEN)
        self.assertTrue(sessions.csrf_matches(session, session.csrf_token))
        self.assertFalse(sessions.csrf_matches(session, "wrong-csrf-token"))

        sessions.logout(session.session_id)
        self.assertIsNone(sessions.get(session.session_id))

    def test_expired_and_capacity_evicted_sessions_are_rejected(self) -> None:
        digest = hashlib.sha256(self.ACCESS_TOKEN.encode("utf-8")).hexdigest()
        now = datetime(2026, 9, 27, 2, 0, tzinfo=timezone.utc)
        sessions = DistrictAdminSessions(
            digest,
            lifetime=timedelta(minutes=15),
            maximum_sessions=1,
        )

        with patch("registration_service.district_auth._utc_now", return_value=now):
            first = sessions.login(self.ACCESS_TOKEN)
            second = sessions.login(self.ACCESS_TOKEN)
            assert first is not None and second is not None
            self.assertIsNone(sessions.get(first.session_id))
            self.assertIsNotNone(sessions.get(second.session_id))

        with patch(
            "registration_service.district_auth._utc_now",
            return_value=now + timedelta(minutes=16),
        ):
            self.assertIsNone(sessions.get(second.session_id))

    def test_invalid_configured_digest_fails_fast(self) -> None:
        with self.assertRaisesRegex(ValueError, "SHA-256 hex digest"):
            DistrictAdminSessions("not-a-digest")


class DistrictPortalCoreTests(unittest.TestCase):
    INSTALLATION_ID = "11111111-1111-4111-8111-111111111111"

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repository = RegistrationRepository(
            Path(self.temporary.name) / "registration.sqlite3"
        )
        self.provisioner = _Provisioner()
        self.service = RegistrationService(
            self.repository,
            managed_domain="csm.example.gov.ph",
            provisioner=self.provisioner,
        )

    def test_directory_registration_heartbeat_and_health_lifecycle(self) -> None:
        school = self.service.save_directory_school(
            school_id="123456",
            school_name="  Example   Elementary School  ",
            school_district=" Schools District of Motiong ",
            school_division=" Schools Division of Samar ",
        )
        self.assertEqual(school["school_name"], "Example Elementary School")
        self.assertEqual(school["directory_status"], "approved")
        self.assertEqual(school["health_status"], "pending")
        self.assertEqual(
            self.service.district_overview()["counts"],
            {
                "total": 1,
                "pending": 1,
                "registered": 0,
                "connected": 0,
                "attention": 0,
            },
        )

        activation_code = self.service.issue_directory_activation_code(
            "123456", valid_for=timedelta(days=2)
        )
        registration = self.service.complete_initial_registration(
            school_id="123456",
            school_name="Example Elementary School",
            installation_id=self.INSTALLATION_ID,
            activation_code=activation_code,
            credential_id="district-test-passkey",
            credential_public_key=b"district-test-public-key",
            sign_count=0,
        )
        unseen = self.service.district_school_detail("123456")
        self.assertEqual(unseen["registration_state"], "registered")
        self.assertEqual(unseen["health_status"], "unseen")

        heartbeat = self.service.record_heartbeat(
            installation_id=self.INSTALLATION_ID,
            installation_secret=registration.installation_secret,
            application_version="0.5.1",
            local_server_state="running",
            gateway_state="connected",
            survey_status="online",
        )
        self.assertTrue(heartbeat["ok"])
        connected = self.service.district_school_detail("123456")
        self.assertEqual(connected["health_status"], "connected")
        self.assertEqual(connected["application_version"], "0.5.1")
        self.assertEqual(connected["survey_status"], "online")
        self.assertNotIn("installation_secret", connected)
        self.assertNotIn("tunnel_token", connected)

        counts = self.service.district_overview()["counts"]
        self.assertEqual(counts["registered"], 1)
        self.assertEqual(counts["connected"], 1)
        self.assertEqual(counts["attention"], 0)
        audit_text = repr(self.service.list_audit_events(school_id="123456"))
        self.assertNotIn(activation_code, audit_text)
        self.assertNotIn(registration.installation_secret, audit_text)

    def test_directory_identity_wins_and_unhealthy_origin_needs_attention(self) -> None:
        self.service.save_directory_school(
            school_id="123456",
            school_name="Official Elementary School",
        )
        registration = self.service.complete_initial_registration(
            school_id="123456",
            school_name="Browser Supplied Different Name",
            installation_id=self.INSTALLATION_ID,
            activation_code=self.service.issue_directory_activation_code("123456"),
            credential_id="identity-test-passkey",
            credential_public_key=b"identity-test-public-key",
            sign_count=0,
        )
        detail = self.service.district_school_detail("123456")
        self.assertEqual(detail["school_name"], "Official Elementary School")
        self.assertEqual(
            self.service.school_lookup("123456")["school_name"],
            "Official Elementary School",
        )

        self.service.record_heartbeat(
            installation_id=self.INSTALLATION_ID,
            installation_secret=registration.installation_secret,
            application_version="0.7.0",
            local_server_state="stopped",
            gateway_state="connected",
            survey_status="offline",
        )
        self.assertEqual(
            self.service.district_school_detail("123456")["health_status"],
            "attention",
        )

        self.service.record_heartbeat(
            installation_id=self.INSTALLATION_ID,
            installation_secret=registration.installation_secret,
            application_version="0.7.0",
            local_server_state="running",
            gateway_state="connected",
            survey_status="online",
            error_code="local_server_error",
        )
        self.assertEqual(
            self.service.district_school_detail("123456")["health_status"],
            "attention",
        )

    def test_suspension_blocks_activation_and_registration(self) -> None:
        self.service.save_directory_school(
            school_id="654321",
            school_name="Suspended School",
        )
        activation_code = self.service.issue_directory_activation_code("654321")
        suspended = self.service.set_directory_school_status("654321", "suspended")
        self.assertEqual(suspended["health_status"], "suspended")

        with self.assertRaisesRegex(ServiceError, "suspended") as issue_context:
            self.service.issue_directory_activation_code("654321")
        self.assertEqual(issue_context.exception.code, "school_suspended")

        with self.assertRaisesRegex(ServiceError, "suspended") as register_context:
            self.service.complete_initial_registration(
                school_id="654321",
                school_name="Suspended School",
                installation_id=self.INSTALLATION_ID,
                activation_code=activation_code,
                credential_id="suspended-passkey",
                credential_public_key=b"suspended-public-key",
                sign_count=0,
            )
        self.assertEqual(register_context.exception.code, "school_suspended")
        self.assertEqual(self.provisioner.revoked, ["tunnel-654321"])
        self.assertEqual(
            [item["school_id"] for item in self.service.list_district_schools(status="suspended")],
            ["654321"],
        )

    def test_heartbeat_rejects_invalid_credentials_and_unbounded_status_values(self) -> None:
        self.service.save_directory_school(
            school_id="123456",
            school_name="Example Elementary School",
        )
        code = self.service.issue_directory_activation_code("123456")
        registration = self.service.complete_initial_registration(
            school_id="123456",
            school_name="Example Elementary School",
            installation_id=self.INSTALLATION_ID,
            activation_code=code,
            credential_id="heartbeat-passkey",
            credential_public_key=b"heartbeat-public-key",
            sign_count=0,
        )

        with self.assertRaises(ServiceError) as credential_context:
            self.service.record_heartbeat(
                installation_id=self.INSTALLATION_ID,
                installation_secret="wrong-installation-secret",
                application_version="0.5.1",
                local_server_state="running",
                gateway_state="connected",
                survey_status="online",
            )
        self.assertEqual(credential_context.exception.code, "authorization_failed")

        with self.assertRaises(ServiceError) as payload_context:
            self.service.record_heartbeat(
                installation_id=self.INSTALLATION_ID,
                installation_secret=registration.installation_secret,
                application_version="0.5.1",
                local_server_state="running",
                gateway_state="unexpected-state",
                survey_status="online",
            )
        self.assertEqual(payload_context.exception.code, "invalid_heartbeat")

    def test_school_status_and_installation_credentials_are_isolated(self) -> None:
        registrations = []
        for school_id, installation_id in (
            ("123456", self.INSTALLATION_ID),
            ("654321", "33333333-3333-4333-8333-333333333333"),
        ):
            self.service.save_directory_school(school_id=school_id, school_name=f"School {school_id}")
            registrations.append(self.service.complete_initial_registration(
                school_id=school_id, school_name=f"School {school_id}",
                installation_id=installation_id,
                activation_code=self.service.issue_directory_activation_code(school_id),
                credential_id=f"passkey-{school_id}",
                credential_public_key=b"public-key", sign_count=0,
            ))
        for registration, survey_status in zip(registrations, ("maintenance", "online")):
            self.service.record_heartbeat(
                installation_id=registration.installation_id,
                installation_secret=registration.installation_secret,
                application_version="0.7.0", local_server_state="running",
                gateway_state="connected", survey_status=survey_status,
            )
        with self.assertRaises(ServiceError) as context:
            self.service.record_heartbeat(
                installation_id=registrations[1].installation_id,
                installation_secret=registrations[0].installation_secret,
                application_version="0.7.0", local_server_state="stopped",
                gateway_state="disconnected", survey_status="offline",
            )
        self.assertEqual(context.exception.code, "authorization_failed")
        self.assertEqual(self.service.district_school_detail("123456")["survey_status"], "maintenance")
        other = self.service.district_school_detail("654321")
        self.assertEqual(other["survey_status"], "online")
        self.assertEqual(other["health_status"], "connected")
        self.assertEqual([item["school_id"] for item in self.service.list_district_schools(status="maintenance")], ["123456"])

    def test_new_portal_activation_supersedes_an_earlier_unused_code(self) -> None:
        self.service.save_directory_school(
            school_id="246810",
            school_name="Replacement Code School",
        )
        first = self.service.issue_directory_activation_code("246810")
        second = self.service.issue_directory_activation_code("246810")
        self.assertNotEqual(first, second)
        with self.repository.read() as connection:
            first_row = connection.execute(
                "SELECT used_at FROM activation_codes WHERE code_hash=?",
                (hashlib.sha256(first.encode("utf-8")).hexdigest(),),
            ).fetchone()
            second_row = connection.execute(
                "SELECT used_at FROM activation_codes WHERE code_hash=?",
                (hashlib.sha256(second.encode("utf-8")).hexdigest(),),
            ).fetchone()
        self.assertIsNotNone(first_row)
        self.assertIsNotNone(first_row["used_at"])
        self.assertIsNotNone(second_row)
        self.assertIsNone(second_row["used_at"])

    def test_health_filter_applies_limit_after_classification(self) -> None:
        self.service.save_directory_school(
            school_id="111111",
            school_name="A Pending School",
        )
        self.service.save_directory_school(
            school_id="222222",
            school_name="B Connected School",
        )
        installation_id = "44444444-4444-4444-8444-444444444444"
        registration = self.service.complete_initial_registration(
            school_id="222222",
            school_name="B Connected School",
            installation_id=installation_id,
            activation_code=self.service.issue_directory_activation_code("222222"),
            credential_id="filtered-health-passkey",
            credential_public_key=b"filtered-health-public-key",
            sign_count=0,
        )
        self.service.record_heartbeat(
            installation_id=installation_id,
            installation_secret=registration.installation_secret,
            application_version="0.7.0",
            local_server_state="running",
            gateway_state="connected",
            survey_status="online",
        )
        connected = self.service.list_district_schools(status="connected", limit=1)
        self.assertEqual([item["school_id"] for item in connected], ["222222"])


@unittest.skipUnless(
    SERVICE_DEPS_AVAILABLE,
    "The independently deployed registration-service dependencies are not installed.",
)
class DistrictPortalAppTests(unittest.TestCase):
    ACCESS_TOKEN = "district-administrator-access-token-for-api-tests"
    INSTALLATION_ID = "22222222-2222-4222-8222-222222222222"

    def setUp(self) -> None:
        from fastapi.testclient import TestClient

        from registration_service.app import create_app

        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repository = RegistrationRepository(
            Path(self.temporary.name) / "registration.sqlite3"
        )
        self.provisioner = _Provisioner()
        self.service = RegistrationService(
            self.repository,
            managed_domain="csm.example.gov.ph",
            provisioner=self.provisioner,
        )
        digest = hashlib.sha256(self.ACCESS_TOKEN.encode("utf-8")).hexdigest()
        self.app = create_app(
            self.service,
            district_admin_token_sha256=digest,
        )
        self.environment = patch.dict(
            os.environ,
            {
                "SCHOOL_CSM_ALLOW_INSECURE_TEST_CLIENT": "1",
                "SCHOOL_CSM_WEBAUTHN_ORIGIN": "https://testserver",
                "SCHOOL_CSM_WEBAUTHN_RP_ID": "testserver",
            },
            clear=False,
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.client = TestClient(self.app, base_url="https://testserver")

    def _login(self) -> str:
        response = self.client.post(
            "/v1/admin/session",
            json={"access_token": self.ACCESS_TOKEN},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.login_cookie_header = response.headers.get("set-cookie", "")
        csrf_token = str(response.json().get("csrf_token") or "")
        self.assertGreaterEqual(len(csrf_token), 32)
        return csrf_token

    def test_district_page_and_assets_are_cache_safe_and_privacy_explicit(self) -> None:
        page = self.client.get("/district")
        self.assertEqual(page.status_code, 200, page.text)
        self.assertIn("School data stays local", page.text)
        self.assertIn("survey answers", page.text)
        self.assertIn('id="login-view" aria-labelledby="login-title" hidden', page.text)
        self.assertEqual(page.headers["cache-control"], "no-store")
        self.assertIn("frame-ancestors 'none'", page.headers["content-security-policy"])
        self.assertNotIn("unsafe-inline", page.headers["content-security-policy"])

        stylesheet = self.client.get("/assets/district.css")
        script = self.client.get("/assets/district.js")
        manage_page = self.client.get("/manage")
        manage_stylesheet = self.client.get("/assets/manage.css")
        self.assertEqual(stylesheet.status_code, 200, stylesheet.text)
        self.assertEqual(script.status_code, 200, script.text)
        self.assertEqual(manage_page.status_code, 200, manage_page.text)
        self.assertEqual(manage_stylesheet.status_code, 200, manage_stylesheet.text)
        self.assertIn("text/css", stylesheet.headers["content-type"])
        self.assertIn("text/css", manage_stylesheet.headers["content-type"])
        self.assertIn("javascript", script.headers["content-type"])
        self.assertIn('/assets/manage.css', manage_page.text)
        self.assertNotIn("<style", manage_page.text)
        self.assertNotIn(
            self.ACCESS_TOKEN,
            page.text + stylesheet.text + script.text + manage_page.text + manage_stylesheet.text,
        )

    def test_admin_session_cookie_and_csrf_fail_closed(self) -> None:
        unauthenticated = self.client.get("/v1/admin/overview")
        self.assertEqual(unauthenticated.status_code, 401, unauthenticated.text)

        rejected = self.client.post(
            "/v1/admin/session",
            json={"access_token": "wrong-district-administrator-token"},
        )
        self.assertEqual(rejected.status_code, 401, rejected.text)
        self.assertNotIn(SESSION_COOKIE, rejected.headers.get("set-cookie", ""))

        csrf_token = self._login()
        cookie_policy = self.login_cookie_header.casefold()
        self.assertIn(SESSION_COOKIE.casefold(), cookie_policy)
        self.assertIn("httponly", cookie_policy)
        self.assertIn("secure", cookie_policy)
        self.assertIn("samesite=strict", cookie_policy)

        current = self.client.get("/v1/admin/session")
        self.assertEqual(current.status_code, 200, current.text)
        self.assertTrue(current.json()["ok"])
        self.assertEqual(current.json()["role"], "district_administrator")
        self.assertEqual(current.json()["csrf_token"], csrf_token)

        missing_csrf = self.client.post(
            "/v1/admin/schools",
            json={"school_id": "123456", "school_name": "Example School"},
        )
        self.assertEqual(missing_csrf.status_code, 403, missing_csrf.text)
        self.assertNotIn(self.ACCESS_TOKEN, missing_csrf.text)

        logout = self.client.delete(
            "/v1/admin/session",
            headers={CSRF_HEADER: csrf_token},
        )
        self.assertIn(logout.status_code, {200, 204}, logout.text)
        after_logout = self.client.get("/v1/admin/overview")
        self.assertEqual(after_logout.status_code, 401, after_logout.text)

    def test_authorized_directory_activation_and_audit_workflow(self) -> None:
        csrf_token = self._login()
        csrf = {CSRF_HEADER: csrf_token}

        created = self.client.post(
            "/v1/admin/schools",
            headers=csrf,
            json={
                "school_id": "123456",
                "school_name": "Example Elementary School",
                "school_district": "Schools District of Motiong",
                "school_division": "Schools Division of Samar",
            },
        )
        self.assertIn(created.status_code, {200, 201}, created.text)
        self.assertEqual(
            self.service.district_school_detail("123456")["directory_status"],
            "approved",
        )

        overview = self.client.get("/v1/admin/overview")
        roster = self.client.get("/v1/admin/schools")
        detail = self.client.get("/v1/admin/schools/123456")
        self.assertEqual(overview.status_code, 200, overview.text)
        self.assertEqual(roster.status_code, 200, roster.text)
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertIn("123456", roster.text)
        self.assertIn("Example Elementary School", detail.text)

        issued = self.client.post(
            "/v1/admin/schools/123456/activation-codes",
            headers=csrf,
            json={"valid_days": 2},
        )
        self.assertEqual(issued.status_code, 200, issued.text)
        activation_code = str(issued.json().get("activation_code") or "")
        self.assertGreaterEqual(len(activation_code), 20)
        self.assertNotIn(activation_code.encode("utf-8"), Path(self.repository.path).read_bytes())

        missing_csrf = self.client.patch(
            "/v1/admin/schools/123456",
            json={"status": "suspended"},
        )
        self.assertEqual(missing_csrf.status_code, 403, missing_csrf.text)
        suspended = self.client.patch(
            "/v1/admin/schools/123456",
            headers=csrf,
            json={"status": "suspended"},
        )
        self.assertEqual(suspended.status_code, 200, suspended.text)
        self.assertEqual(
            self.service.district_school_detail("123456")["directory_status"],
            "suspended",
        )
        blocked = self.client.post(
            "/v1/admin/schools/123456/activation-codes",
            headers=csrf,
            json={"valid_days": 2},
        )
        self.assertEqual(blocked.status_code, 403, blocked.text)

        audit = self.client.get("/v1/admin/audit")
        self.assertEqual(audit.status_code, 200, audit.text)
        self.assertIn("district_school_saved", audit.text)
        self.assertIn("district_school_status_changed", audit.text)
        self.assertNotIn(activation_code, audit.text)
        self.assertNotIn(self.ACCESS_TOKEN, audit.text)

    def test_installation_heartbeat_uses_installation_credentials(self) -> None:
        self.service.save_directory_school(
            school_id="765432",
            school_name="Heartbeat Elementary School",
        )
        activation_code = self.service.issue_directory_activation_code("765432")
        registration = self.service.complete_initial_registration(
            school_id="765432",
            school_name="Heartbeat Elementary School",
            installation_id=self.INSTALLATION_ID,
            activation_code=activation_code,
            credential_id="heartbeat-api-passkey",
            credential_public_key=b"heartbeat-api-public-key",
            sign_count=0,
        )
        payload = {
            "application_version": "0.5.1",
            "local_server_state": "running",
            "gateway_state": "connected",
            "survey_status": "online",
            "error_code": "",
        }
        rejected = self.client.post(
            f"/v1/installations/{self.INSTALLATION_ID}/heartbeat",
            headers={"Authorization": "Bearer wrong-installation-secret"},
            json=payload,
        )
        self.assertEqual(rejected.status_code, 401, rejected.text)

        accepted = self.client.post(
            f"/v1/installations/{self.INSTALLATION_ID}/heartbeat",
            headers={"Authorization": f"Bearer {registration.installation_secret}"},
            json=payload,
        )
        self.assertEqual(accepted.status_code, 200, accepted.text)
        self.assertTrue(accepted.json()["ok"])
        self.assertNotIn(registration.installation_secret, accepted.text)
        self.assertEqual(
            self.service.district_school_detail("765432")["health_status"],
            "connected",
        )

    def test_registration_begin_uses_official_directory_name(self) -> None:
        self.service.save_directory_school(
            school_id="135790",
            school_name="Official District School",
        )
        activation_code = self.service.issue_directory_activation_code("135790")
        response = self.client.post(
            "/v1/registrations/begin",
            json={
                "school_id": "135790",
                "school_name": "Untrusted Browser Name",
                "installation_id": self.INSTALLATION_ID,
                "activation_code": activation_code,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["school_name"], "Official District School")
        self.assertNotIn("Untrusted Browser Name", response.text)


if __name__ == "__main__":
    unittest.main()
