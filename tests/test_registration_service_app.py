from __future__ import annotations

import base64
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


SERVICE_DEPS_AVAILABLE = bool(
    importlib.util.find_spec("fastapi") and importlib.util.find_spec("webauthn")
)


@unittest.skipUnless(
    SERVICE_DEPS_AVAILABLE,
    "The independently deployed registration-service dependencies are not installed.",
)
class RegistrationServiceAppTests(unittest.TestCase):
    def setUp(self) -> None:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from fastapi.testclient import TestClient

        from registration_service.app import create_app
        from registration_service.core import RegistrationRepository, RegistrationService
        from registration_service.handoff import CredentialHandoffVault
        from registration_service.signing import AuthorizationEnvelopeSigner

        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repository = RegistrationRepository(
            Path(self.temporary.name) / "registration.sqlite3"
        )

        class Provisioner:
            def provision(self, **_kwargs):
                return {"tunnel_id": "tunnel-one", "tunnel_token": "token-one"}

            def rotate(self, **_kwargs):
                return {"tunnel_id": "tunnel-two", "tunnel_token": "token-two"}

            def revoke(self, **_kwargs):
                return None

            def credential(self, *, tunnel_id, public_host, origin_port):
                return {"tunnel_id": tunnel_id, "tunnel_token": "existing-token"}

        service = RegistrationService(
            self.repository,
            managed_domain="csm.example.gov.ph",
            provisioner=Provisioner(),
            signer=AuthorizationEnvelopeSigner(
                Ed25519PrivateKey.generate(), key_id="test-key"
            ),
        )
        self.service = service
        self.app = create_app(service)
        self.app.state.handoff_vault = CredentialHandoffVault(
            self.repository, b"v" * 32
        )
        self.environment = patch.dict(
            os.environ,
            {
                "SCHOOL_CSM_ALLOW_INSECURE_TEST_CLIENT": "1",
                "SCHOOL_CSM_WEBAUTHN_ORIGIN": "https://registration.example.gov.ph",
                "SCHOOL_CSM_WEBAUTHN_RP_ID": "registration.example.gov.ph",
            },
            clear=False,
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.client = TestClient(self.app)

    def test_health_and_browser_passkey_page_have_security_headers(self) -> None:
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["service"], "school-csm-gateway-registration")
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertIn("frame-ancestors 'none'", response.headers["content-security-policy"])

        page = self.client.get("/manage")
        self.assertEqual(page.status_code, 200)
        self.assertIn("navigator.credentials", self.client.get("/assets/manage.js").text)
        self.assertNotIn("OpenAI", page.text)
        self.assertIn("does not receive School CSM survey responses", page.text)
        script = self.client.get("/assets/manage.js").text
        self.assertNotIn("data-verified", script)
        self.assertIn("intent_id", script)

    def test_browser_cannot_self_assert_migration_validation(self) -> None:
        response = self.client.post(
            "/v1/transfers/begin",
            json={
                "school_id": "123456",
                "destination_installation_id": "22222222-2222-4222-8222-222222222222",
                "transfer_mode": "server_and_data",
                "data_validation": {"verified": True},
            },
        )
        self.assertEqual(response.status_code, 422)

    def test_initial_registration_begin_returns_real_webauthn_options(self) -> None:
        code = self.repository.issue_activation_code("123456")
        response = self.client.post(
            "/v1/registrations/begin",
            json={
                "school_id": "123456",
                "school_name": "Example Elementary School",
                "installation_id": "11111111-1111-4111-8111-111111111111",
                "activation_code": code,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        document = response.json()
        self.assertTrue(document["ceremony_id"])
        options = document["public_key"]
        self.assertEqual(options["rp"]["id"], "registration.example.gov.ph")
        self.assertEqual(options["user"]["name"], "123456")
        self.assertTrue(options["challenge"])
        self.assertNotIn(code, response.text)

    def test_lost_passkey_reset_requires_admin_code_and_returns_webauthn_options(self) -> None:
        activation = self.repository.issue_activation_code("123456")
        self.service.complete_initial_registration(
            school_id="123456",
            school_name="Example Elementary School",
            installation_id="11111111-1111-4111-8111-111111111111",
            activation_code=activation,
            credential_id="cGFzc2tleS1vbmU",
            credential_public_key=b"test-public-key",
            sign_count=0,
        )
        rejected = self.client.post(
            "/v1/passkeys/reset/begin",
            json={"school_id": "123456", "reset_code": "not-an-admin-code"},
        )
        self.assertEqual(rejected.status_code, 400)
        reset_code = self.repository.issue_passkey_reset_code("123456")
        response = self.client.post(
            "/v1/passkeys/reset/begin",
            json={"school_id": "123456", "reset_code": reset_code},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["public_key"]["challenge"])
        self.assertNotIn(reset_code, response.text)

    def test_handoff_redeem_is_retryable_until_acknowledged_and_installation_bound(self) -> None:
        from registration_service.handoff import CredentialHandoffVault

        vault: CredentialHandoffVault = self.app.state.handoff_vault
        installation = "11111111-1111-4111-8111-111111111111"
        code = vault.issue(
            installation,
            {
                "school_id": "123456",
                "installation_id": installation,
                "registration_id": "registration-one",
                "public_host": "123456.csm.example.gov.ph",
                "tunnel_id": "tunnel-one",
                "installation_secret": "installation-secret",
                "tunnel_token": "tunnel-token",
            },
        )
        wrong = self.client.post(
            "/v1/handoffs/redeem",
            json={
                "completion_code": code,
                "installation_id": "22222222-2222-4222-8222-222222222222",
            },
        )
        self.assertEqual(wrong.status_code, 400)
        first = self.client.post(
            "/v1/handoffs/redeem",
            json={"completion_code": code, "installation_id": installation},
        )
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json()["installation_secret"], "installation-secret")
        second = self.client.post(
            "/v1/handoffs/redeem",
            json={"completion_code": code, "installation_id": installation},
        )
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(
            first.json()["acknowledgement_token"],
            second.json()["acknowledgement_token"],
        )
        wrong_acknowledgement = self.client.post(
            "/v1/handoffs/acknowledge",
            json={
                "completion_code": code,
                "installation_id": installation,
                "acknowledgement_token": "x" * 43,
            },
        )
        self.assertEqual(wrong_acknowledgement.status_code, 400)
        acknowledgement = self.client.post(
            "/v1/handoffs/acknowledge",
            json={
                "completion_code": code,
                "installation_id": installation,
                "acknowledgement_token": first.json()["acknowledgement_token"],
            },
        )
        self.assertEqual(acknowledgement.status_code, 200, acknowledgement.text)
        self.assertTrue(acknowledgement.json()["acknowledged"])
        repeated = self.client.post(
            "/v1/handoffs/acknowledge",
            json={
                "completion_code": code,
                "installation_id": installation,
                "acknowledgement_token": first.json()["acknowledgement_token"],
            },
        )
        self.assertEqual(repeated.status_code, 200, repeated.text)
        self.assertTrue(repeated.json()["already_acknowledged"])
        consumed = self.client.post(
            "/v1/handoffs/redeem",
            json={"completion_code": code, "installation_id": installation},
        )
        self.assertEqual(consumed.status_code, 400)

    def test_installation_authentication_is_throttled_before_repeated_scrypt(self) -> None:
        installation = "77777777-7777-4777-8777-777777777777"
        activation = self.repository.issue_activation_code("765432")
        self.service.complete_initial_registration(
            school_id="765432",
            school_name="Rate Limit School",
            installation_id=installation,
            activation_code=activation,
            credential_id="rate-limit-passkey",
            credential_public_key=b"test-public-key",
            sign_count=0,
        )
        url = f"/v1/installations/{installation}/authorization?school_id=765432"
        for attempt in range(12):
            response = self.client.get(
                url, headers={"Authorization": f"Bearer wrong-secret-{attempt}"}
            )
            self.assertEqual(response.status_code, 401, response.text)
        throttled = self.client.get(
            url, headers={"Authorization": "Bearer wrong-secret-final"}
        )
        self.assertEqual(throttled.status_code, 429, throttled.text)
        self.assertIn("Too many installation", throttled.json()["detail"])

    def test_unknown_handoff_paths_cannot_churn_rate_limit_identities(self) -> None:
        for attempt in range(12):
            response = self.client.post(
                f"/v1/handoffs/not-a-route-{attempt}", json={}
            )
            self.assertEqual(response.status_code, 404, response.text)
        throttled = self.client.post("/v1/handoffs/not-a-route-final", json={})
        self.assertEqual(throttled.status_code, 429, throttled.text)

    def test_json_body_and_size_policy_fail_closed(self) -> None:
        media = self.client.post(
            "/v1/handoffs/redeem",
            content="not-json",
            headers={"Content-Type": "text/plain"},
        )
        self.assertEqual(media.status_code, 415)
        oversized = self.client.post(
            "/v1/handoffs/redeem",
            content=b"{}",
            headers={
                "Content-Type": "application/json",
                "Content-Length": str(1024 * 1024 + 1),
            },
        )
        self.assertEqual(oversized.status_code, 413)


if __name__ == "__main__":
    unittest.main()
