from __future__ import annotations

import json
from pathlib import Path
import re
import tempfile
import unittest

from registration_service.cloudflare import (
    CloudflareTunnelProvisioner,
    CloudflareWorkersVPCProvisioner,
)


class _RecordedCloudflare(CloudflareTunnelProvisioner):
    def __init__(self) -> None:
        super().__init__(
            account_id="account",
            zone_id="zone",
            api_token="secret-api-token",
            origin_url="http://127.0.0.1:8080",
        )
        self.calls: list[tuple[str, str, object]] = []
        self.created = 0

    def _request(self, method, path, payload=None):
        self.calls.append((method, path, payload))
        if method == "POST" and path.endswith("/cfd_tunnel"):
            self.created += 1
            return {"id": f"tunnel-{self.created}"}
        if method == "GET" and path.endswith("/token"):
            return "connector-token"
        if method == "GET" and "/dns_records?" in path:
            return []
        return {}


class CloudflareProvisionerTests(unittest.TestCase):
    def test_rotation_moves_route_but_revocation_is_post_commit(self) -> None:
        provider = _RecordedCloudflare()
        rotated = provider.rotate(
            tunnel_id="source-tunnel",
            school_id="123456",
            public_host="123456.csm.example.gov.ph",
        )
        self.assertEqual(rotated["tunnel_id"], "tunnel-1")
        called_paths = [path for _method, path, _payload in provider.calls]
        self.assertFalse(any("source-tunnel/connections" in path for path in called_paths))
        self.assertFalse(any(path.endswith("/source-tunnel") for path in called_paths))

        provider.revoke(tunnel_id="source-tunnel")
        self.assertTrue(provider.calls[-2][1].endswith("/source-tunnel/connections"))
        self.assertTrue(provider.calls[-1][1].endswith("/source-tunnel"))

    def test_failed_cutover_can_restore_dns_before_deleting_replacement(self) -> None:
        provider = _RecordedCloudflare()
        provider.rollback_rotation(
            source_tunnel_id="source-tunnel",
            replacement_tunnel_id="replacement-tunnel",
            public_host="123456.csm.example.gov.ph",
        )
        dns_call = next(
            index
            for index, (method, path, _payload) in enumerate(provider.calls)
            if method == "POST" and path.endswith("/dns_records")
        )
        disconnect_call = next(
            index
            for index, (_method, path, _payload) in enumerate(provider.calls)
            if path.endswith("/replacement-tunnel/connections")
        )
        self.assertLess(dns_call, disconnect_call)
        dns_payload = provider.calls[dns_call][2]
        self.assertEqual(dns_payload["content"], "source-tunnel.cfargotunnel.com")

    def test_rollback_does_not_depend_on_reconfiguring_the_source_tunnel(self) -> None:
        class _NoReconfigureProvider(_RecordedCloudflare):
            def _configure(self, _tunnel_id, _public_host, **_kwargs):
                raise AssertionError("the already-active source must not be reconfigured")

        provider = _NoReconfigureProvider()
        provider.rollback_rotation(
            source_tunnel_id="source-tunnel",
            replacement_tunnel_id="replacement-tunnel",
            public_host="123456.csm.example.gov.ph",
        )
        self.assertTrue(
            any(
                payload and payload.get("content") == "source-tunnel.cfargotunnel.com"
                for _method, _path, payload in provider.calls
            )
        )


class _RecordedWorkersVPC(CloudflareWorkersVPCProvisioner):
    def __init__(self, worker: Path) -> None:
        super().__init__(
            account_id="a" * 32,
            api_token="scoped-api-token-value",
            worker_source=worker,
            compatibility_date="2026-09-30",
        )
        self.calls: list[tuple[str, str, object]] = []
        self.services: list[dict] = []
        self.created = 0
        self.bound_service = ""

    def _request(self, method, path, payload=None):
        self.calls.append((method, path, payload))
        if method == "POST" and path.endswith("/cfd_tunnel"):
            self.created += 1
            return {"id": f"00000000-0000-0000-0000-{self.created:012d}"}
        if method == "POST" and path.endswith("/connectivity/directory/services"):
            service = dict(payload)
            service["service_id"] = f"10000000-0000-0000-0000-{len(self.services) + 1:012d}"
            self.services.append(service)
            return service
        if method == "GET" and "/connectivity/directory/services?" in path:
            return list(self.services)
        if method == "GET" and path.endswith("/workers/scripts"):
            return []
        if method == "PUT" and "/connectivity/directory/services/" in path:
            service_id = path.rsplit("/", 1)[-1]
            for index, service in enumerate(self.services):
                if service.get("service_id") == service_id:
                    replacement = dict(payload)
                    replacement["service_id"] = service_id
                    self.services[index] = replacement
                    return replacement
            return {}
        if method == "DELETE" and "/connectivity/directory/services/" in path:
            service_id = path.rsplit("/", 1)[-1]
            self.services = [s for s in self.services if s.get("service_id") != service_id]
            return {}
        if method == "GET" and path.endswith("/settings"):
            return {
                "bindings": [
                    {
                        "type": "vpc_service",
                        "name": "SCHOOL_CSM_ORIGIN",
                        "service_id": self.bound_service,
                    }
                ]
            }
        if method == "GET" and path.endswith("/token"):
            return "connector-token"
        return {}

    def _request_body(self, method, path, *, body, extra_headers=None):
        self.calls.append((method, path, body))
        if method == "PUT" and "/workers/scripts/" in path:
            match = re.search(br'\{"main_module".*?\}\r\n--', body)
            if not match:
                raise AssertionError("multipart metadata was not found")
            metadata = json.loads(match.group(0)[:-4].decode("utf-8"))
            binding = next(
                row for row in metadata["bindings"] if row["type"] == "vpc_service"
            )
            self.bound_service = binding["service_id"]
        return {}


class WorkersVPCProvisionerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        worker = Path(self.temporary.name) / "index.js"
        worker.write_text("export default { fetch() { return new Response('ok'); } };", encoding="utf-8")
        self.provider = _RecordedWorkersVPC(worker)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_provision_creates_narrow_vpc_service_and_ordinary_worker(self) -> None:
        result = self.provider.provision(
            school_id="123627",
            public_host="123627.motiong-district-csm-survey.workers.dev",
        )
        self.assertEqual(result["tunnel_token"], "connector-token")
        service = self.provider.services[0]
        self.assertEqual(service["type"], "http")
        self.assertEqual(service["http_port"], 8080)
        self.assertEqual(service["host"]["ipv4"], "127.0.0.1")
        self.assertEqual(
            service["host"]["network"]["tunnel_id"], result["tunnel_id"]
        )
        paths = [path for _method, path, _payload in self.provider.calls]
        self.assertTrue(any(path.endswith("/workers/scripts/123627") for path in paths))
        self.assertTrue(any(path.endswith("/workers/scripts/123627/subdomain") for path in paths))
        self.assertFalse(any("dns_records" in path for path in paths))

    def test_initial_provision_refuses_to_replace_an_existing_worker(self) -> None:
        original_request = self.provider._request

        def request(method, path, payload=None):
            if method == "GET" and path.endswith("/workers/scripts"):
                return [{"id": "123627"}]
            return original_request(method, path, payload)

        self.provider._request = request
        with self.assertRaisesRegex(Exception, "already exists"):
            self.provider.provision(
                school_id="123627",
                public_host="123627.motiong-district-csm-survey.workers.dev",
            )
        self.assertEqual(self.provider.created, 0)
        self.assertEqual(self.provider.services, [])

    def test_credential_updates_only_the_fixed_service_port(self) -> None:
        provisioned = self.provider.provision(
            school_id="123627",
            public_host="123627.motiong-district-csm-survey.workers.dev",
        )
        self.provider.credential(
            tunnel_id=provisioned["tunnel_id"],
            public_host="123627.motiong-district-csm-survey.workers.dev",
            origin_port=8099,
        )
        self.assertEqual(self.provider.services[0]["http_port"], 8099)
        self.assertEqual(self.provider.services[0]["host"]["ipv4"], "127.0.0.1")

    def test_rotation_rebinds_worker_and_rollback_restores_source(self) -> None:
        source = self.provider.provision(
            school_id="123627",
            public_host="123627.motiong-district-csm-survey.workers.dev",
        )
        source_service = self.provider.services[0]["service_id"]
        replacement = self.provider.rotate(
            tunnel_id=source["tunnel_id"],
            school_id="123627",
            public_host="123627.motiong-district-csm-survey.workers.dev",
        )
        self.assertNotEqual(self.provider.bound_service, source_service)
        self.provider.rollback_rotation(
            source_tunnel_id=source["tunnel_id"],
            replacement_tunnel_id=replacement["tunnel_id"],
            public_host="123627.motiong-district-csm-survey.workers.dev",
        )
        self.assertEqual(self.provider.bound_service, source_service)


if __name__ == "__main__":
    unittest.main()
