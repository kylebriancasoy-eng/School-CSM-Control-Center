from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog

from school_csm_control_center.storage.field_preset_store import FieldPresetStore
from school_csm_control_center.ui.main_window import SchoolCSMControlCenterWindow
from school_csm_control_center.ui.widgets import TooltipIconButton


def editable_record(service: str = "Record-specific service") -> dict:
    return {
        "mode": "onsite",
        "control_number": "EDIT-001",
        "survey_date": "2026-07-17",
        "meta": {
            "agency_visited": "Calapi Elementary School",
            "service_availed": service,
            "region": "region_05",
            "client_type": "Citizen",
        },
        "cc": {"cc1": 1, "cc2": 1, "cc3": 1},
        "sqd": {"sqd1": 5},
        "feedback": {"comments": "Ready for editing."},
    }


class FieldPresetOverlayUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.project_root = Path(self.temporary.name)
        self.window = self._make_window()

    def tearDown(self) -> None:
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.temporary.cleanup()

    def _make_window(self) -> SchoolCSMControlCenterWindow:
        window = SchoolCSMControlCenterWindow(self.project_root)
        window.resize(1280, 760)
        window.show()
        self.app.processEvents()
        return window

    def _open_add_drawer(self) -> None:
        QTest.mouseClick(
            self.window.dashboard.add_button,
            Qt.MouseButton.LeftButton,
        )
        QTest.qWait(250)
        self.app.processEvents()
        self.assertTrue(self.window.drawer.is_open)

    def _open_preset_overlay(self) -> None:
        QTest.mouseClick(
            self.window.drawer.preset_button,
            Qt.MouseButton.LeftButton,
        )
        self.app.processEvents()
        self.assertTrue(self.window.drawer.preset_overlay.isVisible())

    def _apply_locked_fields(self, *keys: str) -> None:
        overlay = self.window.drawer.preset_overlay
        for key in keys:
            row = overlay._rows[key]
            row["locked"].setChecked(True)
            self.assertTrue(row["prefill"].isChecked())
            self.assertFalse(row["prefill"].isEnabled())
        QTest.mouseClick(overlay.apply_button, Qt.MouseButton.LeftButton)
        self.app.processEvents()
        self.assertFalse(overlay.isVisible())

    def test_preset_entry_and_overlay_actions_are_icon_only_and_in_window(self) -> None:
        self._open_add_drawer()
        preset_button = self.window.drawer.preset_button
        self.assertIsInstance(preset_button, TooltipIconButton)
        self.assertEqual(preset_button.text(), "")
        self.assertFalse(preset_button.icon().isNull())
        self.assertTrue(preset_button.toolTip().strip())
        self.assertTrue(preset_button.accessibleName().strip())

        self._open_preset_overlay()
        overlay = self.window.drawer.preset_overlay
        self.assertFalse(overlay.isWindow())
        self.assertIs(overlay.window(), self.window)
        action_buttons = overlay.findChildren(TooltipIconButton)
        self.assertEqual(len(action_buttons), 4)
        for button in action_buttons:
            with self.subTest(tooltip=button.toolTip()):
                self.assertEqual(button.text(), "")
                self.assertFalse(button.icon().isNull())
                self.assertTrue(button.toolTip().strip())
                self.assertTrue(button.accessibleName().strip())
        self.assertEqual(self.window.findChildren(QDialog), [])
        self.assertEqual(
            [
                widget
                for widget in QApplication.topLevelWidgets()
                if isinstance(widget, QDialog)
            ],
            [],
        )

    def test_lock_implies_prefill_and_applies_again_after_form_reset(self) -> None:
        self._open_add_drawer()
        self.window.drawer.service_input.setText("Enrollment Records")
        self._open_preset_overlay()

        row = self.window.drawer.preset_overlay._rows["service_availed"]
        self.assertFalse(row["prefill"].isChecked())
        QTest.mouseClick(row["locked"], Qt.MouseButton.LeftButton)
        self.assertTrue(row["locked"].isChecked())
        self.assertTrue(row["prefill"].isChecked())
        self.assertFalse(row["prefill"].isEnabled())
        QTest.mouseClick(
            self.window.drawer.preset_overlay.apply_button,
            Qt.MouseButton.LeftButton,
        )
        self.app.processEvents()

        expected = {
            "value": ["Enrollment Records"],
            "prefill": True,
            "locked": True,
        }
        self.assertEqual(
            FieldPresetStore(self.project_root).load()["service_availed"],
            expected,
        )
        self.assertEqual(self.window.drawer.service_input.text(), "Enrollment Records")
        self.assertFalse(self.window.drawer.service_input.isEnabled())

        # Programmatic mutation represents draft noise; reset must restore the
        # persisted prefill and retain the lock for the next add operation.
        self.window.drawer.service_input.setText("Temporary draft value")
        self.window.drawer.reset_form()
        self.assertEqual(self.window.drawer.service_input.text(), "Enrollment Records")
        self.assertFalse(self.window.drawer.service_input.isEnabled())

    def test_locked_presets_survive_new_window_and_edit_mode_unlocks_them(self) -> None:
        self._open_add_drawer()
        self.window.drawer.service_input.setText("Default Service")
        self.window.drawer.region_input.setCurrentIndex(
            self.window.drawer.region_input.findData("region_08")
        )
        self._open_preset_overlay()
        self._apply_locked_fields("service_availed", "region")

        original_store = self.window.store
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.window = self._make_window()
        self.assertIsNot(self.window.store, original_store)

        self._open_add_drawer()
        self.assertEqual(self.window.drawer.service_input.text(), "Default Service")
        self.assertEqual(
            self.window.drawer.region_input.currentText(),
            "Region VIII – Eastern Visayas",
        )
        self.assertFalse(self.window.drawer.service_input.isEnabled())
        self.assertFalse(self.window.drawer.region_input.isEnabled())

        saved = self.window.store.add(editable_record())
        self.window.drawer.open_for_edit(saved)
        QTest.qWait(250)
        self.app.processEvents()
        self.assertEqual(
            self.window.drawer.title_label.text(),
            "Edit Survey Result",
        )
        self.assertEqual(
            self.window.drawer.service_input.text(),
            "Record-specific service",
        )
        self.assertEqual(
            self.window.drawer.region_input.currentText(),
            "Region V – Bicol Region",
        )
        self.assertTrue(self.window.drawer.service_input.isEnabled())
        self.assertTrue(self.window.drawer.region_input.isEnabled())


if __name__ == "__main__":
    unittest.main()
