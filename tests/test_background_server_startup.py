from __future__ import annotations

import os
from pathlib import Path
import socket
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from school_csm_control_center.storage.control_center_settings import (
    ControlCenterSettingsStore,
)
from school_csm_control_center.storage.survey_store import SurveyStore
from school_csm_control_center.ui.server_board import SurveyServerBoard
from school_csm_control_center.web_server.controller import SurveyServerController
from school_csm_control_center.windows_startup import (
    RUN_KEY_PATH,
    RUN_VALUE_NAME,
    StartupRegistrationError,
    WindowsStartupRegistration,
    compiled_startup_command,
    disable,
    enable,
    is_enabled,
)


class _RegistryKey:
    def __init__(self, registry: "_FakeRegistry") -> None:
        self.registry = registry

    def __enter__(self) -> "_RegistryKey":
        return self

    def __exit__(self, *_args: object) -> None:
        return None


class _FakeRegistry:
    HKEY_CURRENT_USER = object()
    KEY_READ = 1
    KEY_SET_VALUE = 2
    REG_SZ = 1

    def __init__(self) -> None:
        self.values: dict[str, tuple[str, int]] = {}

    def CreateKeyEx(self, root: object, path: str, reserved: int, access: int) -> _RegistryKey:
        assert root is self.HKEY_CURRENT_USER
        assert path == RUN_KEY_PATH
        return _RegistryKey(self)

    def OpenKey(self, root: object, path: str, reserved: int, access: int) -> _RegistryKey:
        assert root is self.HKEY_CURRENT_USER
        assert path == RUN_KEY_PATH
        return _RegistryKey(self)

    def SetValueEx(self, key: _RegistryKey, name: str, reserved: int, kind: int, value: str) -> None:
        self.values[name] = (value, kind)

    def QueryValueEx(self, key: _RegistryKey, name: str) -> tuple[str, int]:
        try:
            return self.values[name]
        except KeyError as exc:
            raise FileNotFoundError(name) from exc

    def DeleteValue(self, key: _RegistryKey, name: str) -> None:
        try:
            del self.values[name]
        except KeyError as exc:
            raise FileNotFoundError(name) from exc


class WindowsStartupTests(unittest.TestCase):
    def test_exact_compiled_command_and_idempotent_enable_disable(self) -> None:
        registry = _FakeRegistry()
        executable = Path(r"C:\Program Files (x86)\MoSSLab\School CSM Control Center\School CSM Control Center.exe")
        expected = (
            '"C:\\Program Files (x86)\\MoSSLab\\School CSM Control Center\\School CSM Control Center.exe" '
            "--background --auto-start-server"
        )
        self.assertEqual(compiled_startup_command(executable=executable), expected)
        self.assertTrue(enable(registry=registry, executable=executable))
        self.assertTrue(enable(registry=registry, executable=executable))
        self.assertEqual(registry.values[RUN_VALUE_NAME], (expected, registry.REG_SZ))
        self.assertTrue(is_enabled(registry=registry, executable=executable))
        self.assertFalse(disable(registry=registry))
        self.assertFalse(disable(registry=registry))
        self.assertFalse(is_enabled(registry=registry, executable=executable))

    def test_facade_uses_injected_registry_and_executable(self) -> None:
        registry = _FakeRegistry()
        executable = Path(r"C:\Program Files (x86)\MoSSLab\School CSM Control Center\School CSM Control Center.exe")
        registration = WindowsStartupRegistration(
            Path(r"C:\Program Files (x86)\MoSSLab\School CSM Control Center"),
            registry=registry,
            executable=executable,
        )
        self.assertTrue(registration.set_enabled(True))
        self.assertTrue(registration.is_enabled())
        self.assertFalse(registration.set_enabled(False))
        self.assertFalse(registration.is_enabled())

    def test_source_build_refuses_registration(self) -> None:
        with patch("school_csm_control_center.windows_startup.sys.frozen", False, create=True):
            with self.assertRaises(StartupRegistrationError):
                compiled_startup_command()


class BackgroundStartupControllerTests(unittest.TestCase):
    def test_setting_defaults_false_and_controller_round_trips_registration(self) -> None:
        registry = _FakeRegistry()
        executable = Path(r"C:\Program Files (x86)\MoSSLab\School CSM Control Center\School CSM Control Center.exe")
        with TemporaryDirectory() as temp, patch(
            "school_csm_control_center.web_server.controller.is_windows",
            return_value=False,
        ):
            root = Path(temp)
            store = ControlCenterSettingsStore(root)
            self.assertFalse(store.load()["background_server_startup_enabled"])
            controller = SurveyServerController(SurveyStore(root), root)
            self.assertTrue(
                controller.set_background_server_startup(
                    True,
                    registry=registry,
                    executable=executable,
                )
            )
            self.assertTrue(store.load()["background_server_startup_enabled"])
            self.assertFalse(
                controller.set_background_server_startup(False, registry=registry)
            )
            self.assertFalse(store.load()["background_server_startup_enabled"])

    def test_failed_settings_write_rolls_back_windows_startup_entry(self) -> None:
        registry = _FakeRegistry()
        executable = Path(
            r"C:\Program Files (x86)\MoSSLab\School CSM Control Center\School CSM Control Center.exe"
        )
        with TemporaryDirectory() as temp, patch(
            "school_csm_control_center.web_server.controller.is_windows",
            return_value=False,
        ):
            root = Path(temp)
            controller = SurveyServerController(SurveyStore(root), root)
            with patch.object(
                controller.settings_store,
                "save",
                side_effect=OSError("disk unavailable"),
            ):
                with self.assertRaises(OSError):
                    controller.set_background_server_startup(
                        True,
                        registry=registry,
                        executable=executable,
                    )
            self.assertNotIn(RUN_VALUE_NAME, registry.values)
            self.assertFalse(
                controller.settings()["background_server_startup_enabled"]
            )

    def test_preferred_port_validation_and_unattended_start_skips_firewall(self) -> None:
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])
        probe.close()
        with TemporaryDirectory() as temp, patch(
            "school_csm_control_center.web_server.controller.is_windows",
            return_value=False,
        ), patch(
            "school_csm_control_center.web_server.controller.local_ipv4_candidates",
            return_value=[("127.0.0.1 - This laptop", "127.0.0.1")],
        ):
            root = Path(temp)
            controller = SurveyServerController(SurveyStore(root), root)
            self.assertEqual(controller.set_preferred_port(port), port)
            with self.assertRaises(ValueError):
                controller.set_preferred_port(0)
            controller.configure_firewall = lambda *_args, **_kwargs: self.fail(
                "Unattended startup must not request runtime firewall elevation"
            )
            try:
                controller.start(port, port, configure_firewall=False)
                self.assertTrue(controller.running)
                self.assertEqual(controller.settings()["firewall_status"], "Preauthorized")
            finally:
                controller.stop()

    def test_shutdown_during_start_prevents_late_server_creation(self) -> None:
        entered_firewall = Event()
        release_firewall = Event()
        with TemporaryDirectory() as temp, patch(
            "school_csm_control_center.web_server.controller.is_windows",
            return_value=False,
        ), patch(
            "school_csm_control_center.web_server.controller.local_ipv4_candidates",
            return_value=[("127.0.0.1 - This laptop", "127.0.0.1")],
        ):
            root = Path(temp)
            controller = SurveyServerController(SurveyStore(root), root)

            def delayed_firewall(*_args: object, **_kwargs: object) -> dict[str, object]:
                entered_firewall.set()
                release_firewall.wait(5)
                return {"ok": True, "firewall": "Allowed"}

            controller.configure_firewall = delayed_firewall
            self.assertTrue(controller.start_async(0, 0, configure_firewall=True))
            self.assertTrue(entered_firewall.wait(5))
            controller.request_shutdown()
            release_firewall.set()
            worker = controller._operation_thread
            if worker is not None:
                worker.join(5)
            self.assertFalse(controller.running)
            self.assertEqual(controller.port, 0)


class BackgroundStartupUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_switch_emits_user_changes_and_safe_sync_does_not_emit(self) -> None:
        with TemporaryDirectory() as temp, patch(
            "school_csm_control_center.web_server.controller.is_windows",
            return_value=False,
        ), patch(
            "school_csm_control_center.web_server.controller.local_ipv4_candidates",
            return_value=[("127.0.0.1 - This laptop", "127.0.0.1")],
        ):
            root = Path(temp)
            controller = SurveyServerController(SurveyStore(root), root)
            board = SurveyServerBoard(controller)
            changes: list[bool] = []
            board.background_startup_requested.connect(changes.append)
            self.assertFalse(board.background_startup_switch.isChecked())
            self.assertEqual(board.background_startup_switch.text(), "OFF")
            board.background_startup_switch.click()
            self.app.processEvents()
            self.assertEqual(changes, [True])
            self.assertEqual(board.background_startup_switch.text(), "ON")
            board.sync_background_startup(False)
            self.app.processEvents()
            self.assertEqual(changes, [True])
            self.assertEqual(board.background_startup_state.text(), "Disabled")
            self.assertTrue(board.background_startup_switch.accessibleName())
            board.set_background_startup_enabled(True, "Starts with Windows")
            self.assertTrue(board.background_startup_switch.isChecked())
            self.assertEqual(board.status_detail.text(), "Starts with Windows")
            calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
            controller._settings["background_server_startup_enabled"] = True
            controller.start_async = lambda *args, **kwargs: calls.append((args, kwargs)) or True
            board._start_server_after_settings()
            self.assertEqual(calls[0][1]["configure_firewall"], False)
            board.deleteLater()


if __name__ == "__main__":
    unittest.main()
