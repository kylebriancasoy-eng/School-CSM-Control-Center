from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import Mock, patch
from csm_updates.release import REPOSITORY, SCHOOL_SETUP, DISTRICT_SETUP, parse_release, stage_setup, launch_setup


def metadata(version="0.8.9", *, district=False):
    names = (DISTRICT_SETUP, "district-release.json") if district else (SCHOOL_SETUP, "release.json")
    return {"tag_name": "v" + version, "name": "Release " + version, "body": "Updates <b>are plain text</b>",
            "draft": False, "prerelease": False,
            "assets": [{"name": name, "browser_download_url": f"https://github.com/{REPOSITORY}/releases/download/v{version}/{name}"} for name in names]}


class ReleaseCheckTests(unittest.TestCase):
    def test_version_comparison_and_product_binding(self):
        info = parse_release(json.dumps(metadata()).encode())
        self.assertTrue(info.newer_than("0.8.8"))
        self.assertFalse(info.newer_than("0.8.10"))
        self.assertIn("<b>", info.notes)
        with self.assertRaises(ValueError):
            parse_release(json.dumps(metadata(district=True)).encode())
        self.assertEqual(parse_release(json.dumps(metadata(district=True)).encode(), district=True).version, "0.8.9")

    def test_rejects_unpublished_malformed_or_wrong_repository_releases(self):
        for change in ({"draft": True}, {"prerelease": True}, {"tag_name": "v9.0.0-beta"},
                       {"assets": []}, {"assets": [{"name": SCHOOL_SETUP, "browser_download_url": "https://evil.example/setup.exe"}]},
                       {"body": {}}, {"tag_name": "../../setup"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                parse_release(json.dumps({**metadata(), **change}).encode())
        with self.assertRaises(ValueError):
            parse_release(b"x" * (256 * 1024 + 1))

    def test_staging_preserves_source_and_uses_distinct_product_filename(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for district, name in ((False, SCHOOL_SETUP), (True, DISTRICT_SETUP)):
                data = b"MZ-test-setup-" + name.encode()
                (root / name).write_bytes(data)
                staged = stage_setup(root, district=district)
                try:
                    self.assertNotEqual(staged.parent, root)
                    self.assertEqual(staged.name, name)
                    self.assertEqual(staged.read_bytes(), data)
                    self.assertEqual((root / name).read_bytes(), data)
                finally:
                    shutil.rmtree(staged.parent)
            (root / SCHOOL_SETUP).write_bytes(b"not an executable")
            with self.assertRaises(ValueError): stage_setup(root)

    def test_source_application_cannot_launch_setup(self):
        with patch("csm_updates.release.sys.frozen", False, create=True):
            with self.assertRaises(RuntimeError): launch_setup(Path.cwd())

    def test_missing_bundled_setup_is_not_downloaded_or_executed(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(FileNotFoundError): stage_setup(Path(folder))


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class UpdateUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_plain_text_details_and_available_state(self):
        from csm_updates.qt import UpdateController, UpdateDialog
        control = UpdateController("0.8.8")
        control._complete(parse_release(json.dumps(metadata()).encode()))
        self.assertTrue(control.available)
        dialog = UpdateDialog(control, Path.cwd(), Mock())
        self.assertIn("<b>are plain text</b>", dialog.notes.toPlainText())
        self.assertEqual(dialog.setup_button.text(), "Update…")
        self.assertFalse(dialog.setup_button.isEnabled())
        control._complete(None)
        self.assertIn("keeps working", control.status)
        self.assertTrue(control.available)  # Preserve previously verified details offline.
        dialog.close()

    def test_district_check_uses_district_assets(self):
        from csm_updates.qt import UpdateController
        control = UpdateController("0.8.8", district=True)
        with patch("csm_updates.qt.fetch_latest", return_value=parse_release(json.dumps(metadata(district=True)).encode(), district=True)) as fetch:
            class ImmediateThread:
                def __init__(self, *, target, **kwargs): self.target = target
                def start(self): self.target()
            with patch("csm_updates.qt.Thread", ImmediateThread):
                control.check()
            fetch.assert_called_once_with(district=True)
        self.assertTrue(control.available)

    def test_district_backup_acknowledgement_and_product_specific_setup(self):
        from csm_updates.qt import UpdateController, UpdateDialog
        from PySide6.QtWidgets import QMessageBox
        control = UpdateController("0.8.8", district=True)
        exit_app = Mock()
        dialog = UpdateDialog(control, Path.cwd(), exit_app)
        self.assertIn("#f8fbef", dialog.styleSheet())
        with patch("csm_updates.qt.sys.frozen", True, create=True):
            dialog.refresh()
            self.assertFalse(dialog.setup_button.isEnabled())
            with patch("csm_updates.qt.QMessageBox.information"), patch("csm_updates.qt.launch_setup") as launch:
                dialog._open_setup()
                launch.assert_not_called()
            dialog.backup_confirmation.setChecked(True)
            self.assertTrue(dialog.setup_button.isEnabled())
            with patch("csm_updates.qt.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes), patch("csm_updates.qt.launch_setup") as launch:
                dialog._open_setup()
                launch.assert_called_once_with(Path.cwd(), district=True)
        exit_app.assert_called_once()
        dialog.close()

    def test_title_bar_has_accessible_bell_action(self):
        from PySide6.QtWidgets import QMainWindow
        from school_csm_control_center.ui.title_bar import BrandedTitleBar
        host = QMainWindow()
        bar = BrandedTitleBar(host)
        clicked = Mock()
        bar.updates_requested.connect(clicked)
        bar.update_button.click()
        clicked.assert_called_once()
        self.assertEqual(bar.update_button.toolTip(), "Software updates")
        self.assertFalse(bar.update_button.icon().isNull())
        host.close()

    def test_canceled_or_failed_setup_keeps_application_open(self):
        from csm_updates.qt import UpdateController, UpdateDialog
        from PySide6.QtWidgets import QMessageBox
        exit_app = Mock()
        dialog = UpdateDialog(UpdateController("0.8.8"), Path.cwd(), exit_app)
        with patch("csm_updates.qt.QMessageBox.question", return_value=QMessageBox.StandardButton.No), patch("csm_updates.qt.launch_setup") as launch:
            dialog._open_setup()
            launch.assert_not_called()
        with patch("csm_updates.qt.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes), patch("csm_updates.qt.launch_setup", side_effect=OSError("Canceled")), patch("csm_updates.qt.QMessageBox.warning"):
            dialog._open_setup()
        exit_app.assert_not_called()
        with patch("csm_updates.qt.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes), patch("csm_updates.qt.launch_setup") as launch:
            dialog._open_setup()
            launch.assert_called_once()
        exit_app.assert_called_once()
        dialog.close()


if __name__ == "__main__": unittest.main()
