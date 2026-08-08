from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog

from school_csm_control_center.school_services import SERVICE_BY_ID
from school_csm_control_center.storage.control_center_settings import ControlCenterSettingsStore
from school_csm_control_center.ui.main_window import SchoolCSMControlCenterWindow
from school_csm_control_center.ui.widgets import TooltipIconButton


class ApplicationUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        ControlCenterSettingsStore(self.root).save(
            {
                "school_name": "Test Elementary School",
                "school_id": "123627",
                "school_identifier": "test-school",
            }
        )
        self.window = SchoolCSMControlCenterWindow(self.root)
        self.window.resize(1280, 760)
        self.window.show()
        self.app.processEvents()

    def tearDown(self) -> None:
        self.window.close()
        self.app.processEvents()
        self.temp.cleanup()

    def test_shell_has_two_primary_boards_and_utility_overlays_with_icon_only_actions(self) -> None:
        self.assertEqual(self.window.board_stack.count(), 2)
        self.assertEqual(self.window.dashboard.objectName(), "dashboard_board")
        self.assertEqual(self.window.history.objectName(), "history_board")
        self.assertIs(self.window.server_workspace_overlay.content, self.window.server_board)
        self.assertIs(self.window.mrs_workspace_overlay.content, self.window.mrs_printing)
        self.assertIs(self.window.school_workspace_overlay.content, self.window.school_information)
        buttons = self.window.findChildren(TooltipIconButton)
        self.assertGreaterEqual(len(buttons), 12)
        for button in buttons:
            self.assertEqual(button.text(), "")
            self.assertFalse(button.icon().isNull())
            self.assertTrue(button.toolTip().strip())
            self.assertTrue(button.accessibleName().strip())
        self.assertEqual(self.window.findChildren(QDialog), [])

    def test_dashboard_print_button_opens_a_shell_overlay_without_a_dialog(self) -> None:
        self.window.print_overlay._printer_provider = lambda: []

        QTest.mouseClick(self.window.dashboard.print_button, Qt.MouseButton.LeftButton)
        self.app.processEvents()

        self.assertTrue(self.window.print_overlay.isVisible())
        self.assertIs(self.window.print_overlay.parentWidget(), self.window.shell)
        self.assertEqual(self.window.print_overlay.geometry(), self.window.shell.rect())
        self.assertFalse(self.window.print_overlay.print_button.isEnabled())
        self.assertEqual(
            [widget for widget in QApplication.topLevelWidgets() if isinstance(widget, QDialog)],
            [],
        )
        QTest.mouseClick(self.window.print_overlay.close_button, Qt.MouseButton.LeftButton)
        self.app.processEvents()
        self.assertFalse(self.window.print_overlay.isVisible())

    def test_add_drawer_saves_live_and_history_can_view_edit_delete_in_overlays(self) -> None:
        QTest.mouseClick(self.window.dashboard.add_button, Qt.MouseButton.LeftButton)
        QTest.qWait(360)
        self.assertTrue(self.window.drawer.isVisible())
        self.assertTrue(self.window.drawer.is_open)
        self.assertEqual(self.window.drawer.drawer.x(), 0)

        generated_control = self.window.drawer.control_input.text()
        self.assertRegex(generated_control, r"^\d{4}-\d{2}-\d{4}$")
        self.window.drawer.service_input.setText("Enrollment assistance")
        for checks in self.window.drawer.sqd_checks.values():
            checks[5].click()
        QTest.mouseClick(self.window.drawer.save_button, Qt.MouseButton.LeftButton)
        self.app.processEvents()
        self.assertEqual(len(self.window.store.list()), 1)
        self.assertEqual(
            self.window.store.list()[0]["meta"]["service_availed"],
            ["Enrollment assistance"],
        )
        self.assertEqual(self.window.dashboard.total_metric.value_label.text(), "1")

        self.window.drawer.close_drawer()
        QTest.qWait(260)
        QTest.mouseClick(self.window.history_nav, Qt.MouseButton.LeftButton)
        self.app.processEvents()
        self.assertIs(self.window.board_stack.currentWidget(), self.window.history)
        self.assertEqual(self.window.history.table.rowCount(), 1)
        self.window.history.table.selectRow(0)
        self.app.processEvents()
        self.assertTrue(self.window.history.view_button.isEnabled())

        QTest.mouseClick(self.window.history.view_button, Qt.MouseButton.LeftButton)
        self.app.processEvents()
        self.assertTrue(self.window.prompt.isVisible())
        self.assertEqual(
            [widget for widget in QApplication.topLevelWidgets() if isinstance(widget, QDialog)],
            [],
        )
        self.assertEqual(
            sum(isinstance(widget, SchoolCSMControlCenterWindow) for widget in QApplication.topLevelWidgets()),
            1,
        )
        QTest.mouseClick(self.window.prompt.accept_button, Qt.MouseButton.LeftButton)

        QTest.mouseClick(self.window.history.edit_button, Qt.MouseButton.LeftButton)
        QTest.qWait(260)
        self.assertTrue(self.window.drawer.is_open)
        self.assertEqual(self.window.drawer.control_input.text(), generated_control)
        self.window.drawer.close_drawer()
        QTest.qWait(260)

        self.window.history.table.selectRow(0)
        QTest.mouseClick(self.window.history.delete_button, Qt.MouseButton.LeftButton)
        self.app.processEvents()
        self.assertTrue(self.window.prompt.isVisible())
        QTest.mouseClick(self.window.prompt.accept_button, Qt.MouseButton.LeftButton)
        self.app.processEvents()
        self.assertEqual(self.window.store.list(), [])
        self.assertEqual(self.window.history.table.rowCount(), 0)

    def test_legacy_service_alias_and_zero_age_populate_drawer_boards_and_export(self) -> None:
        legacy = {
            "id": "legacy-1",
            "control_number": "LEGACY-001",
            "mode": "onsite",
            "survey_date": "2026-07-17",
            "service": "Certification",
            "meta": {"age": 0, "client_type": "Citizen"},
            "cc": {},
            "sqd": {},
            "feedback": {},
        }

        self.window.drawer.open_for_edit(legacy)
        QTest.qWait(260)
        certification = SERVICE_BY_ID["certified_copies.walk_in"].label
        self.assertEqual(self.window.drawer.service_input.values(), [certification])
        self.assertEqual(self.window.drawer.age_spin.optional_value(), 0)
        self.assertEqual(self.window.drawer._record_from_form()["meta"]["age"], 0)

        self.window.dashboard.refresh([legacy])
        self.window.history.refresh([legacy])
        self.app.processEvents()
        self.assertGreater(self.window.dashboard.service_combo.findData(certification), 0)
        self.assertGreater(self.window.history.service_combo.findData(certification), 0)
        self.assertEqual(self.window.history.table.item(0, 6).text(), "0")
        self.assertEqual(self.window.history.table.item(0, 9).text(), certification)
        exported = self.window._export_row(legacy)
        self.assertEqual(exported["service_availed"], certification)
        self.assertEqual(exported["age"], 0)


if __name__ == "__main__":
    unittest.main()
