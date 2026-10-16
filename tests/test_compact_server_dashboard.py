from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication
from school_csm_control_center.storage.survey_store import SurveyStore
from school_csm_control_center.web_server.controller import SurveyServerController
from school_csm_control_center.ui.server_board import SurveyServerBoard


class CompactServerDashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.old_font = cls.app.font()
        font = Path("C:/Windows/Fonts/segoeui.ttf")
        if font.is_file():
            QFontDatabase.addApplicationFont(str(font))
            cls.app.setFont(QFont("Segoe UI", 10))

    @classmethod
    def tearDownClass(cls):
        cls.app.setFont(cls.old_font)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        with patch("school_csm_control_center.web_server.controller.is_windows", return_value=False), \
             patch("school_csm_control_center.web_server.controller.local_ipv4_candidates",
                   return_value=[("192.0.2.10 - Test network", "192.0.2.10")]):
            self.controller = SurveyServerController(SurveyStore(root), root)
            self.board = SurveyServerBoard(self.controller)
        self.addCleanup(self.board.deleteLater)
        self.addCleanup(self.board.close)

    def test_sections_preserve_fields_and_do_not_start_or_reconfigure_server(self):
        board = self.board
        self.assertEqual([board.sections.tabText(i) for i in range(board.sections.count())],
                         ["Survey settings", "Internet Gateway", "Wi-Fi and QR", "Scanner accounts"])
        before = self.controller.settings()
        board.identifier.setText("unsaved-school-address")
        for index in range(board.sections.count()):
            board.sections.setCurrentIndex(index)
            self.app.processEvents()
        self.assertEqual(board.identifier.text(), "unsaved-school-address")
        self.assertEqual(self.controller.settings(), before)
        self.assertFalse(self.controller.running)

    def test_primary_actions_stay_visible_on_every_section(self):
        board = self.board
        for width, height in ((780, 570), (1280, 720)):
            board.resize(width, height)
            board.show()
            for index in range(board.sections.count()):
                board.sections.setCurrentIndex(index)
                self.app.processEvents()
                scroll = board.sections.widget(index)
                self.assertEqual(scroll.horizontalScrollBar().maximum(), 0,
                                 f"Section {index} clips horizontally at {width}px")
                for button in (board.start_button, board.stop_button, board.apply_button):
                    self.assertTrue(button.isVisible())
                    bottom = button.mapTo(board, QPoint(button.width(), button.height()))
                    self.assertLessEqual(bottom.x(), board.width())
                    self.assertLessEqual(bottom.y(), board.height())
                    self.assertTrue(button.text())
                    self.assertTrue(button.accessibleName())
                output = os.environ.get("SCHOOL_CSM_UI_REVIEW_DIR")
                if output:
                    folder = Path(output)
                    folder.mkdir(parents=True, exist_ok=True)
                    board.grab().save(str(folder / f"server-{width}-{index}.png"))

    def test_gateway_labeled_actions_keep_authorization_and_signal_wiring(self):
        panel = self.board.internet_gateway
        self.assertFalse(panel.connect_button.isEnabled())
        panel.apply_state({"registration_state": "registered", "authorization_state": "active",
            "school_id": "123456", "public_host": "123456.district.example.test",
            "gateway_state": "disconnected", "provider_available": True})
        self.assertTrue(panel.connect_button.isEnabled())
        calls = []
        panel.connect_requested.connect(lambda: calls.append("connect"))
        panel.connect_button.click()
        self.assertEqual(calls, ["connect"])
        panel.apply_state({"registration_state": "registered", "authorization_state": "transferred",
            "school_id": "123456", "public_host": "123456.district.example.test",
            "retirement": {"state": "transferred"}})
        self.assertFalse(panel.connect_button.isEnabled())
        for button in (panel.connect_button, panel.diagnostics_button, panel.setup_button,
                       panel.details_button, panel.prepare_backup_button,
                       panel.prepare_package_button, panel.transfer_button):
            self.assertTrue(button.text())
            self.assertTrue(button.toolTip())


if __name__ == "__main__":
    unittest.main()
