from datetime import timedelta
import json
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4
from unittest.mock import Mock

from registration_service.core import RegistrationRepository, RegistrationService, ServiceError
from registration_service import pilot_monitoring as pilot


class PilotMonitoringTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repository = RegistrationRepository(Path(self.temp.name) / "district.sqlite3")
        self.provisioner = Mock()
        self.service = RegistrationService(self.repository, managed_domain="district.example", provisioner=self.provisioner)
        for school in ("123627", "123642"):
            self.service.save_directory_school(school_id=school, school_name="School " + school,
                school_district="Motiong", school_division="Samar")
        self.code = self.service.issue_directory_activation_code("123627", valid_for=timedelta(hours=1))
        self.device = str(uuid4())
        self.secret = "a" * 64
        self.request = dict(school_id="123627", installation_id=self.device,
            public_host="123627.district.example", activation_code=self.code, monitoring_secret=self.secret)
        self.payload = dict(application_version="0.8.7", local_server_state="running",
            gateway_state="connected", survey_status="online", error_code="")

    def test_link_and_status_preserve_tunnel_and_isolate_other_school(self):
        pilot.link(self.service, **self.request)
        self.assertEqual(self.service.district_school_detail("123627")["health_status"], "unseen")
        pilot.heartbeat(self.service, installation_id=self.device, secret=self.secret, payload=self.payload)
        school = self.service.district_school_detail("123627")
        self.assertEqual(school["health_status"], "connected")
        self.assertEqual(school["monitoring_mode"], "external_pilot")
        self.assertEqual(school["public_host"], self.request["public_host"])
        self.assertEqual(self.service.district_school_detail("123642")["health_status"], "pending")
        self.assertEqual(self.service.district_overview()["counts"]["connected"], 1)
        self.provisioner.provision.assert_not_called()
        self.provisioner.rotate.assert_not_called()
        self.provisioner.revoke.assert_not_called()
        with self.repository.read() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM installations").fetchone()[0], 0)
            row = connection.execute("SELECT * FROM pilot_monitors").fetchone()
            self.assertNotIn(self.secret, json.dumps(dict(row)))
        with self.assertRaises(ServiceError):
            self.service.authenticate_installation(self.device, self.secret)

    def test_link_is_authorized_single_use_and_retry_safe(self):
        for changes in ({"activation_code": "wrong"}, {"school_id": "123642", "public_host": "123642.district.example"},
                        {"public_host": "evil.example"}, {"monitoring_secret": "bad"}):
            with self.subTest(changes=changes), self.assertRaises(ServiceError):
                pilot.link(self.service, **{**self.request, **changes})
        self.assertTrue(pilot.link(self.service, **self.request)["linked"])
        self.assertTrue(pilot.link(self.service, **self.request)["linked"])
        with self.assertRaises(ServiceError):
            pilot.link(self.service, **{**self.request, "installation_id": str(uuid4())})
        with self.assertRaises(ServiceError):
            pilot.heartbeat(self.service, installation_id=self.device, secret="wrong", payload=self.payload)
        self.service.set_directory_school_status("123627", "suspended")
        with self.assertRaises(ServiceError):
            pilot.heartbeat(self.service, installation_id=self.device, secret=self.secret, payload=self.payload)
        self.assertEqual(self.service.district_school_detail("123627")["health_status"], "suspended")

    def test_heartbeat_validation_and_staleness(self):
        pilot.link(self.service, **self.request)
        with self.assertRaises(ServiceError):
            pilot.heartbeat(self.service, installation_id=self.device, secret=self.secret,
                            payload={**self.payload, "survey_status": "forged"})
        pilot.heartbeat(self.service, installation_id=self.device, secret=self.secret, payload=self.payload)
        with self.repository.transaction() as connection:
            connection.execute("UPDATE pilot_monitors SET received_at='2000-01-01T00:00:00+00:00'")
        self.assertEqual(self.service.district_school_detail("123627")["health_status"], "stale")

    def test_https_api_requires_monitor_secret_and_rejects_unknown_fields(self):
        from fastapi.testclient import TestClient
        from registration_service.app import create_app
        with TestClient(create_app(self.service), base_url="https://management.district.example") as client:
            response = client.post("/v1/pilot-monitoring/link", json=self.request)
            self.assertEqual(response.status_code, 200, response.text)
            path = f"/v1/pilot-monitoring/{self.device}/heartbeat"
            self.assertEqual(client.post(path, json=self.payload).status_code, 401)
            response = client.post(path, json=self.payload, headers={"Authorization": "Bearer " + self.secret})
            self.assertEqual(response.status_code, 200, response.text)
            response = client.post("/v1/pilot-monitoring/link", json={**self.request, "tunnel_token": "never-send-this"})
            self.assertEqual(response.status_code, 422)

    def test_link_blocks_accidental_managed_provisioning(self):
        pilot.link(self.service, **self.request)
        code = self.service.issue_directory_activation_code("123627", valid_for=timedelta(hours=1))
        with self.assertRaises(ServiceError) as rejected:
            self.service.complete_initial_registration(school_id="123627", school_name="School",
                installation_id=str(uuid4()), activation_code=code, credential_id="fake-passkey",
                credential_public_key=b"fake-key", sign_count=0)
        self.assertEqual(rejected.exception.code, "pilot_already_linked")
        self.provisioner.provision.assert_not_called()

    def test_unlink_revokes_reporting_without_touching_the_tunnel(self):
        pilot.link(self.service, **self.request)
        from fastapi.testclient import TestClient
        from registration_service.app import create_app
        with TestClient(create_app(self.service), base_url="https://management.district.example") as client:
            response = client.delete("/v1/admin/schools/123627/pilot-monitoring")
            self.assertEqual(response.status_code, 401)
        pilot.unlink(self.service, "123627")
        with self.assertRaises(ServiceError):
            pilot.heartbeat(self.service, installation_id=self.device, secret=self.secret, payload=self.payload)
        with self.assertRaises(ServiceError):
            pilot.link(self.service, **self.request)
        self.assertEqual(self.service.district_school_detail("123627")["health_status"], "pending")
        self.provisioner.revoke.assert_not_called()


class PilotDesktopTests(unittest.TestCase):
    def test_link_saves_separate_credential_and_reports_after_restart(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from tests.test_gateway_ui_integration import _StateStore, _Tunnel, _config
        from tests.test_gateway_data_integrity import _MemoryCredentialManager
        from school_csm_control_center.storage.gateway_credentials import GatewayCredentialStore
        from school_csm_control_center.internet_gateway.controller import InternetGatewayController
        from threading import Event
        import copy
        local = {"school_id": "123456", "school_name": "School", "preferred_port": 8080,
                 "server_running": False, "survey_status": "online"}
        store = _StateStore()
        backend = _MemoryCredentialManager()
        credentials = GatewayCredentialStore(backend)
        config = _config()
        sent = Event()
        client = Mock(config=config)
        client.link_pilot_monitoring.return_value = {"linked": True, "school_id": "123456",
            "public_host": "123456.school-account.workers.dev", "mode": "monitoring_only"}
        client.pilot_monitoring_heartbeat.side_effect = lambda *args: sent.set()
        controller = InternetGatewayController(Path.cwd(), local_settings_provider=lambda: local,
            state_store=store, credentials=credentials, client=client, tunnel=_Tunnel())
        controller.configure_direct_worker_vpc(public_host="123456.school-account.workers.dev",
            tunnel_id="22222222-2222-4222-8222-222222222222", tunnel_token="t" * 64, beta_acknowledged=True)
        original = copy.deepcopy(store.state)
        local["server_running"] = True
        controller.link_pilot_monitoring("district-code")
        self.assertTrue(sent.wait(2))
        secret = credentials.load_pilot_monitoring()["secret"]
        client.link_pilot_monitoring.side_effect = ValueError("Invalid code")
        with self.assertRaises(ValueError):
            controller.link_pilot_monitoring("wrong-new-code")
        self.assertEqual(credentials.load_pilot_monitoring()["secret"], secret)
        client.link_pilot_monitoring.side_effect = None
        controller.link_pilot_monitoring("district-code")
        self.assertEqual(credentials.load_pilot_monitoring()["secret"], secret)
        self.assertEqual(credentials.load_tunnel_credential(), "t" * 64)
        self.assertIsNone(credentials.load_installation_secret())
        self.assertEqual(store.state, original)
        # The same receipt survives another controller instance, without linking again.
        sent.clear()
        restarted = InternetGatewayController(Path.cwd(), local_settings_provider=lambda: local,
            state_store=store, credentials=credentials, client=client, tunnel=_Tunnel())
        restarted.check_authorization()
        self.assertTrue(sent.wait(2))
        self.assertEqual(client.pilot_monitoring_heartbeat.call_args.args[1], secret)
        client.heartbeat.assert_not_called()
        # A changed provider cannot receive the previous district's secret.
        sent.clear()
        restarted._provider_config = SimpleNamespace(service_url="https://other.example")
        with patch.object(client, "pilot_monitoring_heartbeat") as heartbeat:
            restarted._report_heartbeat()
            heartbeat.assert_not_called()


if __name__ == "__main__":
    unittest.main()
