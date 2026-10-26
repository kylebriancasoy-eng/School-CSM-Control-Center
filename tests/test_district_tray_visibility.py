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

    def test_update_bell_is_pinned_and_uses_district_release_details(self):
        from csm_updates.release import ReleaseInfo
        from PySide6.QtWidgets import QScrollArea
        self.window.updates._complete(ReleaseInfo("99.0.0", "District update", "Test release details"))
        self.assertIn("99.0.0", self.window.update_bell.toolTip())
        self.assertFalse(self.window.update_bell.icon().isNull())
        # Header remains outside the scrolling settings area.
        parent = self.window.update_bell.parentWidget()
        while parent is not None:
            self.assertNotIsInstance(parent, QScrollArea)
            parent = parent.parentWidget()
        with patch.object(self.window.updates, "check") as check:
            self.window.update_bell.click()
            self.assertTrue(self.window._update_dialog.controller.district)
            self.assertIn("District", self.window._update_dialog.windowTitle())
            check.assert_not_called()  # Already have verified release details.
        self.window._update_dialog.close()
        self.window._update_dialog.backup_confirmation.setChecked(True)
        self.window.update_bell.click()
        self.assertFalse(self.window._update_dialog.backup_confirmation.isChecked())
        self.window._update_dialog.close()

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

    def test_exit_is_blocked_until_current_host_operation_finishes(self) -> None:
        self.assertTrue(self.window.exit_action.isEnabled())
        self.window._working = True
        self.window._refresh()
        self.assertFalse(self.window.exit_action.isEnabled())

        isolated_application = Mock()
        with patch(
            "district_management_server.tray.QApplication.instance",
            return_value=isolated_application,
        ), patch("district_management_server.tray.QMessageBox.information") as notice:
            # Cover direct invocation as well as the disabled tray menu item:
            # neither path may race a daemon operation's credential recovery.
            self.window.exit_application()
        notice.assert_called_once()
        isolated_application.quit.assert_not_called()
        self.controller.stop.assert_not_called()
        self.assertFalse(self.window._exiting)

        self.window._finished()
        self.assertFalse(self.window._working)
        self.assertTrue(self.window.exit_action.isEnabled())


if __name__ == "__main__":
    unittest.main()
