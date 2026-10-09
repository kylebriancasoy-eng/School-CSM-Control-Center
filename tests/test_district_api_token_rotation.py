"""Synthetic-only Cloudflare token replacement and failure recovery tests."""

from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from district_management_server.credentials import CLOUDFLARE_API_TOKEN_TARGET
from district_management_server.token_rotation import (
    ApiTokenRotationError, replace_cloudflare_api_token, verify_replacement,
)


OLD = "synthetic-previous-cloudflare-token"
NEW = "cfat_synthetic-replacement-cloudflare-token"


class _Store:
    def __init__(self):
        self.token = OLD
        self.writes = []

    def load(self):
        return SimpleNamespace(cloudflare_api_token=self.token)

    def save(self, target, token):
        self.writes.append(target)
        self.token = token


class DistrictApiTokenRotationTests(unittest.TestCase):
    def setUp(self):
        self.config = SimpleNamespace(
            cloudflare_account_id="a" * 32,
            management_tunnel_id="synthetic-tunnel",
            management_vpc_service_id="synthetic-service",
            management_worker_name="management",
        )
        self.store = _Store()
        self.controller = Mock()
        self.controller.start.return_value = SimpleNamespace(state="running")
        self.verifier = Mock()

    def replace(self, candidate=NEW):
        return replace_cloudflare_api_token(
            self.config, candidate, self.controller,
            credential_store=self.store, verifier=self.verifier,
        )

    def test_success_updates_only_api_token_and_restarts(self):
        self.replace()
        self.assertEqual(self.store.token, NEW)
        self.assertEqual(self.store.writes, [CLOUDFLARE_API_TOKEN_TARGET])
        self.verifier.assert_called_once_with(self.config, NEW)
        self.controller.stop.assert_called_once()
        self.controller.start.assert_called_once()

    def test_validation_failure_never_stops_or_saves(self):
        for candidate in ("", "short", "x" * 2049, "x" * 25 + "\nsecret", "x" * 25 + " secret"):
            with self.subTest(length=len(candidate)), self.assertRaises(ApiTokenRotationError):
                self.replace(candidate)
        self.assertEqual(self.store.writes, [])
        self.controller.stop.assert_not_called()
        self.verifier.assert_not_called()

    def test_unverified_token_does_not_mutate_and_does_not_echo_exception(self):
        self.verifier.side_effect = RuntimeError(f"Authorization: Bearer {NEW}")
        with self.assertRaises(ApiTokenRotationError) as error:
            self.replace()
        self.assertNotIn(NEW, str(error.exception))
        self.assertEqual(self.store.token, OLD)
        self.controller.stop.assert_not_called()

    def test_same_token_is_rejected_without_restart(self):
        with self.assertRaises(ApiTokenRotationError):
            self.replace(OLD)
        self.assertEqual(self.store.writes, [])
        self.controller.stop.assert_not_called()

    def test_failed_restart_restores_previous_and_restarts(self):
        self.controller.start.side_effect = [RuntimeError(NEW), SimpleNamespace(state="running")]
        with self.assertRaisesRegex(ApiTokenRotationError, "previous saved API token") as error:
            self.replace()
        self.assertNotIn(NEW, str(error.exception))
        self.assertEqual(self.store.token, OLD)
        self.assertEqual(self.store.writes, [CLOUDFLARE_API_TOKEN_TARGET] * 2)
        self.assertEqual(self.controller.start.call_count, 2)

    def test_failed_recovery_has_explicit_safe_message(self):
        self.controller.start.side_effect = RuntimeError(NEW)
        with self.assertRaisesRegex(ApiTokenRotationError, "Do not revoke either token") as error:
            self.replace()
        self.assertNotIn(NEW, str(error.exception))
        self.assertEqual(self.store.token, OLD)

    def test_account_token_verification_only_reads_existing_resources(self):
        reader = Mock(side_effect=[
            {"status": "active"}, {"id": self.config.management_tunnel_id},
            {"service_id": self.config.management_vpc_service_id}, [{"id": "management"}],
        ])
        verify_replacement(self.config, NEW, reader=reader)
        paths = [call.args[0] for call in reader.call_args_list]
        self.assertTrue(paths[0].endswith("/accounts/" + "a" * 32 + "/tokens/verify"))
        self.assertEqual(len(paths), 4)
        self.assertTrue(all(path.startswith("/client/v4/accounts/" + "a" * 32) for path in paths))

    def test_user_token_uses_user_verification(self):
        reader = Mock(side_effect=[
            {"status": "active"}, {"id": self.config.management_tunnel_id},
            {"service_id": self.config.management_vpc_service_id}, [{"id": "management"}],
        ])
        verify_replacement(self.config, "synthetic-user-token-for-tests", reader=reader)
        self.assertEqual(reader.call_args_list[0].args[0], "/client/v4/user/tokens/verify")

    def test_wrong_resources_inactive_and_missing_worker_fail_closed(self):
        for results in (
            [{"status": "expired"}, {"id": "synthetic-tunnel"}, {"service_id": "synthetic-service"}, [{"id": "management"}]],
            [{"status": "active"}, {"id": "other"}, {"service_id": "synthetic-service"}, [{"id": "management"}]],
            [{"status": "active"}, {"id": "synthetic-tunnel"}, {"service_id": "other"}, [{"id": "management"}]],
            [{"status": "active"}, {"id": "synthetic-tunnel"}, {"service_id": "synthetic-service"}, []],
        ):
            with self.subTest(results=results), self.assertRaises(ApiTokenRotationError):
                verify_replacement(self.config, NEW, reader=Mock(side_effect=results))


if __name__ == "__main__":
    unittest.main()
