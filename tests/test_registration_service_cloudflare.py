from __future__ import annotations

import unittest

from registration_service.cloudflare import CloudflareTunnelProvisioner


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


if __name__ == "__main__":
    unittest.main()
