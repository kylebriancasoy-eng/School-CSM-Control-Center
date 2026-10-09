"""Isolated Qt behavior for the district tray's Open Status action.

These tests exercise real window state without a server, tunnel, credentials,
or native notification icon. Windows STARTUPINFO suppression still needs a
compiled Windows smoke test; the offscreen platform cannot reproduce it.
"""

from __future__ import annotations

from contextlib import ExitStack
import os
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from district_management_server.paths import DistrictRuntimePaths
from district_management_server.server_controller import (
    DistrictServerController,
    DistrictServerSnapshot,
)
from district_management_server.tray import DistrictManagementWindow


class _IsolatedTray(QObject):
    """Retain the real menu wiring without touching the desktop tray."""

    activated = Signal(object)
    ActivationReason = QSystemTrayIcon.ActivationReason
    MessageIcon = QSystemTrayIcon.MessageIcon

    def __init__(self, _icon, parent=None) -> None:
        super().__init__(parent)
        self.menu = None

    def setContextMenu(self, menu) -> None:
        self.menu = menu

    def setToolTip(self, _text) -> None:
        pass

    def show(self) -> None:
        pass

    def hide(self) -> None:
        pass

    def showMessage(self, *_arguments) -> None:
        pass


class DistrictTrayVisibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.resources = ExitStack()
        self.addCleanup(self.resources.close)
        # Only a unique, nonexistent path is required: never create or clean up
        # runtime directories, and never resolve the live machine-wide state.
        isolated_root = Path(__file__).resolve().parent / (
            f"unused-district-state-{uuid4().hex}"
        )
        self.paths = DistrictRuntimePaths(isolated_root)
        self.controller = Mock(spec=DistrictServerController)
        self.controller.snapshot.return_value = DistrictServerSnapshot(
            "stopped", "Isolated visibility test."
        )
        self.resources.enter_context(
            patch(
                "district_management_server.tray.DistrictServerController",
                return_value=self.controller,
            )
        )
        self.resources.enter_context(
            patch("district_management_server.tray.QSystemTrayIcon", _IsolatedTray)
        )
        self.resources.enter_context(
            patch("district_management_server.tray.is_enabled", return_value=False)
        )
        self.resources.enter_context(
            patch(
                "district_management_server.tray.set_enabled",
                side_effect=AssertionError("Visibility must not change Windows startup."),
            )
        )
        self.resources.enter_context(
            patch(
                "district_management_server.tray.DistrictCredentialStore",
                side_effect=AssertionError("Visibility must not access credentials."),
            )
        )
        self.window = DistrictManagementWindow(
            self.paths, isolated_root / "app", start_hidden=True
        )
        self.window.timer.stop()
        self.addCleanup(self._close_window)
        self.open_status = next(
            action
            for action in self.window.tray.menu.actions()
            if action.text() == "Open Status"
        )

    def _close_window(self) -> None:
        # Avoid exit_application(), which intentionally stops the live service.
        self.window.timer.stop()
        self.window._exiting = True
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def tearDown(self) -> None:
        self.controller.start.assert_not_called()
        self.controller.restart.assert_not_called()
        self.controller.stop.assert_not_called()
        self.assertFalse(self.paths.configuration_file.exists())

    def test_open_status_restores_background_and_closed_to_tray_window(self) -> None:
        self.assertFalse(self.window.isVisible())

        self.open_status.trigger()
        self.app.processEvents()
        self.assertTrue(self.window.isVisible())
        self.assertFalse(self.window.isMinimized())

        self.window.close()
        self.app.processEvents()
        self.assertFalse(self.window.isVisible())

        self.open_status.trigger()
        self.app.processEvents()
        self.assertTrue(self.window.isVisible())
        self.assertFalse(self.window.isMinimized())

    def test_open_status_restores_minimized_window(self) -> None:
        self.open_status.trigger()
        self.app.processEvents()
        self.window.showMinimized()
        self.app.processEvents()
        self.assertTrue(self.window.isMinimized())

        self.open_status.trigger()
        self.app.processEvents()
        self.assertTrue(self.window.isVisible())
        self.assertFalse(self.window.isMinimized())


if __name__ == "__main__":
    unittest.main()
