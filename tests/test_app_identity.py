from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from school_csm_control_center.app_identity import (
    APPLICATION_SUBTITLE,
    APP_ICON_CANDIDATES,
    FORMAL_APPLICATION_NAME,
    SHORT_APPLICATION_NAME,
    WINDOWS_APP_USER_MODEL_ID,
    apply_application_identity,
    apply_windows_window_icon,
    configure_windows_taskbar_identity,
    load_application_icon,
    resolve_application_icon,
)
from school_csm_control_center.ui.main_window import SchoolCSMControlCenterWindow


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ApplicationIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_supplied_ico_is_the_preferred_application_icon(self) -> None:
        path = resolve_application_icon(PROJECT_ROOT)
        self.assertIsNotNone(path)
        self.assertEqual(path.name, APP_ICON_CANDIDATES[0])
        self.assertTrue(path.is_file())
        self.assertFalse(load_application_icon(PROJECT_ROOT).isNull())

    def test_formal_deped_identity_is_consistent(self) -> None:
        self.assertEqual(
            FORMAL_APPLICATION_NAME,
            "DepEd Client Satisfaction Measurement System",
        )
        self.assertEqual(
            SHORT_APPLICATION_NAME,
            "DepEd Client Satisfaction Measurement System",
        )
        self.assertEqual(APPLICATION_SUBTITLE, "Local Survey Management and Analysis System")
        self.assertEqual(WINDOWS_APP_USER_MODEL_ID, "DepEd.SchoolCSMControlCenter")

    def test_application_and_real_window_use_supplied_icon(self) -> None:
        icon = apply_application_identity(self.app, PROJECT_ROOT)
        self.assertFalse(icon.isNull())
        self.assertFalse(self.app.windowIcon().isNull())
        self.assertEqual(
            Path(str(self.app.property("applicationIconPath"))),
            PROJECT_ROOT / "School CSM Control Center Icon.ico",
        )

        window = SchoolCSMControlCenterWindow(PROJECT_ROOT)
        try:
            self.assertFalse(window.windowIcon().isNull())
            self.assertEqual(
                Path(str(window.property("applicationIconPath"))),
                PROJECT_ROOT / "School CSM Control Center Icon.ico",
            )
            self.assertEqual(window.title_bar.brand_label.text(), "")
            self.assertIsNotNone(window.title_bar.brand_label.pixmap())
            self.assertFalse(window.title_bar.brand_label.pixmap().isNull())
            self.assertIn(
                "application icon",
                window.title_bar.brand_label.accessibleName().casefold(),
            )
            if self.app.platformName().casefold() == "offscreen":
                self.assertFalse(apply_windows_window_icon(window, PROJECT_ROOT))
        finally:
            window.close()
            window.deleteLater()
            self.app.processEvents()

    def test_missing_assets_are_safe_for_test_and_portable_roots(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.assertIsNone(resolve_application_icon(root))
            self.assertTrue(load_application_icon(root).isNull())

    def test_windows_taskbar_identity_can_be_registered(self) -> None:
        if sys.platform == "win32":
            self.assertTrue(configure_windows_taskbar_identity())
        else:
            self.assertFalse(configure_windows_taskbar_identity())


if __name__ == "__main__":
    unittest.main()
