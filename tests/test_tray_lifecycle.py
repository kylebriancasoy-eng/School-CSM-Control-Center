from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QWidget

from run_school_csm_control_center import parse_startup_arguments
from school_csm_control_center.runtime_instance import activate_existing_window
from school_csm_control_center.ui.main_window import SchoolCSMControlCenterWindow
from school_csm_control_center.ui.system_tray import ControlCenterSystemTray


class TrayMenuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_menu_actions_and_server_state_are_synchronized(self) -> None:
        parent = QWidget()
        tray = ControlCenterSystemTray(
            QIcon(), parent, available_override=True, show_immediately=False
        )
        intents: list[str] = []
        startup_changes: list[bool] = []
        tray.open_requested.connect(lambda: intents.append("open"))
        tray.start_server_requested.connect(lambda: intents.append("start"))
        tray.stop_server_requested.connect(lambda: intents.append("stop"))
        tray.background_startup_toggled.connect(startup_changes.append)

        tray.open_action.trigger()
        tray.start_action.trigger()
        tray.background_action.setChecked(True)
        self.assertEqual(intents, ["open", "start"])
        self.assertEqual(startup_changes, [True])

        tray.set_direct_survey_url("http://192.168.137.1:8080/survey")
        tray.set_server_state("online", "Healthy")
        self.assertIn("Running", tray.status_action.text())
        self.assertFalse(tray.start_action.isEnabled())
        self.assertTrue(tray.stop_action.isEnabled())
        self.assertTrue(tray.open_survey_action.isEnabled())

        tray.set_server_state("starting", "Starting")
        self.assertFalse(tray.start_action.isEnabled())
        self.assertFalse(tray.stop_action.isEnabled())
        tray.hide()
        parent.deleteLater()

    def test_background_close_hides_but_explicit_close_shuts_down(self) -> None:
        with TemporaryDirectory() as temporary, patch(
            "school_csm_control_center.web_server.controller.is_windows",
            return_value=False,
        ):
            window = SchoolCSMControlCenterWindow(Path(temporary))
            window.system_tray._available = True
            window._background_startup_enabled = True
            window._apply_background_lifecycle()
            shutdowns: list[bool] = []
            window.server_controller.request_shutdown = lambda: shutdowns.append(True)
            window.show()
            self.app.processEvents()

            window.close()
            self.app.processEvents()
            self.assertFalse(window.isVisible())
            self.assertEqual(shutdowns, [])

            window._explicit_exit_requested = True
            window.close()
            self.app.processEvents()
            self.assertEqual(shutdowns, [True])
            window.deleteLater()
            self.app.setQuitOnLastWindowClosed(True)


class StartupArgumentTests(unittest.TestCase):
    def test_only_the_owned_background_flags_enable_hidden_autostart(self) -> None:
        self.assertEqual(parse_startup_arguments([]), (False, False))
        self.assertEqual(
            parse_startup_arguments(["--background", "--auto-start-server"]),
            (True, True),
        )
        self.assertEqual(
            parse_startup_arguments(
                ["--background", "--auto-start-server", "--show"]
            ),
            (False, False),
        )

    def test_second_launch_restores_the_hidden_native_window(self) -> None:
        calls: list[tuple[object, ...]] = []

        class _Function:
            def __init__(self, result: object, name: str) -> None:
                self.result = result
                self.name = name
                self.argtypes = None
                self.restype = None

            def __call__(self, *args: object) -> object:
                calls.append((self.name, *args))
                return self.result

        class _User32:
            FindWindowW = _Function(1234, "find")
            ShowWindow = _Function(True, "show")
            SetForegroundWindow = _Function(True, "foreground")

        with patch(
            "school_csm_control_center.runtime_instance.sys.platform", "win32"
        ), patch("ctypes.WinDLL", return_value=_User32()):
            self.assertTrue(activate_existing_window("School CSM"))

        self.assertIn(("show", 1234, 9), calls)
        self.assertIn(("foreground", 1234), calls)


if __name__ == "__main__":
    unittest.main()
