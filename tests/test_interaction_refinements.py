from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox, QDialog, QVBoxLayout, QWidget

from school_csm_control_center.questionnaire import SQD_QUESTIONS
from school_csm_control_center.ui.controls import NoWheelComboBox
from school_csm_control_center.ui.main_window import SchoolCSMControlCenterWindow
from school_csm_control_center.ui.survey_widgets import RATING_COLUMNS, SQDRatingTable


def _wheel_event(target: QWidget, delta: int = -120) -> QWheelEvent:
    center = target.rect().center()
    return QWheelEvent(
        QPointF(center),
        QPointF(target.mapToGlobal(center)),
        QPoint(),
        QPoint(0, delta),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.ScrollUpdate,
        False,
    )


class InteractionRefinementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_closed_focused_dropdown_ignores_mouse_wheel(self) -> None:
        host = QWidget()
        layout = QVBoxLayout(host)
        combo = NoWheelComboBox()
        combo.addItems([f"Option {number}" for number in range(30)])
        combo.setCurrentIndex(10)
        layout.addWidget(combo)
        host.show()
        combo.setFocus(Qt.FocusReason.MouseFocusReason)
        self.app.processEvents()

        QApplication.sendEvent(combo, _wheel_event(combo))
        self.app.processEvents()
        self.assertEqual(combo.currentIndex(), 10)

        host.close()
        host.deleteLater()
        self.app.processEvents()

    def test_open_dropdown_list_remains_scrollable(self) -> None:
        host = QWidget()
        layout = QVBoxLayout(host)
        combo = NoWheelComboBox()
        combo.setMaxVisibleItems(5)
        combo.addItems([f"Option {number}" for number in range(40)])
        layout.addWidget(combo)
        host.show()
        combo.showPopup()
        QTest.qWait(50)
        view = combo.view()
        scroll_bar = view.verticalScrollBar()
        self.assertTrue(view.isVisible())
        self.assertGreater(scroll_bar.maximum(), 0)
        before = scroll_bar.value()

        QApplication.sendEvent(view.viewport(), _wheel_event(view.viewport()))
        self.app.processEvents()
        self.assertGreater(scroll_bar.value(), before)

        combo.hidePopup()
        host.close()
        host.deleteLater()
        self.app.processEvents()

    def test_every_app_dropdown_uses_click_gated_combo(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            window = SchoolCSMControlCenterWindow(Path(temporary))
            try:
                combos = window.findChildren(QComboBox)
                self.assertGreaterEqual(len(combos), 19)
                self.assertTrue(all(isinstance(combo, NoWheelComboBox) for combo in combos))
            finally:
                window.close()
                window.deleteLater()
                self.app.processEvents()

    def test_clicking_blank_sqd_cell_area_selects_its_checkbox(self) -> None:
        table = SQDRatingTable()
        table.resize(760, table.height())
        table.show()
        self.app.processEvents()
        row = table._codes.index("sqd1")

        first_cell = table.cellWidget(row, 1 + RATING_COLUMNS.index(1))
        second_cell = table.cellWidget(row, 1 + RATING_COLUMNS.index(5))
        QTest.mouseClick(
            first_cell,
            Qt.MouseButton.LeftButton,
            pos=QPoint(3, 3),
        )
        self.assertEqual(table.value("sqd1"), 1)

        QTest.mouseClick(
            second_cell,
            Qt.MouseButton.LeftButton,
            pos=QPoint(3, 3),
        )
        self.assertEqual(table.value("sqd1"), 5)
        self.assertEqual(
            sum(check.isChecked() for check in table.visible_checks()["sqd1"].values()),
            1,
        )

        table.close()
        table.deleteLater()
        self.app.processEvents()

    def test_onsite_save_requires_every_sqd_and_accepts_na(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            window = SchoolCSMControlCenterWindow(root)
            window.show()
            self.app.processEvents()
            drawer = window.drawer
            drawer.open_for_add()
            QTest.qWait(250)
            missing_code = "sqd8"
            for code in SQD_QUESTIONS["onsite"]:
                if code != missing_code:
                    drawer.sqd_table.set_value(code, 5)

            QTest.mouseClick(drawer.save_button, Qt.MouseButton.LeftButton)
            self.app.processEvents()
            self.assertEqual(window.store.list(), [])
            self.assertTrue(drawer.status_banner.isVisible())
            self.assertIn(missing_code.upper(), drawer.status_banner.text())
            self.assertTrue(
                drawer.sqd_table._cells[missing_code][1].property("requiredMissing")
            )
            self.assertEqual(window.findChildren(QDialog), [])

            drawer.sqd_table.set_value(missing_code, 0)
            QTest.mouseClick(drawer.save_button, Qt.MouseButton.LeftButton)
            self.app.processEvents()
            records = window.store.list()
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["sqd"][missing_code], 0)

            window.close()
            window.deleteLater()
            self.app.processEvents()

    def test_online_requires_visible_sqd_rows_but_not_hidden_sqd0(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            window = SchoolCSMControlCenterWindow(root)
            window.show()
            self.app.processEvents()
            drawer = window.drawer
            drawer.open_for_add()
            QTest.qWait(250)
            online_index = drawer.mode_combo.findData("online")
            drawer.mode_combo.setCurrentIndex(online_index)
            self.app.processEvents()
            missing_code = "sqd6"
            for code in SQD_QUESTIONS["online"]:
                if code != missing_code:
                    drawer.sqd_table.set_value(code, 4)

            QTest.mouseClick(drawer.save_button, Qt.MouseButton.LeftButton)
            self.app.processEvents()
            self.assertEqual(window.store.list(), [])
            self.assertIn(missing_code.upper(), drawer.status_banner.text())
            self.assertNotIn("SQD0", drawer.status_banner.text())

            drawer.sqd_table.set_value(missing_code, 4)
            QTest.mouseClick(drawer.save_button, Qt.MouseButton.LeftButton)
            self.app.processEvents()
            records = window.store.list()
            self.assertEqual(len(records), 1)
            self.assertNotIn("sqd0", records[0]["sqd"])

            window.close()
            window.deleteLater()
            self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
