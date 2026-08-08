from __future__ import annotations

import unittest

from school_csm_control_center.web_server.access_codes import (
    build_access_urls,
    build_named_portal_url,
    build_direct_portal_url,
    build_wifi_qr_payload,
)


class AccessCodeTests(unittest.TestCase):
    def test_visible_wpa_payload_matches_windows_compact_format(self) -> None:
        payload = build_wifi_qr_payload(
            ssid="School Survey Laptop",
            security="WPA",
            password="example-password",
            hidden=False,
        )
        self.assertEqual(
            payload,
            "WIFI:T:WPA;S:School Survey Laptop;P:example-password;;",
        )
        self.assertNotIn("H:false", payload)

    def test_hidden_wpa_payload_adds_hidden_flag_only_when_needed(self) -> None:
        payload = build_wifi_qr_payload(
            ssid="Hidden;Hotspot",
            security="WPA",
            password="p:a;s,s\\word",
            hidden=True,
        )
        self.assertEqual(
            payload,
            r"WIFI:T:WPA;S:Hidden\;Hotspot;P:p\:a\;s\,s\\word;H:true;;",
        )

    def test_secure_wifi_requires_password(self) -> None:
        self.assertEqual(
            build_wifi_qr_payload(ssid="Survey Hotspot", security="WPA", password=""),
            "",
        )

    def test_open_wifi_uses_nopass_format(self) -> None:
        self.assertEqual(
            build_wifi_qr_payload(ssid="Survey Hotspot", security="NOPASS"),
            "WIFI:T:nopass;S:Survey Hotspot;;",
        )

    def test_named_access_is_distinct_from_direct_fallback(self) -> None:
        direct_root, named_root, direct_access, named_access = build_access_urls(
            hostname="csm.calapies.home.arpa",
            local_ip="192.168.137.1",
            port=8080,
            access_key="sample-token",
        )
        self.assertEqual(direct_root, "http://192.168.137.1:8080")
        self.assertEqual(named_root, "http://csm.calapies.home.arpa:8080")
        self.assertEqual(direct_access, "http://192.168.137.1:8080/access/sample-token")
        self.assertEqual(named_access, "http://csm.calapies.home.arpa:8080/access/sample-token")
        self.assertEqual(
            build_named_portal_url(hostname="csm.calapies.home.arpa", port=8080),
            "http://csm.calapies.home.arpa:8080/portal",
        )
        self.assertEqual(
            build_direct_portal_url(local_ip="192.168.137.1", port=8080),
            "http://192.168.137.1:8080/portal",
        )


if __name__ == "__main__":
    unittest.main()
