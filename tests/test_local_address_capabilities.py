from __future__ import annotations

from pathlib import Path
import socket
from tempfile import TemporaryDirectory
import sys
import types
import unittest
from unittest.mock import patch

try:
    from PySide6.QtCore import QObject as _QtObject  # noqa: F401
except ModuleNotFoundError:
    class _Signal:
        def __init__(self, *_types: object) -> None:
            self._callbacks: list[object] = []

        def connect(self, callback: object) -> None:
            self._callbacks.append(callback)

        def emit(self, *args: object) -> None:
            for callback in list(self._callbacks):
                callback(*args)

    class _QObject:
        def __init__(self, _parent: object | None = None) -> None:
            pass

    qt_core = types.ModuleType("PySide6.QtCore")
    qt_core.QObject = _QObject
    qt_core.Signal = _Signal
    pyside = types.ModuleType("PySide6")
    pyside.QtCore = qt_core
    sys.modules.setdefault("PySide6", pyside)
    sys.modules.setdefault("PySide6.QtCore", qt_core)

from school_csm_control_center.storage.survey_store import SurveyStore
from school_csm_control_center.web_server.access_codes import (
    build_access_capability,
    build_access_urls,
)
from school_csm_control_center.web_server.controller import SurveyServerController


class _AliveThread:
    @staticmethod
    def is_alive() -> bool:
        return True


class LocalAddressCapabilityTests(unittest.TestCase):
    def test_unverified_named_address_is_not_returned(self) -> None:
        urls = build_access_urls(
            hostname="csm.calapies.home.arpa",
            local_ip="192.168.137.1",
            port=8080,
            access_key="token",
            named_available=False,
        )
        self.assertEqual(urls[0], "http://192.168.137.1:8080")
        self.assertEqual(urls[1], "")
        self.assertEqual(urls[2], "http://192.168.137.1:8080/access/token")
        self.assertEqual(urls[3], "")

    def test_direct_ipv4_is_primary_even_when_named_dns_is_verified(self) -> None:
        capability = build_access_capability(
            hostname="csm.calapies.home.arpa",
            local_ip="192.168.137.1",
            port=8080,
            access_key="token",
            server_running=True,
            direct_health_verified=True,
            named_resolver_verified=True,
        )
        self.assertTrue(capability.named_available)
        self.assertEqual(capability.primary_kind, "direct_ipv4")
        self.assertEqual(capability.primary_url, capability.direct_access_url)

    def test_hotspot_mode_does_not_replace_explicit_ip_selection(self) -> None:
        candidates = [
            ("192.168.137.1 — Microsoft Wi-Fi Direct Virtual Adapter", "192.168.137.1"),
            ("10.10.10.20 — School Wi-Fi", "10.10.10.20"),
        ]
        with TemporaryDirectory() as temp, patch(
            "school_csm_control_center.web_server.controller.is_windows",
            return_value=False,
        ), patch(
            "school_csm_control_center.web_server.controller.local_ipv4_candidates",
            return_value=candidates,
        ):
            root = Path(temp)
            controller = SurveyServerController(SurveyStore(root), root)
            controller.set_server_ip("10.10.10.20")
            controller.set_network_mode("hotspot")
            self.assertEqual(controller.settings_store.load()["server_ip"], "10.10.10.20")
            self.assertEqual(controller.selected_local_ip(), "10.10.10.20")

    def test_controller_only_emits_named_url_after_dns_verification(self) -> None:
        with TemporaryDirectory() as temp, patch(
            "school_csm_control_center.web_server.controller.is_windows",
            return_value=False,
        ), patch(
            "school_csm_control_center.web_server.controller.local_ipv4_candidates",
            return_value=[("192.168.137.1 — Hotspot", "192.168.137.1")],
        ):
            root = Path(temp)
            controller = SurveyServerController(SurveyStore(root), root)
            controller._server = object()  # focused state test; no network listener is needed
            controller._thread = _AliveThread()
            controller._port = 8080
            controller._bound_ip = "192.168.137.1"
            controller._direct_health_verified = True
            controller._active_network_settings = {
                "server_ip": "192.168.137.1",
                "preferred_port": 8080,
                "network_mode": "hotspot",
                "access_mode": "captive_portal",
                "hotspot_internet_mode": "survey_only",
                "school_identifier": "school-csm",
            }
            controller._settings.update(controller._active_network_settings)

            direct, named, direct_access, named_access = controller.urls()
            self.assertTrue(direct)
            self.assertTrue(direct_access)
            self.assertEqual(named, "")
            self.assertEqual(named_access, "")

            controller._portal_status.update({"dns_running": True, "dns_verified": True})
            direct2, named2, direct_access2, named_access2 = controller.urls()
            self.assertEqual(direct2, direct)
            self.assertEqual(direct_access2, direct_access)
            self.assertIn("csm.school-csm.home.arpa", named2)
            self.assertIn("csm.school-csm.home.arpa", named_access2)

            controller.set_access_mode("two_step")
            restart = controller.network_restart_state()
            self.assertTrue(restart["required"])
            self.assertIn("access_mode", restart["settings"])
            # The running server continues to advertise only its active,
            # verified configuration until it is stopped and started again.
            self.assertEqual(controller.access_capability()["access_mode"], "captive_portal")

    def test_controller_health_checks_direct_address_before_advertising_it(self) -> None:
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])
        probe.close()
        with TemporaryDirectory() as temp, patch(
            "school_csm_control_center.web_server.controller.is_windows",
            return_value=False,
        ), patch(
            "school_csm_control_center.web_server.controller.local_ipv4_candidates",
            return_value=[("127.0.0.1 — This laptop", "127.0.0.1")],
        ):
            root = Path(temp)
            controller = SurveyServerController(SurveyStore(root), root)
            controller.configure_firewall = lambda *_args, **_kwargs: {
                "ok": True,
                "firewall": "Not required",
            }
            try:
                controller.start(port, port)
                capability = controller.access_capability()
                self.assertTrue(capability["direct_verified"])
                self.assertTrue(str(capability["direct_access_url"]).startswith(f"http://127.0.0.1:{port}/access/"))
                self.assertFalse(capability["named_available"])
            finally:
                controller.stop()


if __name__ == "__main__":
    unittest.main()
