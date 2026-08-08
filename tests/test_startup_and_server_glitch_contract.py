from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class StartupAndServerGlitchContractTests(unittest.TestCase):
    def test_windows_helpers_are_hidden(self):
        admin = (ROOT / "school_csm_control_center" / "windows_admin.py").read_text(encoding="utf-8")
        controller = (ROOT / "school_csm_control_center" / "web_server" / "controller.py").read_text(encoding="utf-8")
        portal = (ROOT / "school_csm_control_center" / "web_server" / "captive_portal.py").read_text(encoding="utf-8")
        self.assertIn("def hidden_process_creation_flags", admin)
        self.assertIn("creationflags=hidden_process_creation_flags()", controller)
        self.assertIn("creationflags=hidden_process_creation_flags()", portal)

    def test_server_board_uses_background_operations(self):
        board = (ROOT / "school_csm_control_center" / "ui" / "server_board.py").read_text(encoding="utf-8")
        controller = (ROOT / "school_csm_control_center" / "web_server" / "controller.py").read_text(encoding="utf-8")
        self.assertIn("self.controller.start_async", board)
        self.assertIn("self.controller.stop_async", board)
        self.assertIn("def start_async", controller)
        self.assertIn("def stop_async", controller)

    def test_first_window_paint_occurs_behind_splash(self):
        app = (ROOT / "school_csm_control_center" / "app.py").read_text(encoding="utf-8")
        self.assertIn("window.setUpdatesEnabled(False)", app)
        self.assertIn("window.setUpdatesEnabled(True)", app)
        self.assertIn("splash.finish(window)", app)


if __name__ == "__main__":
    unittest.main()
