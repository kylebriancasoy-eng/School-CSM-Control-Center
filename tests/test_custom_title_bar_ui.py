from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from school_csm_control_center.ui import theme
from school_csm_control_center.ui.main_window import SchoolCSMControlCenterWindow
from school_csm_control_center.ui.widgets import TooltipIconButton


EXPECTED_RESIZE_HANDLES = {
    "left",
    "right",
    "top",
    "bottom",
    "top_left",
    "top_right",
    "bottom_left",
    "bottom_right",
}


class CustomTitleBarUiTests(unittest.TestCase):
    """Offscreen behavioral contract for the frameless application shell."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.project_root = Path(self.temporary.name)
        self.window = self._make_window(self.project_root)

    def tearDown(self) -> None:
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.temporary.cleanup()

    def _make_window(self, root: Path) -> SchoolCSMControlCenterWindow:
        window = SchoolCSMControlCenterWindow(root)
        window.resize(1180, 720)
        window.showNormal()
        window.show()
        self.app.processEvents()
        return window

    def test_window_is_frameless_and_title_bar_belongs_to_its_frame(self) -> None:
        self.assertTrue(
            self.window.windowFlags() & Qt.WindowType.FramelessWindowHint
        )
        self.assertIsInstance(self.window.window_frame, QWidget)
        self.assertIs(self.window.centralWidget(), self.window.window_frame)
        self.assertEqual(self.window.title_bar.objectName(), "custom_title_bar")
        self.assertFalse(self.window.title_bar.isWindow())
        self.assertIs(self.window.title_bar.window(), self.window)
        self.assertTrue(self.window.title_bar.isAncestorOf(self.window.title_bar.brand_label))

    def test_title_bar_has_recognizable_brand_title_status_and_color_family(self) -> None:
        title_bar = self.window.title_bar
        self.assertEqual(title_bar.brand_label.objectName(), "title_bar_brand")
        self.assertEqual(title_bar.title_label.objectName(), "title_bar_title")
        self.assertEqual(title_bar.subtitle_label.objectName(), "title_bar_subtitle")
        self.assertEqual(title_bar.status_label.objectName(), "title_bar_status")
        self.assertIn("CSM", title_bar.brand_label.text().upper())
        self.assertIn("DEPED", title_bar.title_label.text().upper())
        self.assertIn("CLIENT SATISFACTION", title_bar.title_label.text().upper())
        self.assertIn("SYSTEM", title_bar.title_label.text().upper())
        self.assertTrue(title_bar.subtitle_label.text().strip())
        self.assertTrue(title_bar.status_label.text().strip())

        # Styling may live on the window rather than the child frame. Requiring
        # both a navy surface and teal/cyan accent keeps the color-family
        # contract stable without coupling the test to one selector layout.
        style = "\n".join(
            (
                self.window.styleSheet(),
                self.window.window_frame.styleSheet(),
                title_bar.styleSheet(),
            )
        ).casefold()
        navy_colors = (theme.TITLE_BG, theme.WINDOW_BG, theme.PANEL_BG)
        accent_colors = (theme.ACCENT_TEAL, theme.ACCENT_CYAN)
        self.assertTrue(any(color.casefold() in style for color in navy_colors))
        self.assertTrue(any(color.casefold() in style for color in accent_colors))

    def test_all_window_actions_are_accessible_icon_only_buttons(self) -> None:
        buttons = (
            self.window.title_bar.minimize_button,
            self.window.title_bar.maximize_button,
            self.window.title_bar.close_button,
        )
        for button in buttons:
            with self.subTest(button=button.objectName()):
                self.assertIsInstance(button, TooltipIconButton)
                self.assertEqual(button.text(), "")
                self.assertFalse(button.icon().isNull())
                self.assertTrue(button.toolTip().strip())
                self.assertTrue(button.statusTip().strip())
                self.assertTrue(button.accessibleName().strip())
                self.assertTrue(button.accessibleDescription().strip())

    def test_minimize_and_maximize_restore_buttons_are_wired_and_synchronized(self) -> None:
        title_bar = self.window.title_bar
        self.window.showNormal()
        title_bar.sync_window_state()
        self.app.processEvents()
        self.assertIn("maximiz", title_bar.maximize_button.toolTip().casefold())

        QTest.mouseClick(title_bar.minimize_button, Qt.MouseButton.LeftButton)
        self.app.processEvents()
        self.assertTrue(
            self.window.windowState() & Qt.WindowState.WindowMinimized
        )

        self.window.showNormal()
        self.app.processEvents()
        QTest.mouseClick(title_bar.maximize_button, Qt.MouseButton.LeftButton)
        self.app.processEvents()
        self.assertTrue(self.window.isMaximized())
        self.assertIn("restore", title_bar.maximize_button.toolTip().casefold())
        self.assertFalse(title_bar.maximize_button.icon().isNull())

        QTest.mouseClick(title_bar.maximize_button, Qt.MouseButton.LeftButton)
        self.app.processEvents()
        self.assertFalse(self.window.isMaximized())
        self.assertIn("maximiz", title_bar.maximize_button.toolTip().casefold())

    def test_toggle_method_and_title_bar_double_click_switch_window_state(self) -> None:
        self.window.showNormal()
        self.window.title_bar.toggle_max_restore()
        self.app.processEvents()
        self.assertTrue(self.window.isMaximized())
        self.window.title_bar.toggle_max_restore()
        self.app.processEvents()
        self.assertFalse(self.window.isMaximized())

        QTest.mouseDClick(
            self.window.title_bar,
            Qt.MouseButton.LeftButton,
            pos=QPoint(self.window.title_bar.width() // 2, self.window.title_bar.height() // 2),
        )
        self.app.processEvents()
        self.assertTrue(self.window.isMaximized())

    def test_close_button_is_wired_on_a_disposable_window(self) -> None:
        with tempfile.TemporaryDirectory() as disposable_root:
            disposable = self._make_window(Path(disposable_root))
            self.assertTrue(disposable.isVisible())
            QTest.mouseClick(
                disposable.title_bar.close_button,
                Qt.MouseButton.LeftButton,
            )
            self.app.processEvents()
            self.assertFalse(disposable.isVisible())
            disposable.deleteLater()
            self.app.processEvents()

    def test_resize_handles_cover_edges_and_follow_maximized_state(self) -> None:
        self.window.showNormal()
        self.window.resize(1100, 700)
        self.app.processEvents()
        handles = self.window.resize_handles
        self.assertEqual(set(handles), EXPECTED_RESIZE_HANDLES)

        for name, handle in handles.items():
            with self.subTest(handle=name):
                self.assertIsInstance(handle, QWidget)
                self.assertEqual(handle.objectName(), "window_resize_handle")
                self.assertEqual(handle.property("edgeName"), name)
                self.assertTrue(handle.edges)
                self.assertIs(handle.window(), self.window)
                self.assertFalse(handle.geometry().isEmpty())
                self.assertNotEqual(
                    handle.cursor().shape(),
                    Qt.CursorShape.ArrowCursor,
                )
                self.assertTrue(handle.isVisible())

        frame_rect = self.window.window_frame.rect()
        self.assertEqual(handles["left"].geometry().left(), frame_rect.left())
        self.assertEqual(handles["top"].geometry().top(), frame_rect.top())
        self.assertEqual(handles["right"].geometry().right(), frame_rect.right())
        self.assertEqual(handles["bottom"].geometry().bottom(), frame_rect.bottom())

        self.window.showMaximized()
        self.window.title_bar.sync_window_state()
        self.app.processEvents()
        self.assertTrue(self.window.isMaximized())
        self.assertTrue(all(not handle.isVisible() for handle in handles.values()))
        self.assertIn(
            "restore",
            self.window.title_bar.maximize_button.toolTip().casefold(),
        )

        self.window.showNormal()
        self.window.title_bar.sync_window_state()
        self.app.processEvents()
        self.assertTrue(all(handle.isVisible() for handle in handles.values()))
        self.assertIn(
            "maximiz",
            self.window.title_bar.maximize_button.toolTip().casefold(),
        )


if __name__ == "__main__":
    unittest.main()
