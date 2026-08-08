from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QLineEdit, QVBoxLayout, QWidget

from school_csm_control_center.school_services import SCHOOL_TRANSACTIONS, SERVICE_BY_ID
from school_csm_control_center.ui.controls import OptionalAgeSpinBox
from school_csm_control_center.ui.survey_widgets import CollapsibleServiceChecklist


class AgeAndServiceChecklistUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _hosted(self, *widgets: QWidget) -> QWidget:
        host = QWidget()
        host.resize(900, 680)
        layout = QVBoxLayout(host)
        for widget in widgets:
            layout.addWidget(widget)
        host.show()
        self.app.processEvents()
        self.addCleanup(self._close_widget, host)
        return host

    def _close_widget(self, widget: QWidget) -> None:
        widget.close()
        widget.deleteLater()
        self.app.processEvents()

    def test_optional_age_placeholder_clears_and_restores_around_empty_editing(self) -> None:
        focus_target = QLineEdit()
        age = OptionalAgeSpinBox()
        self._hosted(focus_target, age)
        focus_target.setFocus(Qt.FocusReason.OtherFocusReason)
        self.app.processEvents()

        self.assertEqual(age.value(), age.minimum())
        self.assertEqual(age.lineEdit().text(), "Not Provided")

        QTest.mouseClick(age.lineEdit(), Qt.MouseButton.LeftButton)
        self.app.processEvents()
        self.assertTrue(age.hasFocus() or age.lineEdit().hasFocus())
        self.assertEqual(age.lineEdit().text(), "")

        QTest.mouseClick(focus_target, Qt.MouseButton.LeftButton)
        self.app.processEvents()
        self.assertEqual(age.value(), age.minimum())
        self.assertEqual(age.lineEdit().text(), "Not Provided")

    def test_optional_age_preserves_an_entered_age_after_focus_out(self) -> None:
        focus_target = QLineEdit()
        age = OptionalAgeSpinBox()
        self._hosted(focus_target, age)
        focus_target.setFocus(Qt.FocusReason.OtherFocusReason)
        self.app.processEvents()

        QTest.mouseClick(age.lineEdit(), Qt.MouseButton.LeftButton)
        QTest.keyClicks(age.lineEdit(), "37")
        self.app.processEvents()
        QTest.mouseClick(focus_target, Qt.MouseButton.LeftButton)
        self.app.processEvents()

        self.assertEqual(age.value(), 37)
        self.assertEqual(age.lineEdit().text(), "37")

    def test_optional_age_distinguishes_not_provided_from_zero_and_supports_130(self) -> None:
        age = OptionalAgeSpinBox()
        focus_target = QLineEdit()
        self._hosted(age, focus_target)
        focus_target.setFocus(Qt.FocusReason.OtherFocusReason)
        self.app.processEvents()

        self.assertIsNone(age.optional_value())
        age.set_optional_value(0)
        self.assertEqual(age.optional_value(), 0)
        self.assertEqual(age.lineEdit().text(), "0")
        age.set_optional_value(130)
        self.assertEqual(age.optional_value(), 130)
        self.assertEqual(age.maximum(), 130)
        age.set_optional_value(None)
        self.assertIsNone(age.optional_value())
        self.assertEqual(age.lineEdit().text(), "Not Provided")

    def test_service_catalog_expands_inline_and_option_rows_toggle(self) -> None:
        checklist = CollapsibleServiceChecklist()
        self._hosted(checklist)
        dialogs_before = {
            widget
            for widget in QApplication.topLevelWidgets()
            if isinstance(widget, QDialog)
        }

        self.assertEqual(len(SCHOOL_TRANSACTIONS), 18)
        self.assertEqual(tuple(checklist.checks), SCHOOL_TRANSACTIONS)
        self.assertFalse(checklist.is_expanded)
        self.assertFalse(checklist.body.isVisible())

        QTest.mouseClick(
            checklist.header,
            Qt.MouseButton.LeftButton,
            pos=QPoint(4, checklist.header.rect().center().y()),
        )
        self.app.processEvents()
        self.assertTrue(checklist.is_expanded)
        self.assertTrue(checklist.body.isVisible())
        self.assertFalse(checklist.body.isWindow())
        self.assertIs(checklist.body.window(), checklist.window())
        self.assertEqual(
            {
                widget
                for widget in QApplication.topLevelWidgets()
                if isinstance(widget, QDialog)
            },
            dialogs_before,
        )

        first, second = SCHOOL_TRANSACTIONS[:2]
        first_row = checklist.checks[first].parentWidget()
        second_row = checklist.checks[second].parentWidget()
        self.assertEqual(first_row.objectName(), "service_checklist_option")
        self.assertFalse(checklist.checks[first].isChecked())

        QTest.mouseClick(
            first_row,
            Qt.MouseButton.LeftButton,
            pos=QPoint(first_row.rect().right() - 3, first_row.rect().center().y()),
        )
        self.app.processEvents()
        self.assertTrue(checklist.checks[first].isChecked())

        QTest.mouseClick(
            first_row,
            Qt.MouseButton.LeftButton,
            pos=QPoint(first_row.rect().right() - 3, first_row.rect().center().y()),
        )
        self.app.processEvents()
        self.assertFalse(checklist.checks[first].isChecked())

        QTest.mouseClick(first_row, Qt.MouseButton.LeftButton)
        QTest.mouseClick(second_row, Qt.MouseButton.LeftButton)
        self.app.processEvents()
        self.assertEqual(checklist.values(), [first, second])
        self.assertEqual(checklist.count_label.text(), "2 selected")

        QTest.mouseClick(
            checklist.header,
            Qt.MouseButton.LeftButton,
            pos=QPoint(4, checklist.header.rect().center().y()),
        )
        self.app.processEvents()
        self.assertFalse(checklist.is_expanded)
        self.assertFalse(checklist.body.isVisible())

    def test_service_checklist_supports_other_and_legacy_scalar_values(self) -> None:
        checklist = CollapsibleServiceChecklist()
        self._hosted(checklist)
        checklist.set_expanded(True)
        self.app.processEvents()

        certification = SERVICE_BY_ID["certified_copies.walk_in"].label
        checklist.set_values("Certification")
        self.app.processEvents()
        self.assertTrue(checklist.checks[certification].isChecked())
        self.assertFalse(checklist.other_check.isChecked())
        self.assertEqual(checklist.values(), [certification])
        self.assertEqual(checklist.text(), certification)

        first, second = SCHOOL_TRANSACTIONS[:2]
        checklist.set_values([first, second, "A locally defined transaction"])
        self.app.processEvents()
        self.assertTrue(checklist.checks[first].isChecked())
        self.assertTrue(checklist.checks[second].isChecked())
        self.assertTrue(checklist.other_check.isChecked())
        self.assertEqual(checklist.values(), [first, second, "A locally defined transaction"])
        self.assertEqual(checklist.count_label.text(), "3 selected")


if __name__ == "__main__":
    unittest.main()
