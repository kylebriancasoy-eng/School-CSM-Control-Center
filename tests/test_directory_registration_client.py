from __future__ import annotations

import json
import os
from pathlib import Path
from threading import Event, Thread
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from school_csm_control_center.internet_gateway.directory_registration import (
    DistrictRegistrationController, DistrictRegistrationQueue,
)
from school_csm_control_center.storage.gateway_credentials import GatewayCredentialStore
from school_csm_control_center.ui.internet_gateway_panel import InternetGatewayPanel


class MemoryCredentials:
    def __init__(self): self.values = {}
    def write(self, key, value): self.values[key] = value
    def read(self, key): return self.values.get(key)
    def delete(self, key): return self.values.pop(key, None) is not None


class FakeClient:
    config = SimpleNamespace(service_url="https://district.example.test")
    def __init__(self): self.sent = []; self.decision = "pending"; self.fail = False
    def submit_directory_request(self, payload):
        if self.fail: raise OSError("offline (private detail must not be saved)")
        self.sent.append(dict(payload))
        return {"request_id": payload["request_id"], "school_id": payload["school_id"], "status": self.decision}


class DirectoryRegistrationClientTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.credentials = GatewayCredentialStore(MemoryCredentials())
        self.queue = DistrictRegistrationQueue(self.root, self.credentials)
        self.client = FakeClient()
        self.installation = "11111111-1111-4111-8111-111111111111"
        self.settings = {
            "school_id": "123456", "school_name": "Example Elementary School",
            "school_district": "Schools District of Motiong", "school_division": "Schools Division of Samar",
            "school_head": "Must stay local", "school_email": "private@example.test",
            "school_contact": "Private", "school_logo_path": "school_logo.png", "survey_records": ["private"],
        }

    def prepare(self, **kwargs):
        return self.queue.queue(self.settings, self.installation, self.client.config.service_url, **kwargs)

    def test_offline_queue_survives_restart_with_same_protected_receipt(self):
        queued = self.prepare()
        receipt = self.credentials.load_directory_request_receipt()
        self.client.fail = True
        with self.assertRaises(OSError): self.queue.send(self.client)
        restored = DistrictRegistrationQueue(self.root, self.credentials)
        self.assertEqual(restored.load()["status"], "queued")
        self.client.fail = False
        self.assertEqual(restored.send(self.client)["status"], "pending")
        self.assertEqual(self.client.sent[0]["request_id"], queued["payload"]["request_id"])
        self.assertEqual(self.client.sent[0]["request_secret"], receipt["request_secret"])

    def test_payload_has_only_approved_identity_fields_and_secret_never_in_json(self):
        self.prepare()
        self.queue.send(self.client)
        self.assertEqual(set(self.client.sent[0]), {
            "school_id", "school_name", "school_district", "school_division",
            "request_id", "installation_id", "request_secret",
        })
        text = self.queue.path.read_text()
        self.assertNotIn(self.client.sent[0]["request_secret"], text)
        for private in ("Must stay local", "private@example.test", "survey_records", "school_logo"):
            self.assertNotIn(private, text)
        self.assertEqual(self.queue.path.parent.name, "gateway")

    def test_pending_correction_keeps_identity_but_approved_roster_is_not_rewritten(self):
        first = self.prepare()
        self.settings["school_name"] = "Corrected School"
        edited = self.prepare()
        self.assertEqual(edited["payload"]["request_id"], first["payload"]["request_id"])
        self.client.decision = "approved"
        self.queue.send(self.client)
        self.settings["school_name"] = "Local only edit"
        frozen = self.prepare()
        self.assertEqual(frozen["payload"]["school_name"], "Corrected School")

    def test_rejected_correction_or_explicit_resubmission_gets_new_receipt(self):
        first = self.prepare()
        self.client.decision = "rejected"
        self.queue.send(self.client)
        self.assertEqual(self.prepare()["payload"]["request_id"], first["payload"]["request_id"])
        replacement = self.prepare(resubmit=True)
        self.assertNotEqual(replacement["payload"]["request_id"], first["payload"]["request_id"])

    def test_school_device_or_provider_changes_never_reuse_the_receipt(self):
        first = self.prepare()
        self.settings["school_id"] = "234567"
        second = self.prepare()
        self.assertNotEqual(first["payload"]["request_id"], second["payload"]["request_id"])
        self.installation = "22222222-2222-4222-8222-222222222222"
        third = self.prepare()
        self.assertNotEqual(second["payload"]["request_id"], third["payload"]["request_id"])
        with self.assertRaisesRegex(ValueError, "does not match"):
            self.queue.send(SimpleNamespace(config=SimpleNamespace(service_url="https://wrong.example.test")))

    def test_corrupt_state_is_preserved_and_mismatched_responses_are_rejected(self):
        self.prepare()
        before = self.queue.path.read_bytes()
        with patch.object(self.client, "submit_directory_request", return_value={
            "request_id": "mismatch", "school_id": "123456", "status": "approved",
        }):
            with self.assertRaises(ValueError): self.queue.send(self.client)
        self.assertEqual(self.queue.path.read_bytes(), before)
        self.queue.path.write_text("corrupt existing state", encoding="utf-8")
        with self.assertRaises(ValueError): self.prepare()
        self.assertEqual(self.queue.path.read_text(), "corrupt existing state")

    def test_stale_network_response_cannot_replace_new_local_request(self):
        self.prepare()
        real_send = self.client.submit_directory_request
        def switch_school(payload):
            self.settings["school_id"] = "234567"
            self.prepare()
            return real_send(payload)
        with patch.object(self.client, "submit_directory_request", side_effect=switch_school):
            result = self.queue.send(self.client)
        self.assertEqual(result["payload"]["school_id"], "234567")
        self.assertEqual(result["status"], "queued")

    def test_failed_replacement_preserves_the_durable_protected_receipt(self):
        first = self.prepare()
        old_receipt = self.credentials.load_directory_request_receipt()
        before = self.queue.path.read_bytes()
        with patch("school_csm_control_center.internet_gateway.directory_registration.atomic_write_json",
                   side_effect=OSError("disk unavailable")):
            with self.assertRaises(OSError):
                self.queue.queue({**self.settings, "school_id": "234567"},
                                 self.installation, self.client.config.service_url)
        self.assertEqual(self.queue.path.read_bytes(), before)
        self.assertEqual(self.credentials.load_directory_request_receipt(), old_receipt)
        self.assertEqual(self.queue.send(self.client)["payload"]["request_id"], first["payload"]["request_id"])
        self.assertEqual(self.client.sent[0]["request_secret"], old_receipt["request_secret"])

    def test_crash_between_protected_and_json_writes_keeps_old_receipt_recoverable(self):
        class SimulatedCrash(BaseException):
            pass

        first = self.prepare()
        old_receipt = self.credentials.load_directory_request_receipt()
        for _ in range(3):
            # A hard interruption bypasses the ordinary exception rollback.
            with patch("school_csm_control_center.internet_gateway.directory_registration.atomic_write_json",
                       side_effect=SimulatedCrash):
                with self.assertRaises(SimulatedCrash):
                    self.queue.queue({**self.settings, "school_id": "234567"},
                                     self.installation, self.client.config.service_url)
            self.assertEqual(self.credentials.load_directory_request_receipt(first["payload"]["request_id"]), old_receipt)
        restored = DistrictRegistrationQueue(self.root, self.credentials)
        self.assertEqual(restored.queue(self.settings, self.installation,
                                       self.client.config.service_url)["payload"]["request_id"], first["payload"]["request_id"])
        self.assertEqual(restored.send(self.client)["status"], "pending")
        self.assertEqual(self.client.sent[0]["request_secret"], old_receipt["request_secret"])
        self.assertNotIn(old_receipt["request_secret"], self.queue.path.read_text())

    def test_failed_protected_rollback_still_leaves_the_durable_receipt_usable(self):
        first = self.prepare()
        old_receipt = self.credentials.load_directory_request_receipt()
        real_save = self.credentials.save_directory_request_receipt
        calls = 0

        def staged_save(receipt, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("protected rollback unavailable")
            return real_save(receipt, **kwargs)

        with patch.object(self.credentials, "save_directory_request_receipt", side_effect=staged_save), \
             patch("school_csm_control_center.internet_gateway.directory_registration.atomic_write_json",
                   side_effect=OSError("disk unavailable")):
            with self.assertRaises(OSError):
                self.queue.queue({**self.settings, "school_id": "234567"},
                                 self.installation, self.client.config.service_url)
        self.assertEqual(calls, 2)
        self.assertEqual(self.credentials.load_directory_request_receipt(first["payload"]["request_id"]), old_receipt)
        self.assertEqual(self.queue.send(self.client)["payload"]["request_id"], first["payload"]["request_id"])

    def test_receipt_storage_reads_legacy_format_and_keeps_previous_on_replacement(self):
        first = self.prepare()
        old_receipt = self.credentials.load_directory_request_receipt()
        self.credentials._manager.write(self.credentials.directory_request_target, json.dumps(old_receipt))
        self.assertEqual(self.credentials.load_directory_request_receipt(first["payload"]["request_id"]), old_receipt)
        replacement = self.queue.queue({**self.settings, "school_id": "234567"},
                                     self.installation, self.client.config.service_url)
        self.assertEqual(self.credentials.load_directory_request_receipt(first["payload"]["request_id"]), old_receipt)
        self.assertEqual(self.credentials.load_directory_request_receipt()["request_id"], replacement["payload"]["request_id"])

    def test_oversized_replacement_does_not_discard_existing_receipt(self):
        self.prepare()
        old_receipt = self.credentials.load_directory_request_receipt()
        before = self.queue.path.read_bytes()
        with self.assertRaisesRegex(ValueError, "protected storage limit"):
            self.queue.queue(self.settings, self.installation, "https://example.test/" + "a" * 2000)
        self.assertEqual(self.queue.path.read_bytes(), before)
        self.assertEqual(self.credentials.load_directory_request_receipt(), old_receipt)

    def _finish_async_without_delivering_qt_signal(self, controller):
        workers = []

        def worker_factory(**kwargs):
            worker = Thread(**kwargs)
            workers.append(worker)
            return worker

        with patch("school_csm_control_center.internet_gateway.directory_registration.Thread",
                   side_effect=worker_factory):
            self.assertTrue(controller.send_async())
        workers[0].join(timeout=3)
        self.assertFalse(workers[0].is_alive())

    def test_queued_qt_completion_cannot_overwrite_a_newer_school_profile(self):
        app = QApplication.instance() or QApplication([])
        controller = DistrictRegistrationController(self.queue, self.client)
        self.addCleanup(controller.close)
        self.assertTrue(controller.prepare(self.settings, self.installation))
        self.client.decision = "approved"
        self._finish_async_without_delivering_qt_signal(controller)
        self.assertTrue(controller.prepare({**self.settings, "school_id": "234567"}, self.installation))
        app.processEvents()
        self.assertEqual(controller.state()["status"], "queued")
        self.assertFalse(controller.state()["busy"])
        self.assertEqual(self.queue.load()["payload"]["school_id"], "234567")
        self.assertEqual(self.queue.load()["status"], "queued")

    def test_qt_delivery_reads_the_latest_durable_request_from_another_process(self):
        app = QApplication.instance() or QApplication([])
        controller = DistrictRegistrationController(self.queue, self.client)
        self.addCleanup(controller.close)
        self.assertTrue(controller.prepare(self.settings, self.installation))
        self.client.decision = "approved"
        self._finish_async_without_delivering_qt_signal(controller)
        other_queue = DistrictRegistrationQueue(self.root, self.credentials)
        other_queue.queue({**self.settings, "school_id": "234567"},
                          self.installation, self.client.config.service_url)
        app.processEvents()
        self.assertEqual(controller.state()["status"], "queued")
        self.assertFalse(controller.state()["busy"])

    def test_stale_qt_completion_does_not_hide_newer_profile_storage_error(self):
        app = QApplication.instance() or QApplication([])
        controller = DistrictRegistrationController(self.queue, self.client)
        self.addCleanup(controller.close)
        self.assertTrue(controller.prepare(self.settings, self.installation))
        self.client.decision = "approved"
        self._finish_async_without_delivering_qt_signal(controller)
        with patch.object(self.queue, "queue", side_effect=OSError("storage unavailable")):
            self.assertFalse(controller.prepare({**self.settings, "school_id": "234567"}, self.installation))
        app.processEvents()
        self.assertEqual(controller.state()["status"], "storage_error")
        self.assertFalse(controller.state()["busy"])

    def test_failed_qt_delivery_uses_a_newer_request_saved_by_another_process(self):
        app = QApplication.instance() or QApplication([])
        controller = DistrictRegistrationController(self.queue, self.client)
        self.addCleanup(controller.close)
        self.client.decision = "approved"
        self.prepare()
        self.queue.send(self.client)
        self.assertTrue(controller.prepare(self.settings, self.installation))
        self.assertEqual(controller.state()["status"], "approved")
        self.client.fail = True
        self._finish_async_without_delivering_qt_signal(controller)
        other_queue = DistrictRegistrationQueue(self.root, self.credentials)
        other_queue.queue({**self.settings, "school_id": "234567"},
                          self.installation, self.client.config.service_url)
        app.processEvents()
        self.assertEqual(controller.state()["status"], "queued")
        self.assertIn("could not be confirmed", controller.state()["detail"])
        self.assertFalse(controller.state()["busy"])

    def test_closed_controller_ignores_a_worker_finishing_after_shutdown(self):
        app = QApplication.instance() or QApplication([])
        controller = DistrictRegistrationController(self.queue, self.client)
        self.assertTrue(controller.prepare(self.settings, self.installation))
        entered, release = Event(), Event()
        real_submit = self.client.submit_directory_request

        def blocked_submit(payload):
            entered.set()
            if not release.wait(timeout=3):
                raise TimeoutError("test worker was not released")
            return real_submit(payload)

        workers = []

        def worker_factory(**kwargs):
            worker = Thread(**kwargs)
            workers.append(worker)
            return worker

        with patch.object(self.client, "submit_directory_request", side_effect=blocked_submit), \
             patch("school_csm_control_center.internet_gateway.directory_registration.Thread", side_effect=worker_factory):
            self.assertTrue(controller.send_async())
            try:
                self.assertTrue(entered.wait(timeout=3))
                controller.close()
                before = controller.state()
            finally:
                controller.close()
                release.set()
                workers[0].join(timeout=3)
        self.assertFalse(workers[0].is_alive())
        app.processEvents()
        self.assertEqual(controller.state(), before)

    def test_provider_unavailable_and_storage_error_do_not_block_local_registration(self):
        app = QApplication.instance() or QApplication([])
        controller = DistrictRegistrationController(self.queue, None)
        self.assertFalse(controller.prepare(self.settings, self.installation))
        self.assertEqual(controller.state()["status"], "provider_unavailable")
        self.assertFalse(self.queue.path.exists())
        controller.client = self.client
        with patch.object(self.credentials, "save_directory_request_receipt", side_effect=OSError("denied")):
            self.assertFalse(controller.prepare(self.settings, self.installation))
        self.assertEqual(controller.state()["status"], "storage_error")
        self.assertNotIn("denied", controller.state()["detail"])
        controller.close()
        app.processEvents()

    def test_school_panel_has_visible_review_status_and_safe_retry_button(self):
        app = QApplication.instance() or QApplication([])
        panel = InternetGatewayPanel()
        self.assertFalse(panel.directory_sync_button.isEnabled())
        panel.apply_directory_state({"status": "pending", "detail": "Await district review", "busy": False})
        self.assertIn("Pending approval", panel.directory_status.text())
        self.assertTrue(panel.directory_sync_button.isEnabled())
        self.assertFalse(panel.connect_button.isEnabled())
        panel.apply_directory_state({"status": "rejected", "busy": False})
        self.assertEqual(panel.directory_sync_button.text(), "Submit corrected request")
        panel.apply_directory_state({"status": "pending", "busy": True})
        self.assertFalse(panel.directory_sync_button.isEnabled())
        panel.deleteLater()
        app.processEvents()

    def test_completed_profile_is_queued_by_the_window_without_gateway_activation(self):
        from school_csm_control_center.internet_gateway.config import GatewayProviderConfig
        from school_csm_control_center.ui.main_window import SchoolCSMControlCenterWindow
        from tests.test_school_registration import _complete_settings
        app = QApplication.instance() or QApplication([])
        complete = _complete_settings(self.root)
        config = GatewayProviderConfig(self.client.config.service_url, "district.example.test",
            {"test": b"k" * 32}, ("127.0.0.1",))
        with patch("school_csm_control_center.ui.main_window.load_gateway_provider_config", return_value=config), \
             patch("school_csm_control_center.ui.main_window.GatewayProviderClient", return_value=self.client), \
             patch("school_csm_control_center.ui.main_window.GatewayCredentialStore", return_value=self.credentials), \
             patch("school_csm_control_center.ui.mrs_printing_board.discover_physical_printers", return_value=[]):
            window = SchoolCSMControlCenterWindow(self.root)
            try:
                window.show()
                app.processEvents()
                with patch.object(window.district_registration, "send_async", return_value=True) as send:
                    window._school_information_saved(complete)
                self.assertTrue(send.called)
                state = window.district_registration.queue.load()
                self.assertEqual(state["status"], "queued")
                self.assertEqual(state["payload"]["school_id"], "123627")
                self.assertTrue(window.school_registration_complete)
                self.assertIn("Queued locally", window.server_board.internet_gateway.directory_status.text())
                self.assertEqual(window.gateway_controller.state()["registration_state"], "not_configured")
                self.assertIsNone(self.credentials.load_tunnel_credential())
                self.assertIsNone(self.credentials.load_installation_secret())
                with patch.object(window, "_show_school_internet_activation") as setup:
                    window._request_gateway_connection()
                    setup.assert_called_once()
                # Initial profile completion opens the optional guide; local
                # use is available without approval or any activation secret.
                window._registration_complete = False
                with patch.object(window.district_registration, "send_async", return_value=True):
                    window._school_information_saved(complete)
                self.assertEqual(window.school_information.workspace_stack.currentIndex(), 1)
                self.assertTrue(window.school_workspace_overlay.dismissible)
                with patch.object(window, "_open_gateway_setup") as setup:
                    with patch.object(window.district_registration, "state", return_value={"status": "pending"}):
                        window._begin_school_internet_activation()
                        setup.assert_not_called()
                    with patch.object(window.district_registration, "state", return_value={"status": "approved"}):
                        window._begin_school_internet_activation()
                        setup.assert_called_once_with(guided=True)
                with patch.object(window.gateway_controller, "state", return_value={
                        "school_id": "123627", "public_host": "school.example.test", "registration_state": "registered"}), \
                     patch.object(window, "show_board") as navigate, \
                     patch.object(window, "_open_gateway_setup") as setup:
                    window._begin_school_internet_activation()
                    navigate.assert_called_once_with("server")
                    setup.assert_not_called()
                window.school_information.activation_local_button.click()
                self.assertTrue(window.school_workspace_overlay.isHidden())
            finally:
                window.prepare_for_application_quit()
                window.close()
                window.deleteLater()
                app.processEvents()


if __name__ == "__main__":
    unittest.main()
