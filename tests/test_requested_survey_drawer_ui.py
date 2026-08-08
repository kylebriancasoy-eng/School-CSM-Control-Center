from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QDate, QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QCalendarWidget,
    QCheckBox,
    QDialog,
    QTableWidget,
    QWidget,
)

from school_csm_control_center.questionnaire import RATING_LABELS, SQD_QUESTIONS
from school_csm_control_center.ui.main_window import SchoolCSMControlCenterWindow


class RequestedSurveyDrawerUiTests(unittest.TestCase):
    """Behavioral contract for the revised survey-entry experience."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.project_root = Path(self.temporary.name)
        self.window = SchoolCSMControlCenterWindow(self.project_root)
        self.window.resize(1360, 820)
        self.window.show()
        self.app.processEvents()
        self.window.drawer.open_for_add()
        QTest.qWait(250)
        self.app.processEvents()

    def tearDown(self) -> None:
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.temporary.cleanup()

    def _select_mode(self, mode: str) -> None:
        combo = self.window.drawer.mode_combo
        index = combo.findData(mode)
        self.assertGreaterEqual(index, 0)
        combo.setCurrentIndex(index)
        self.app.processEvents()

    def _choose_sqd(self, code: str, score: int) -> None:
        check = self.window.drawer.sqd_checks[code][score]
        self.assertIsInstance(check, QCheckBox)
        check.click()
        self.app.processEvents()

    @staticmethod
    def _checked_count(group) -> int:
        return sum(check.isChecked() for check in group.checks.values())

    @staticmethod
    def _form_field_for(control: QWidget) -> QWidget:
        current: QWidget | None = control
        while current is not None and current.objectName() != "survey_form_field":
            current = current.parentWidget()
        if current is None:
            raise AssertionError("The control is not contained in a survey form field.")
        return current

    def test_control_number_tracks_date_and_advances_after_save(self) -> None:
        drawer = self.window.drawer
        self._select_mode("onsite")
        selected_date = QDate.currentDate()
        drawer.date_edit.setDate(selected_date)
        self.app.processEvents()

        month = selected_date.toString("yyyy-MM")
        first_control = f"{month}-0001"
        second_control = f"{month}-0002"
        self.assertTrue(drawer.control_input.isReadOnly())
        self.assertEqual(drawer.control_input.text(), first_control)

        for code in SQD_QUESTIONS["onsite"]:
            self._choose_sqd(code, 5)
        QTest.mouseClick(drawer.save_button, Qt.MouseButton.LeftButton)
        self.app.processEvents()
        records = self.window.store.list()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["control_number"], first_control)
        self.assertEqual(drawer.control_input.text(), second_control)

        next_month = selected_date.addMonths(1)
        drawer.date_edit.setDate(next_month)
        self.app.processEvents()
        self.assertEqual(
            drawer.control_input.text(),
            f"{next_month.toString('yyyy-MM')}-0001",
        )

    def test_calendar_is_an_in_app_overlay_and_updates_date_without_dialogs(self) -> None:
        drawer = self.window.drawer
        self.assertTrue(drawer.date_edit.isReadOnly())
        QTest.mouseClick(drawer.calendar_button, Qt.MouseButton.LeftButton)
        self.app.processEvents()

        overlay = drawer.calendar_overlay
        self.assertTrue(overlay.isVisible())
        self.assertFalse(overlay.isWindow())
        self.assertIs(overlay.window(), self.window)
        self.assertIsInstance(overlay.calendar, QCalendarWidget)
        self.assertIs(overlay.calendar.window(), self.window)
        self.assertEqual(self.window.findChildren(QDialog), [])
        self.assertEqual(
            [
                widget
                for widget in QApplication.topLevelWidgets()
                if isinstance(widget, QDialog)
            ],
            [],
        )

        chosen = QDate.currentDate().addDays(-3)
        overlay.calendar.setSelectedDate(chosen)
        # Emitting the public calendar signal keeps the test deterministic in
        # the offscreen platform while exercising the overlay's real binding.
        overlay.calendar.clicked.emit(chosen)
        self.app.processEvents()
        self.assertEqual(drawer.date_edit.date(), chosen)
        self.assertEqual(
            drawer.control_input.text(),
            f"{chosen.toString('yyyy-MM')}-0001",
        )

    def test_date_and_agency_fields_share_one_aligned_grid_row(self) -> None:
        drawer = self.window.drawer
        date_field = drawer.date_field
        agency_field = self._form_field_for(drawer.office_input)
        self.assertIs(date_field.parentWidget(), agency_field.parentWidget())

        date_position = date_field.mapTo(drawer.form_body, QPoint(0, 0))
        agency_position = agency_field.mapTo(drawer.form_body, QPoint(0, 0))
        self.assertLessEqual(abs(date_position.y() - agency_position.y()), 2)
        self.assertLessEqual(abs(date_field.width() - agency_field.width()), 4)

    def test_client_and_sex_tickboxes_are_exclusive_and_keep_online_custom_text(self) -> None:
        drawer = self.window.drawer
        for group, first, second in (
            (drawer.client_type_group, "Citizen", "Business"),
            (drawer.sex_group, "Male", "Female"),
        ):
            with self.subTest(group=type(group).__name__):
                self.assertTrue(group.checks)
                self.assertTrue(
                    all(isinstance(check, QCheckBox) for check in group.checks.values())
                )
                group.set_value(first)
                self.assertEqual(group.value(), first)
                self.assertEqual(self._checked_count(group), 1)
                group.set_value(second)
                self.assertEqual(group.value(), second)
                self.assertEqual(self._checked_count(group), 1)

        self._select_mode("online")
        drawer.client_type_group.set_value("Cooperative / NGO")
        drawer.sex_group.set_value("Prefer to self-describe")
        self.assertEqual(drawer.client_type_group.value(), "Cooperative / NGO")
        self.assertEqual(drawer.sex_group.value(), "Prefer to self-describe")
        self.assertEqual(self._checked_count(drawer.client_type_group), 1)
        self.assertEqual(self._checked_count(drawer.sex_group), 1)

        payload = drawer._record_from_form()
        self.assertEqual(payload["meta"]["client_type"], "Cooperative / NGO")
        self.assertEqual(payload["meta"]["sex"], "Prefer to self-describe")

    def test_sqd_table_has_emoji_headers_legend_and_one_tick_per_row(self) -> None:
        drawer = self.window.drawer
        self._select_mode("onsite")
        self.assertIsInstance(drawer.sqd_table, QTableWidget)
        self.assertTrue(drawer.sqd_legend.isVisible())
        self.assertEqual(
            set(drawer.sqd_checks),
            set(SQD_QUESTIONS["onsite"]),
        )
        for code, checks in drawer.sqd_checks.items():
            with self.subTest(mode="onsite", code=code):
                self.assertEqual(set(checks), {0, 1, 2, 3, 4, 5})
                self.assertTrue(all(isinstance(check, QCheckBox) for check in checks.values()))

        headers = [
            drawer.sqd_table.horizontalHeaderItem(column).text()
            for column in range(drawer.sqd_table.columnCount())
            if drawer.sqd_table.horizontalHeaderItem(column) is not None
        ]
        emoji_headers = [
            text for text in headers if any(ord(character) > 0xFFFF for character in text)
        ]
        self.assertGreaterEqual(len(emoji_headers), 5)

        legend_text = " ".join(
            label.text() for label in drawer.sqd_legend.findChildren(QWidget)
            if hasattr(label, "text") and callable(label.text)
        )
        for rating in RATING_LABELS.values():
            self.assertIn(rating, legend_text)

        self._choose_sqd("sqd1", 1)
        self._choose_sqd("sqd1", 5)
        self.assertEqual(
            sum(check.isChecked() for check in drawer.sqd_checks["sqd1"].values()),
            1,
        )
        self.assertTrue(drawer.sqd_checks["sqd1"][5].isChecked())

        self._select_mode("online")
        self.assertEqual(
            set(drawer.sqd_checks),
            set(SQD_QUESTIONS["online"]),
        )
        for code, checks in drawer.sqd_checks.items():
            with self.subTest(mode="online", code=code):
                self.assertEqual(set(checks), {1, 2, 3, 4, 5})
                self.assertNotIn(0, checks)


if __name__ == "__main__":
    unittest.main()
