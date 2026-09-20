from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from school_csm_control_center.storage.control_center_settings import (
    DEFAULT_SCHOOL_DISTRICT,
    DEFAULT_SCHOOL_DIVISION,
    ControlCenterSettingsStore,
    first_incomplete_registration_section,
    school_registration_complete,
    school_registration_errors,
)
from school_csm_control_center.storage.gateway_credentials import GatewayCredentialStore
from school_csm_control_center.ui.main_window import SchoolCSMControlCenterWindow
from school_csm_control_center.ui.school_information_board import SchoolInformationBoard


def _write_logo(root: Path) -> Path:
    path = root / "data" / "csm_survey" / "school_logo.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    image = QImage(12, 12, QImage.Format.Format_ARGB32)
    image.fill(QColor("#21d9c5"))
    if not image.save(str(path), "PNG"):
        raise RuntimeError("Test logo could not be created")
    return path


def _complete_settings(root: Path) -> dict[str, object]:
    _write_logo(root)
    settings = ControlCenterSettingsStore(root).load()
    settings.update(
        {
            "school_id": "123627",
            "school_name": "Test Elementary School",
            "school_district": DEFAULT_SCHOOL_DISTRICT,
            "school_division": DEFAULT_SCHOOL_DIVISION,
            "school_logo_path": "data/csm_survey/school_logo.png",
            "school_head": "Alex School Head",
            "school_administrator": "Sam School Administrator",
            "csm_focal_person": "Casey CSM Coordinator",
            "school_address": "Motiong, Samar",
            "school_email": "school@example.test",
            "school_contact": "(055) 123-4567",
        }
    )
    return ControlCenterSettingsStore(root).save(settings)


class _MemoryCredentialManager:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def write(self, target: str, secret: str) -> None:
        self.values[target] = secret

    def read(self, target: str) -> str | None:
        return self.values.get(target)

    def delete(self, target: str) -> bool:
        return self.values.pop(target, None) is not None


class SchoolRegistrationValidationTests(unittest.TestCase):
    def test_new_install_has_only_the_requested_district_and_division_presets(self) -> None:
        with TemporaryDirectory() as temporary:
            settings = ControlCenterSettingsStore(Path(temporary)).load()
            self.assertEqual(settings["school_id"], "")
            self.assertEqual(settings["school_name"], "")
            self.assertEqual(settings["school_district"], DEFAULT_SCHOOL_DISTRICT)
            self.assertEqual(settings["school_division"], DEFAULT_SCHOOL_DIVISION)

    def test_every_requested_field_and_durable_logo_is_required(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings = ControlCenterSettingsStore(root).load()
            errors = school_registration_errors(settings, data_root=root)
            self.assertEqual(
                set(errors),
                {
                    "school_id",
                    "school_name",
                    "school_logo_path",
                    "school_head",
                    "school_administrator",
                    "csm_focal_person",
                    "school_address",
                    "school_email",
                    "school_contact",
                },
            )
            self.assertEqual(first_incomplete_registration_section(settings, data_root=root), 0)
            self.assertFalse(school_registration_complete(settings, data_root=root))

            complete = _complete_settings(root)
            self.assertEqual(school_registration_errors(complete, data_root=root), {})
            self.assertTrue(school_registration_complete(complete, data_root=root))

            logo = root / "data" / "csm_survey" / "school_logo.png"
            logo.write_text("not an image", encoding="utf-8")
            self.assertIn(
                "school_logo_path",
                school_registration_errors(complete, data_root=root),
            )
            logo.unlink()
            self.assertIn(
                "school_logo_path",
                school_registration_errors(complete, data_root=root),
            )

    def test_corrupt_png_checksum_is_an_invalid_logo_not_a_startup_error(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            complete = _complete_settings(root)
            logo = root / "data" / "csm_survey" / "school_logo.png"
            payload = bytearray(logo.read_bytes())
            chunk_type = payload.find(b"IDAT")
            self.assertGreater(chunk_type, 4)
            chunk_size = int.from_bytes(payload[chunk_type - 4 : chunk_type], "big")
            checksum_index = chunk_type + 4 + chunk_size
            payload[checksum_index] ^= 0x01
            logo.write_bytes(payload)

            errors = school_registration_errors(complete, data_root=root)
            self.assertIn("school_logo_path", errors)
            self.assertFalse(school_registration_complete(complete, data_root=root))

    def test_upgraded_profile_without_school_administrator_is_incomplete(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings = _complete_settings(root)
            settings["school_administrator"] = ""
            saved = ControlCenterSettingsStore(root).save(settings)
            errors = school_registration_errors(saved, data_root=root)
            self.assertEqual(list(errors), ["school_administrator"])
            self.assertEqual(first_incomplete_registration_section(saved, data_root=root), 1)


class SchoolRegistrationUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_three_sections_block_forward_progress_and_persist_when_complete(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            board = SchoolInformationBoard(root)
            board.set_registration_required(True)
            self.assertEqual(board.section_stack.count(), 3)
            self.assertEqual(board._step, 0)
            self.assertEqual(board.school_id.text(), "")
            self.assertEqual(board.school_name.text(), "")
            self.assertEqual(board.district.text(), DEFAULT_SCHOOL_DISTRICT)
            self.assertEqual(board.division.text(), DEFAULT_SCHOOL_DIVISION)

            board.continue_button.click()
            self.app.processEvents()
            self.assertEqual(board._step, 0)
            self.assertFalse(board.validation_message.isHidden())

            board.school_id.setText("123627")
            board.school_name.setText("Test Elementary School")
            _write_logo(root)
            board._refresh_logo_preview()
            board.continue_button.click()
            self.app.processEvents()
            self.assertEqual(board._step, 1)

            board.school_head.setText("Alex School Head")
            board.school_administrator.setText("Sam School Administrator")
            board.csm_focal_person.setText("Casey CSM Coordinator")
            board.continue_button.click()
            self.app.processEvents()
            self.assertEqual(board._step, 2)

            board.address.setText("Motiong, Samar")
            board.email.setText("not-an-email")
            board.contact.setText("123")
            board.continue_button.click()
            self.app.processEvents()
            self.assertEqual(board._step, 2)
            board.email.setText("school@example.test")
            board.contact.setText("(055) 123-4567")
            saved_events: list[dict[str, object]] = []
            board.school_information_saved.connect(saved_events.append)
            board.continue_button.click()
            self.app.processEvents()

            self.assertEqual(len(saved_events), 1)
            saved = ControlCenterSettingsStore(root).load()
            self.assertTrue(school_registration_complete(saved, data_root=root))
            self.assertEqual(saved["school_administrator"], "Sam School Administrator")
            self.assertEqual(saved["csm_focal_person"], "Casey CSM Coordinator")
            board.deleteLater()

    def test_registered_gateway_school_id_survives_profile_reload(self) -> None:
        with TemporaryDirectory() as temporary:
            board = SchoolInformationBoard(Path(temporary))
            board.set_registered_school_id("123627")
            board.load_settings()
            self.assertEqual(board.school_id.text(), "123627")
            self.assertTrue(board.school_id.isReadOnly())
            board.deleteLater()

    def test_replacing_a_legacy_logo_persists_the_new_canonical_logo(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            legacy_logo = _write_logo(root)
            legacy_logo = legacy_logo.replace(legacy_logo.with_name("legacy-logo.png"))
            settings = ControlCenterSettingsStore(root).load()
            settings.update(
                {
                    "school_id": "123627",
                    "school_name": "Test Elementary School",
                    "school_logo_path": "data/csm_survey/legacy-logo.png",
                }
            )
            ControlCenterSettingsStore(root).save(settings)
            board = SchoolInformationBoard(root)
            replacement = QImage(12, 12, QImage.Format.Format_ARGB32)
            replacement.fill(QColor("#ff5577"))
            self.assertTrue(replacement.save(str(board.logo_pending), "PNG"))
            board.continue_button.click()
            self.app.processEvents()

            saved = ControlCenterSettingsStore(root).load()
            self.assertEqual(saved["school_logo_path"], "data/csm_survey/school_logo.png")
            self.assertTrue(board.logo_target.is_file())
            self.assertTrue(legacy_logo.is_file())
            board.deleteLater()

    def test_incomplete_registration_is_a_nondismissible_startup_gate(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            window = SchoolCSMControlCenterWindow(root)
            window.resize(1100, 760)
            window.show()
            self.app.processEvents()
            self.assertFalse(window.school_registration_complete)
            self.assertTrue(window.school_workspace_overlay.isVisible())
            self.assertFalse(window.school_workspace_overlay.dismissible)
            self.assertFalse(window.board_stack.isEnabled())
            self.assertFalse(window.dashboard_nav.isEnabled())
            self.assertFalse(window.history_nav.isEnabled())
            self.assertFalse(window.mrs_nav.isEnabled())
            self.assertFalse(window.server_nav.isEnabled())
            self.assertTrue(window.school_info_nav.isEnabled())
            window.school_workspace_overlay.close_overlay()
            self.assertTrue(window.school_workspace_overlay.isVisible())
            window.dashboard.add_requested.emit()
            self.app.processEvents()
            self.assertFalse(window.drawer.isVisible())
            self.assertTrue(window.school_workspace_overlay.isVisible())
            window.dashboard.print_requested.emit()
            self.app.processEvents()
            self.assertFalse(window.print_overlay.isVisible())
            self.assertTrue(window.school_workspace_overlay.isVisible())
            window.show_board("history")
            self.assertTrue(window.school_workspace_overlay.isVisible())
            self.assertFalse(window.background_startup_readiness()[0])
            window.close()
            window.deleteLater()
            self.app.processEvents()

    def test_complete_registration_opens_the_normal_workspace(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            _complete_settings(root)
            window = SchoolCSMControlCenterWindow(root)
            window.resize(1100, 760)
            window.show()
            self.app.processEvents()
            self.assertTrue(window.school_registration_complete)
            self.assertFalse(window.school_workspace_overlay.isVisible())
            self.assertTrue(window.school_workspace_overlay.dismissible)
            self.assertTrue(window.board_stack.isEnabled())
            self.assertTrue(window.dashboard_nav.isEnabled())
            self.assertTrue(window.history_nav.isEnabled())
            self.assertTrue(window.mrs_nav.isEnabled())
            self.assertTrue(window.server_nav.isEnabled())
            window.close()
            window.deleteLater()
            self.app.processEvents()

    def test_older_restored_profile_reactivates_registration_gate_immediately(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings = _complete_settings(root)
            protected = GatewayCredentialStore(_MemoryCredentialManager())
            credential_patch = patch(
                "school_csm_control_center.ui.main_window.GatewayCredentialStore",
                return_value=protected,
            )
            credential_patch.start()
            self.addCleanup(credential_patch.stop)
            window = SchoolCSMControlCenterWindow(root)
            window.resize(1100, 760)
            window.show()
            self.app.processEvents()
            settings["school_administrator"] = ""
            ControlCenterSettingsStore(root).save(settings)
            window._pending_gateway_transfer_mode = "server_and_data"
            receipt = {
                "verified": True,
                "transaction_id": "registration-gate-test",
                "manifest_sha256": "a" * 64,
                "active_tree_sha256": "b" * 64,
                "record_counts": {},
            }
            window._gateway_operation_progress(
                {
                    "operation": "migration_import",
                    "busy": False,
                    "result": {"data_validation": receipt},
                }
            )
            self.app.processEvents()

            self.assertFalse(window.school_registration_complete)
            self.assertFalse(window.board_stack.isEnabled())
            self.assertTrue(window.school_workspace_overlay.isVisible())
            self.assertFalse(window.school_workspace_overlay.dismissible)
            self.assertEqual(
                window._deferred_gateway_transfer_request,
                {
                    "transfer_mode": "server_and_data",
                    "data_validation": receipt,
                },
            )
            window.close()
            window.deleteLater()
            self.app.processEvents()

    def test_deferred_authority_receipt_survives_registration_restart(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings = _complete_settings(root)
            settings["school_administrator"] = ""
            ControlCenterSettingsStore(root).save(settings)
            protected = GatewayCredentialStore(_MemoryCredentialManager())
            request = {
                "transfer_mode": "server_and_data",
                "data_validation": {
                    "verified": True,
                    "transaction_id": "33333333-3333-4333-8333-333333333333",
                    "manifest_sha256": "a" * 64,
                    "active_tree_sha256": "b" * 64,
                    "record_counts": {},
                },
            }
            with patch(
                "school_csm_control_center.ui.main_window.GatewayCredentialStore",
                return_value=protected,
            ):
                first = SchoolCSMControlCenterWindow(root)
                self.assertTrue(
                    first._persist_deferred_gateway_transfer_request(request)
                )
                first.close()
                first.deleteLater()
                self.app.processEvents()

                restarted = SchoolCSMControlCenterWindow(root)
                restarted.resize(1100, 760)
                restarted.show()
                self.app.processEvents()
                self.assertEqual(
                    restarted._deferred_gateway_transfer_request,
                    request,
                )
                self.assertTrue(restarted.school_workspace_overlay.isVisible())
                restarted._clear_deferred_gateway_transfer_request()
                self.assertIsNone(restarted._deferred_gateway_transfer_request)
                self.assertFalse(
                    restarted._deferred_gateway_transfer_marker.exists()
                )
                restarted.close()
                restarted.deleteLater()
                self.app.processEvents()

    def test_deferred_receipt_clears_only_after_successful_transfer_redemption(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            _complete_settings(root)
            protected = GatewayCredentialStore(_MemoryCredentialManager())
            request = {
                "transfer_mode": "server_and_data",
                "data_validation": {
                    "verified": True,
                    "transaction_id": "33333333-3333-4333-8333-333333333333",
                    "manifest_sha256": "a" * 64,
                    "active_tree_sha256": "b" * 64,
                    "record_counts": {},
                },
            }
            with patch(
                "school_csm_control_center.ui.main_window.GatewayCredentialStore",
                return_value=protected,
            ):
                window = SchoolCSMControlCenterWindow(root)
                window.resize(1100, 760)
                window.show()
                self.app.processEvents()
                self.assertTrue(
                    window._persist_deferred_gateway_transfer_request(request)
                )
                window.gateway_transfer_overlay.open_overlay()
                window._gateway_operation_progress(
                    {
                        "operation": "completion_code",
                        "busy": False,
                        "error": "Temporary provider error",
                        "detail": "Try again.",
                    }
                )
                self.assertEqual(window._deferred_gateway_transfer_request, request)
                self.assertTrue(window._deferred_gateway_transfer_marker.is_file())

                window._gateway_operation_progress(
                    {
                        "operation": "completion_code",
                        "busy": False,
                        "result": {"ok": True},
                        "detail": "Internet Server authorization saved.",
                    }
                )
                self.assertIsNone(window._deferred_gateway_transfer_request)
                self.assertFalse(window._deferred_gateway_transfer_marker.exists())
                window.close()
                window.deleteLater()
                self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
