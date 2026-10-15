from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication, QLineEdit, QWidget

from school_csm_control_center.ui.school_information_board import SchoolInformationBoard
from school_csm_control_center.ui.internet_gateway_overlays import InternetGatewaySetupOverlay
from tests.test_school_registration import _complete_settings


class GuidedSchoolActivationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.previous_font = cls.app.font()
        # The offscreen platform may not discover installed Windows fonts.
        font = Path("C:/Windows/Fonts/segoeui.ttf")
        if font.is_file():
            QFontDatabase.addApplicationFont(str(font))
            cls.app.setFont(QFont("Segoe UI", 10))

    @classmethod
    def tearDownClass(cls):
        cls.app.setFont(cls.previous_font)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def board(self, complete=True):
        if complete:
            _complete_settings(self.root)
        board = SchoolInformationBoard(self.root)
        board.set_registration_required(not complete)
        self.addCleanup(board.deleteLater)
        return board

    def test_incomplete_profile_cannot_skip_registration_using_optional_tab(self):
        board = self.board(False)
        board.set_registration_required(True)
        self.assertEqual(board.section_stack.count(), 3)
        self.assertFalse(board.internet_button.isEnabled())
        board.show_internet_activation()
        self.assertEqual(board.workspace_stack.currentIndex(), 0)
        self.assertFalse(board.activation_button.isEnabled())
        self.assertFalse(board.activation_local_button.isEnabled())

    def test_approval_stages_and_local_use_do_not_activate_anything(self):
        board = self.board()
        board.set_registration_required(False)
        board.show_internet_activation()
        self.assertEqual(board.workspace_stack.currentIndex(), 1)
        for status in ("queued", "pending", "rejected", "storage_error", "provider_unavailable"):
            board.apply_activation_state(directory={"status": status, "busy": False})
            self.assertFalse(board.activation_button.isEnabled(), status)
            self.assertTrue(board.activation_local_button.isEnabled(), status)
        self.assertFalse(board.activation_check_button.isEnabled())
        board.apply_activation_state(directory={"status": "approved", "busy": False})
        self.assertTrue(board.activation_button.isEnabled())
        self.assertIn("separate completion code", board.activation_steps[2].text())
        board.apply_activation_state(directory={"status": "approved", "busy": True})
        self.assertFalse(board.activation_button.isEnabled())
        self.assertFalse(board.activation_check_button.isEnabled())

    def test_registered_retired_and_broken_existing_setups_never_offer_initial_activation(self):
        board = self.board()
        board.apply_activation_state(directory={"status": "approved"})
        for state in (
            {"registration_state": "registered", "school_id": "123627", "public_host": "school.example.test"},
            {"registration_state": "registration_error", "school_id": "123627", "public_host": "school.example.test"},
            {"retirement": {"reason": "transferred"}},
        ):
            board.apply_activation_state(gateway=state)
            self.assertTrue(board.activation_button.isHidden())
            self.assertTrue(board.activation_check_button.isHidden())
            self.assertFalse(board.activation_server_button.isHidden())

    def test_activation_uses_saved_identity_and_tab_selection_is_clear(self):
        board = self.board()
        before = board.activation_identity.text()
        board.school_id.setText("999999")
        board.school_name.setText("Unsaved School")
        board.apply_activation_state(directory={"status": "approved"})
        board.internet_button.click()
        self.assertEqual(board.activation_identity.text(), before)
        self.assertTrue(board.internet_button.isChecked())
        board.internet_button.click()
        self.assertTrue(board.internet_button.isChecked())
        self.assertFalse(board.profile_button.isChecked())
        board.profile_button.click()
        self.assertEqual(board.workspace_stack.currentIndex(), 0)
        self.assertTrue(board.profile_button.isChecked())

    def test_guided_overlay_has_named_actions_and_no_pilot_token_fields(self):
        parent = QWidget()
        self.addCleanup(parent.deleteLater)
        overlay = InternetGatewaySetupOverlay(parent)
        overlay.configure(school_id="123627", school_name="Test Elementary School",
                          provider_available=True, guided=True)
        self.assertTrue(overlay.direct_mode.isHidden())
        self.assertTrue(overlay.route_heading.isHidden())
        self.assertTrue(overlay.direct_fields.isHidden())
        self.assertTrue(overlay.register_button.isEnabled())
        self.assertEqual(overlay.register_button.text(), "Open secure activation page")
        self.assertEqual(overlay.redeem_button.text(), "Finish activation")
        self.assertEqual(overlay.completion_code.echoMode(), QLineEdit.EchoMode.Password)
        events = []
        overlay.browser_authorization_requested.connect(events.append)
        overlay.register_button.click()
        self.assertEqual(events, ["registration"])
        overlay.set_status("Working", busy=True)
        overlay._submit()
        self.assertEqual(events, ["registration"])
        overlay.set_status("Ready")
        self.assertFalse(overlay.direct_mode.isEnabled())
        overlay.completion_code.setText("private-test-completion")
        overlay.clear_sensitive_fields()
        self.assertEqual(overlay.completion_code.text(), "")
        overlay.configure(school_id="123627", school_name="School", provider_available=False, guided=True)
        self.assertFalse(overlay.register_button.isEnabled())
        self.assertTrue(overlay.direct_fields.isHidden())

    def test_optional_page_remains_scrollable_at_small_window_sizes(self):
        board = self.board()
        board.apply_activation_state(directory={"status": "approved"})
        board.show_internet_activation()
        for width, height in ((740, 560), (1100, 780)):
            board.resize(width, height)
            board.show()
            self.app.processEvents()
            self.assertLessEqual(board.minimumSizeHint().width(), width)
            for button in (board.activation_button, board.activation_local_button):
                bottom = button.mapTo(board, QPoint(0, button.height())).y()
                self.assertLessEqual(bottom, board.height())
            output = os.environ.get("SCHOOL_CSM_UI_REVIEW_DIR")
            if output:
                folder = Path(output)
                folder.mkdir(parents=True, exist_ok=True)
                self.assertTrue(board.grab().save(str(folder / f"activation-{width}.png")))
        board.close()


if __name__ == "__main__":
    unittest.main()
