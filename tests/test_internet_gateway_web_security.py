from __future__ import annotations

from http.client import HTTPConnection
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
import unittest
from unittest.mock import patch

from school_csm_control_center.storage.survey_store import SurveyStore
from school_csm_control_center.web_server.internet_security import (
    BoundedRateLimiter,
    InternetRequestPolicy,
    InternetRequestRejected,
)
from school_csm_control_center.web_server.server import SurveyHTTPServer


PUBLIC_HOST = "123627.csm.example.gov.ph"


class InternetGatewayPolicyTests(unittest.TestCase):
    def test_loopback_is_not_implicitly_a_trusted_proxy(self) -> None:
        policy = InternetRequestPolicy.from_settings(
            {
                "internet_gateway_enabled": True,
                "internet_gateway_public_host": PUBLIC_HOST,
                "internet_gateway_trusted_proxy_ips": [],
                "school_id": "123627",
            }
        )
        with self.assertRaises(InternetRequestRejected) as rejected:
            policy.classify(
                peer_ip="127.0.0.1",
                host=PUBLIC_HOST,
                forwarded_proto="https",
                forwarding_headers_present=True,
            )
        self.assertEqual(rejected.exception.code, "untrusted_gateway_proxy")

    def test_forwarded_headers_are_rejected_from_an_untrusted_peer(self) -> None:
        policy = InternetRequestPolicy.from_settings(
            {
                "internet_gateway_enabled": True,
                "internet_gateway_public_host": PUBLIC_HOST,
                "internet_gateway_trusted_proxy_ips": ["10.20.30.40"],
                "school_id": "123627",
            }
        )
        with self.assertRaises(InternetRequestRejected) as rejected:
            policy.classify(
                peer_ip="192.168.1.50",
                host=PUBLIC_HOST,
                forwarded_proto="https",
                forwarding_headers_present=True,
            )
        self.assertEqual(rejected.exception.code, "untrusted_gateway_proxy")

    def test_public_host_must_match_the_configured_school_id(self) -> None:
        policy = InternetRequestPolicy.from_settings(
            {
                "internet_gateway_enabled": True,
                "internet_gateway_public_host": "999999.csm.example.gov.ph",
                "school_id": "123627",
            }
        )
        self.assertFalse(policy.configuration_valid)
        with self.assertRaises(InternetRequestRejected) as rejected:
            policy.classify(
                peer_ip="127.0.0.1",
                host="999999.csm.example.gov.ph",
                forwarded_proto="https",
                forwarding_headers_present=True,
            )
        self.assertEqual(rejected.exception.code, "gateway_not_configured")

    def test_public_https_host_rejects_nonstandard_forwarded_port(self) -> None:
        policy = InternetRequestPolicy.from_settings(
            {
                "internet_gateway_enabled": True,
                "internet_gateway_public_host": PUBLIC_HOST,
                "internet_gateway_trusted_proxy_ips": ["127.0.0.1"],
                "school_id": "123627",
            }
        )
        accepted = policy.classify(
            peer_ip="127.0.0.1",
            host="127.0.0.1:8080",
            forwarded_host=f"{PUBLIC_HOST}:443",
            forwarded_proto="https",
            forwarding_headers_present=True,
        )
        self.assertTrue(accepted.internet)
        with self.assertRaises(InternetRequestRejected) as rejected:
            policy.classify(
                peer_ip="127.0.0.1",
                host="127.0.0.1:8080",
                forwarded_host=f"{PUBLIC_HOST}:8443",
                forwarded_proto="https",
                forwarding_headers_present=True,
            )
        self.assertEqual(rejected.exception.code, "school_host_mismatch")

    def test_rate_limiter_is_bounded_and_does_not_retain_plain_identities(self) -> None:
        now = [100.0]
        limiter = BoundedRateLimiter(
            max_identities=64,
            secret=b"test-only-secret",
            clock=lambda: now[0],
        )
        first = limiter.consume("survey_submit", "198.51.100.25", limit=1, window_seconds=60)
        blocked = limiter.consume("survey_submit", "198.51.100.25", limit=1, window_seconds=60)
        self.assertTrue(first.allowed)
        self.assertFalse(blocked.allowed)
        self.assertEqual(blocked.retry_after_seconds, 60)
        for number in range(100):
            limiter.consume("scanner_login", f"client-{number}", limit=1, window_seconds=60)
        self.assertLessEqual(limiter.retained_identity_count, 64)
        self.assertNotIn("198.51.100.25", repr(limiter._entries))

    def test_rate_limiter_retry_does_not_exceed_its_configured_window(self) -> None:
        now = [1000.4]
        limiter = BoundedRateLimiter(
            max_identities=64,
            secret=b"test-only-secret",
            clock=lambda: now[0],
        )

        first = limiter.consume(
            "public_session",
            "198.51.100.25",
            limit=1,
            window_seconds=60,
        )
        blocked = limiter.consume(
            "public_session",
            "198.51.100.25",
            limit=1,
            window_seconds=60,
        )

        self.assertTrue(first.allowed)
        self.assertFalse(blocked.allowed)
        self.assertEqual(blocked.retry_after_seconds, 60)


class InternetGatewayWebSecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = SurveyStore(self.root)
        static_root = (
            Path(__file__).parents[1]
            / "school_csm_control_center"
            / "web_server"
            / "static"
        )
        self.settings = {
            "active_mode": "onsite",
            "survey_status": "online",
            "survey_date": "2026-08-13",
            "school_name": "Gateway Test School",
            "school_id": "123627",
            "school_division": "Test Division",
            "school_district": "Test District",
            "school_address": "Private LAN office address",
            "school_email": "private@example.invalid",
            "school_contact": "private-contact",
            "school_head": "Private Person",
            "csm_focal_person": "Private Person",
            "public_access_key": "gateway-test-key",
            "session_duration_seconds": 300,
            "local_ip": "127.0.0.1",
            "named_hostname": "csm.gateway-test.home.arpa",
            "network_mode": "hotspot",
            "access_mode": "captive_portal",
            "captive_portal_enabled": True,
            "scanner_remote_enabled": True,
            "scanner_intake_enabled": True,
            "internet_gateway_enabled": True,
            "internet_gateway_public_host": PUBLIC_HOST,
            "internet_gateway_trusted_proxy_ips": ["127.0.0.1"],
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
        self.port = int(self.server.server_address[1])

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    @property
    def internet_headers(self) -> dict[str, str]:
        return {
            "Host": PUBLIC_HOST,
            "X-Forwarded-Proto": "https",
            "X-Forwarded-For": "198.51.100.25",
            "Origin": f"https://{PUBLIC_HOST}",
        }

    def request(
        self,
        path: str,
        *,
        method: str = "GET",
        payload: dict | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict, object]:
        connection = HTTPConnection("127.0.0.1", self.port, timeout=8)
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request_headers = dict(headers or {})
        if body is not None:
            request_headers["Content-Type"] = "application/json"
            request_headers["Content-Length"] = str(len(body))
        connection.request(method, path, body=body, headers=request_headers)
        response = connection.getresponse()
        raw = response.read()
        status = response.status
        response_headers = response.headers
        connection.close()
        parsed = json.loads(raw.decode("utf-8")) if raw else {}
        return status, parsed, response_headers

    def begin_public_session(self) -> str:
        status, _payload, headers = self.request(
            "/access/gateway-test-key",
            headers=self.internet_headers,
        )
        self.assertEqual(status, 302)
        cookie = str(headers.get("Set-Cookie") or "")
        self.assertIn("Secure", cookie)
        self.assertIn("HttpOnly", cookie)
        return cookie.split(";", 1)[0]

    @staticmethod
    def survey_payload(token: str = "internet-transport-test") -> dict:
        return {
            "mode": "onsite",
            "submission_token": token,
            "meta": {
                "client_type": "Citizen",
                "sex": "Male",
                "age": 30,
                "region": "region_08",
                "service_availed": ["Enrollment (Walk-in)"],
                "school_id": "999999",
                "access_transport": "local",
                "submission_source": "manual_entry",
            },
            "cc": {"cc1": 4, "cc2": 5, "cc3": 4},
            "sqd": {f"sqd{number}": 5 for number in range(9)},
            "feedback": {"comments": "Internet response", "email": ""},
            "privacy_acknowledgement": {
                "accepted": True,
                "notice_version": "2026-08-14-internet-gateway-1",
            },
        }

    def test_local_session_behavior_is_unchanged_and_cookie_is_not_secure(self) -> None:
        status, _payload, headers = self.request(
            "/access/gateway-test-key",
            headers={"Host": f"127.0.0.1:{self.port}"},
        )
        self.assertEqual(status, 302)
        cookie = str(headers.get("Set-Cookie") or "")
        self.assertIn("HttpOnly", cookie)
        self.assertNotIn("Secure", cookie)

    def test_public_captive_route_cannot_bypass_the_access_key(self) -> None:
        status, payload, headers = self.request("/portal", headers=self.internet_headers)
        self.assertEqual(status, 403)
        self.assertEqual(payload["code"], "local_captive_portal_only")
        self.assertIsNone(headers.get("Set-Cookie"))

    def test_public_host_mismatch_is_rejected_before_route_dispatch(self) -> None:
        headers = dict(self.internet_headers)
        headers["Host"] = "999999.csm.example.gov.ph"
        status, payload, _headers = self.request("/healthz", headers=headers)
        self.assertEqual(status, 421)
        self.assertEqual(payload["code"], "school_host_mismatch")

    def test_keep_alive_connection_reauthorizes_each_request(self) -> None:
        connection = HTTPConnection("127.0.0.1", self.port, timeout=8)
        connection.request("GET", "/healthz", headers=self.internet_headers)
        first = connection.getresponse()
        self.assertEqual(first.status, 200)
        first.read()

        rejected_headers = dict(self.internet_headers)
        rejected_headers["Host"] = "999999.csm.example.gov.ph"
        connection.request("GET", "/healthz", headers=rejected_headers)
        second = connection.getresponse()
        payload = json.loads(second.read().decode("utf-8"))
        connection.close()

        self.assertEqual(second.status, 421)
        self.assertEqual(payload["code"], "school_host_mismatch")

    def test_public_config_contains_public_links_but_no_lan_or_private_contacts(self) -> None:
        cookie = self.begin_public_session()
        headers = {**self.internet_headers, "Cookie": cookie}
        status, config, _response_headers = self.request("/api/config", headers=headers)
        self.assertEqual(status, 200)
        self.assertEqual(config["access_transport"], "internet")
        self.assertEqual(config["survey_address"], f"https://{PUBLIC_HOST}")
        self.assertEqual(config["direct_address"], "")
        self.assertEqual(config["scanner_url"], f"https://{PUBLIC_HOST}/scanner")
        serialized = json.dumps(config)
        self.assertNotIn("127.0.0.1", serialized)
        self.assertNotIn("home.arpa", serialized)
        self.assertNotIn("private@example.invalid", serialized)
        self.assertNotIn("Private Person", serialized)
        self.assertNotIn("school_email", config)
        self.assertNotIn("school_contact", config)
        self.assertNotIn("school_head", config)
        self.assertNotIn("csm_focal_person", config)
        self.assertIn("transmitted securely", config["privacy_notice"])
        self.assertIn("authoritative response storage remains", config["privacy_notice"])
        self.assertIn("minimum infrastructure metadata", config["privacy_notice"])
        self.assertIn("does not store CSM responses", config["privacy_notice"])
        self.assertIn("not a CSM response database", config["privacy_notice"])
        self.assertEqual(
            config["privacy_notice_version"],
            "2026-08-14-internet-gateway-1",
        )

    def test_browser_cannot_choose_school_or_transport_metadata(self) -> None:
        cookie = self.begin_public_session()
        payload = self.survey_payload()
        status, result, _headers = self.request(
            "/api/submit",
            method="POST",
            payload=payload,
            headers={**self.internet_headers, "Cookie": cookie},
        )
        self.assertEqual(status, 201, result)
        saved = self.store.list()[0]
        self.assertEqual(saved["source_code"], "WBS")
        self.assertEqual(saved["meta"]["submission_source"], "local_web_form")
        self.assertEqual(saved["meta"]["access_transport"], "internet")
        self.assertEqual(saved["meta"]["school_id"], "123627")
        self.assertEqual(
            saved["meta"]["privacy_notice_version"],
            "2026-08-14-internet-gateway-1",
        )

    def test_public_posts_require_the_exact_same_origin(self) -> None:
        cookie = self.begin_public_session()
        headers = dict(self.internet_headers)
        headers.pop("Origin")
        status, payload, _response_headers = self.request(
            "/api/submit",
            method="POST",
            payload=self.survey_payload("missing-origin"),
            headers={**headers, "Cookie": cookie},
        )
        self.assertEqual(status, 403)
        self.assertEqual(payload["code"], "csrf_origin_required")

        headers["Origin"] = "https://attacker.example"
        status, payload, _response_headers = self.request(
            "/api/submit",
            method="POST",
            payload=self.survey_payload("wrong-origin"),
            headers={**headers, "Cookie": cookie},
        )
        self.assertEqual(status, 403)
        self.assertEqual(payload["code"], "csrf_origin_mismatch")

    def test_public_responses_have_security_headers_and_no_wildcard_cors(self) -> None:
        status, _payload, headers = self.request("/healthz", headers=self.internet_headers)
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Cache-Control"), "no-store")
        self.assertEqual(headers.get("Referrer-Policy"), "no-referrer")
        self.assertEqual(headers.get("X-Frame-Options"), "DENY")
        self.assertIn("frame-ancestors 'none'", str(headers.get("Content-Security-Policy") or ""))
        self.assertEqual(headers.get("Strict-Transport-Security"), "max-age=31536000")
        self.assertIsNone(headers.get("Access-Control-Allow-Origin"))

        status, _payload, headers = self.request(
            "/api/scanner/submissions",
            method="OPTIONS",
            headers=self.internet_headers,
        )
        self.assertEqual(status, 204)
        self.assertIsNone(headers.get("Access-Control-Allow-Origin"))

    def test_public_rate_limits_cover_submit_login_jobs_and_upload(self) -> None:
        self.settings["internet_gateway_rate_limits"] = {
            "survey_submit": {"limit": 1, "window_seconds": 60},
            "scanner_login": {"limit": 1, "window_seconds": 60},
            "scanner_jobs": {"limit": 20, "window_seconds": 60},
            "scanner_upload": {"limit": 1, "window_seconds": 60},
        }
        cookie = self.begin_public_session()
        headers = {**self.internet_headers, "Cookie": cookie}
        first_status, _first, _headers = self.request(
            "/api/submit", method="POST", payload=self.survey_payload("rate-first"), headers=headers
        )
        self.assertEqual(first_status, 201)
        status, payload, response_headers = self.request(
            "/api/submit", method="POST", payload=self.survey_payload("rate-second"), headers=headers
        )
        self.assertEqual(status, 429)
        self.assertEqual(payload["code"], "rate_limit_exceeded")
        self.assertEqual(response_headers.get("Retry-After"), "60")

        login_payload = {"username": "missing", "password": "wrong"}
        self.request(
            "/api/scanner/auth/login",
            method="POST",
            payload=login_payload,
            headers=self.internet_headers,
        )
        status, payload, _headers = self.request(
            "/api/scanner/auth/login",
            method="POST",
            payload=login_payload,
            headers=self.internet_headers,
        )
        self.assertEqual(status, 429)
        self.assertEqual(payload["code"], "rate_limit_exceeded")

        first_status, _payload, _headers = self.request(
            "/api/scanner/jobs",
            method="POST",
            payload={"image_data_url": ""},
            headers=self.internet_headers,
        )
        self.assertEqual(first_status, 401)
        status, payload, _headers = self.request(
            "/api/scanner/jobs",
            method="POST",
            payload={"image_data_url": ""},
            headers=self.internet_headers,
        )
        self.assertEqual(status, 429)
        self.assertEqual(payload["code"], "rate_limit_exceeded")

    def test_arbitrary_scanner_cookie_does_not_reset_public_upload_limit(self) -> None:
        self.settings["internet_gateway_rate_limits"] = {
            "scanner_jobs": {"limit": 20, "window_seconds": 60},
            "scanner_upload": {"limit": 1, "window_seconds": 60},
        }
        first_headers = {
            **self.internet_headers,
            "Cookie": "SCHOOL_CSM_SCANNER_SESSION=attacker-selected-one",
        }
        second_headers = {
            **self.internet_headers,
            "Cookie": "SCHOOL_CSM_SCANNER_SESSION=attacker-selected-two",
        }
        first_status, _payload, _headers = self.request(
            "/api/scanner/jobs",
            method="POST",
            payload={"image_data_url": ""},
            headers=first_headers,
        )
        self.assertEqual(first_status, 401)
        status, payload, _headers = self.request(
            "/api/scanner/jobs",
            method="POST",
            payload={"image_data_url": ""},
            headers=second_headers,
        )
        self.assertEqual(status, 429)
        self.assertEqual(payload["code"], "rate_limit_exceeded")

    def test_public_access_session_issuance_is_bounded_per_client_fingerprint(self) -> None:
        self.settings["internet_gateway_rate_limits"] = {
            "access_session": {"limit": 2, "window_seconds": 60},
        }
        cookies: list[str] = []
        for _attempt in range(2):
            status, _payload, headers = self.request(
                "/access/gateway-test-key",
                headers=self.internet_headers,
            )
            self.assertEqual(status, 302)
            cookies.append(str(headers.get("Set-Cookie") or ""))

        status, payload, headers = self.request(
            "/access/gateway-test-key",
            headers=self.internet_headers,
        )
        self.assertEqual(status, 429)
        self.assertEqual(payload["code"], "rate_limit_exceeded")
        self.assertEqual(headers.get("Retry-After"), "60")
        self.assertIsNone(headers.get("Set-Cookie"))
        self.assertEqual(len(self.server.sessions), 2)
        self.assertEqual(len(set(cookies)), 2)

        other_client_headers = {
            **self.internet_headers,
            "X-Forwarded-For": "198.51.100.26",
        }
        status, _payload, _headers = self.request(
            "/access/gateway-test-key",
            headers=other_client_headers,
        )
        self.assertEqual(status, 302)

    def test_new_public_cookie_does_not_reset_submit_rate_limit(self) -> None:
        self.settings["internet_gateway_rate_limits"] = {
            "access_session": {"limit": 10, "window_seconds": 60},
            "survey_submit": {"limit": 1, "window_seconds": 60},
        }
        first_cookie = self.begin_public_session()
        first_status, _payload, _headers = self.request(
            "/api/submit",
            method="POST",
            payload=self.survey_payload("fingerprint-rate-first"),
            headers={**self.internet_headers, "Cookie": first_cookie},
        )
        self.assertEqual(first_status, 201)

        replacement_cookie = self.begin_public_session()
        self.assertNotEqual(first_cookie, replacement_cookie)
        status, payload, headers = self.request(
            "/api/submit",
            method="POST",
            payload=self.survey_payload("fingerprint-rate-second"),
            headers={**self.internet_headers, "Cookie": replacement_cookie},
        )
        self.assertEqual(status, 429)
        self.assertEqual(payload["code"], "rate_limit_exceeded")
        self.assertEqual(headers.get("Retry-After"), "60")
        self.assertEqual(len(self.store.list()), 1)

    def test_survey_session_store_evicts_expired_then_oldest_entries(self) -> None:
        self.server.max_active_survey_sessions = 3
        expired_token, _duration = self.server.create_session()
        with self.server.session_lock:
            self.server.sessions[expired_token] = 0

        live_tokens = [self.server.create_session()[0] for _number in range(3)]
        self.assertNotIn(expired_token, self.server.sessions)
        self.assertEqual(list(self.server.sessions), live_tokens)

        newest_token, _duration = self.server.create_session()
        self.assertEqual(len(self.server.sessions), 3)
        self.assertNotIn(live_tokens[0], self.server.sessions)
        self.assertEqual(list(self.server.sessions), [*live_tokens[1:], newest_token])

    def test_local_access_is_not_subject_to_public_session_issuance_limit(self) -> None:
        self.settings["internet_gateway_rate_limits"] = {
            "access_session": {"limit": 1, "window_seconds": 60},
        }
        local_headers = {"Host": f"127.0.0.1:{self.port}"}
        for _attempt in range(3):
            status, _payload, headers = self.request(
                "/access/gateway-test-key",
                headers=local_headers,
            )
            self.assertEqual(status, 302)
            self.assertNotIn("Secure", str(headers.get("Set-Cookie") or ""))

    def test_unexpected_save_failure_returns_a_generic_500(self) -> None:
        cookie = self.begin_public_session()
        with patch.object(
            self.store,
            "add_with_next_control_number",
            side_effect=RuntimeError("secret database path"),
        ):
            status, payload, _headers = self.request(
                "/api/submit",
                method="POST",
                payload=self.survey_payload("generic-500"),
                headers={**self.internet_headers, "Cookie": cookie},
            )
        self.assertEqual(status, 500)
        self.assertEqual(payload["code"], "internal_server_error")
        self.assertNotIn("secret database path", json.dumps(payload))


if __name__ == "__main__":
    unittest.main()
