from __future__ import annotations

from http.client import HTTPConnection
from pathlib import Path
import socket
import struct
from tempfile import TemporaryDirectory
import unittest

from school_csm_control_center.web_server.captive_portal import (
    CAPTIVE_PROBE_HOSTS,
    CaptivePortalService,
    CaptivePortalStatus,
    HOSTS_BEGIN,
    HOSTS_END,
    build_dns_response,
    configure_probe_hosts,
    remove_probe_hosts,
)


def dns_query(hostname: str, qtype: int = 1) -> bytes:
    transaction = b"\x12\x34"
    header = transaction + struct.pack("!HHHHH", 0x0100, 1, 0, 0, 0)
    labels = b"".join(bytes([len(part)]) + part.encode("ascii") for part in hostname.split(".")) + b"\x00"
    return header + labels + struct.pack("!HH", qtype, 1)


class CaptivePortalTests(unittest.TestCase):
    def test_dns_a_query_maps_to_hotspot_ip(self) -> None:
        response = build_dns_response(dns_query("connectivitycheck.gstatic.com"), "192.168.137.1")
        self.assertEqual(response[:2], b"\x12\x34")
        self.assertEqual(struct.unpack("!H", response[6:8])[0], 1)
        self.assertTrue(response.endswith(socket.inet_aton("192.168.137.1")))

    def test_dns_aaaa_query_returns_no_answer(self) -> None:
        response = build_dns_response(dns_query("captive.apple.com", qtype=28), "192.168.137.1")
        self.assertEqual(struct.unpack("!H", response[6:8])[0], 0)

    def test_probe_hosts_are_added_and_removed_safely(self) -> None:
        with TemporaryDirectory() as temp:
            hosts = Path(temp) / "hosts"
            hosts.write_text("127.0.0.1 localhost\n", encoding="utf-8")
            ok, _ = configure_probe_hosts(
                "192.168.137.1",
                hosts,
                ("csm.calapies.home.arpa",),
            )
            self.assertTrue(ok)
            text = hosts.read_text(encoding="utf-8")
            self.assertIn(HOSTS_BEGIN, text)
            self.assertIn(HOSTS_END, text)
            self.assertIn(CAPTIVE_PROBE_HOSTS[0], text)
            self.assertIn("csm.calapies.home.arpa", text)
            ok2, _ = remove_probe_hosts(hosts)
            self.assertTrue(ok2)
            cleaned = hosts.read_text(encoding="utf-8")
            self.assertNotIn(HOSTS_BEGIN, cleaned)
            self.assertIn("127.0.0.1 localhost", cleaned)


    def test_hosts_mapping_alone_is_not_reported_as_guaranteed_automatic_open(self) -> None:
        status = CaptivePortalStatus(
            http_running=True,
            dns_running=False,
            probe_hosts_configured=True,
            target_url="http://192.168.137.1:8080/portal",
            detail="best effort",
        )
        self.assertFalse(status.automatic_open_available)

    def test_http_redirector_points_to_survey_portal(self) -> None:
        with TemporaryDirectory() as temp:
            hosts = Path(temp) / "hosts"
            hosts.write_text("127.0.0.1 localhost\n", encoding="utf-8")
            service = CaptivePortalService(
                hotspot_ip="127.0.0.1",
                target_provider=lambda: "http://csm.calapies.home.arpa:8080/portal",
                hosts_path=hosts,
                local_hostnames=("csm.calapies.home.arpa",),
            )
            status = service.start(http_port=0, dns_port=0)
            self.assertTrue(status.http_running)
            server = service._http_server  # focused integration test for the bound ephemeral port
            self.assertIsNotNone(server)
            port = int(server.server_address[1])
            connection = HTTPConnection("127.0.0.1", port, timeout=5)
            connection.request("GET", "/generate_204", headers={"Host": "connectivitycheck.gstatic.com"})
            response = connection.getresponse()
            self.assertEqual(response.status, 302)
            self.assertEqual(response.getheader("Location"), "http://csm.calapies.home.arpa:8080/portal")
            connection.close()
            service.stop()


if __name__ == "__main__":
    unittest.main()
