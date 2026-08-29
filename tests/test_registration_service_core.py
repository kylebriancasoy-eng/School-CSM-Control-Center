from __future__ import annotations

import base64
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import UUID

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from registration_service.core import (
    RegistrationRepository,
    RegistrationService,
    ServiceError,
    _verify_secret,
)
from registration_service.signing import AuthorizationEnvelopeSigner
from registration_service.handoff import CredentialHandoffVault
from school_csm_control_center.internet_gateway.client import verify_authorization_envelope
from school_csm_control_center.internet_gateway.config import parse_gateway_provider_config


class _Provisioner:
    def __init__(self) -> None:
        self.number = 0
        self.revoked = []

    def provision(self, *, school_id: str, public_host: str):
        self.number += 1
        return {"tunnel_id": f"tunnel-{self.number}", "tunnel_token": f"token-{self.number}"}

    def rotate(self, *, tunnel_id: str, school_id: str, public_host: str):
        return self.provision(school_id=school_id, public_host=public_host)

    def revoke(self, *, tunnel_id: str):
        self.revoked.append(tunnel_id)

    def rollback_rotation(
        self, *, source_tunnel_id: str, replacement_tunnel_id: str, public_host: str
    ):
        self.revoked.append(replacement_tunnel_id)

    def credential(self, *, tunnel_id: str, public_host: str, origin_port: int):
        return {"tunnel_id": tunnel_id, "tunnel_token": "existing-token"}


class _FailingHandoffWriter:
    def issue_in_transaction(self, *_args, **_kwargs):
        raise RuntimeError("simulated durable handoff failure")


class RegistrationServiceCoreTests(unittest.TestCase):
    SOURCE_ID = "11111111-1111-4111-8111-111111111111"
    DESTINATION_ID = "22222222-2222-4222-8222-222222222222"
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repository = RegistrationRepository(Path(self.temporary.name) / "registration.sqlite3")
        self.private_key = Ed25519PrivateKey.generate()
        self.signer = AuthorizationEnvelopeSigner(self.private_key, key_id="service-2026")
        self.provisioner = _Provisioner()
        self.service = RegistrationService(
            self.repository,
            managed_domain="csm.example.gov.ph",
            provisioner=self.provisioner,
            signer=self.signer,
        )

    def _register(self):
        activation_code = self.repository.issue_activation_code("123456")
        return self.service.complete_initial_registration(
            school_id="123456",
            school_name="Example Elementary School",
            installation_id=self.SOURCE_ID,
            activation_code=activation_code,
            credential_id="passkey-one",
            credential_public_key=b"public-key",
            sign_count=0,
        )

    @staticmethod
    def _migration_receipt(**overrides):
        receipt = {
            "verified": True,
            "transaction_id": "33333333-3333-4333-8333-333333333333",
            "manifest_sha256": "a" * 64,
            "active_tree_sha256": "b" * 64,
            "record_counts": {"responses": 27},
        }
        receipt.update(overrides)
        return receipt

    @classmethod
    def _backup_receipt(cls, **overrides):
        receipt = cls._migration_receipt(
            source_kind="portable_backup",
            backup_id="44444444-4444-4444-8444-444444444444",
            backup_manifest_sha256="c" * 64,
        )
        receipt.update(overrides)
        return receipt

    def test_initial_registration_uses_activation_once_and_stores_no_response_data(self) -> None:
        result = self._register()
        self.assertEqual(result.public_host, "123456.csm.example.gov.ph")
        self.assertNotEqual(result.installation_secret, "")
        with self.repository.read() as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        forbidden = {"responses", "survey_responses", "scanner_images", "narratives"}
        self.assertFalse(tables & forbidden)
        with self.assertRaises(ServiceError):
            self.service.complete_initial_registration(
                school_id="123456",
                school_name="Example Elementary School",
                installation_id="33333333-3333-4333-8333-333333333333",
                activation_code="INVALID-CODE",
                credential_id="passkey-two",
                credential_public_key=b"public-key",
                sign_count=0,
            )

    def test_transfer_requires_verified_data_unless_server_only(self) -> None:
        self._register()
        with self.assertRaisesRegex(ServiceError, "verified staged migration"):
            self.service.complete_transfer(
                school_id="123456",
                destination_installation_id=self.DESTINATION_ID,
                credential_id="passkey-one",
                new_sign_count=1,
                transfer_mode="server_and_data",
                data_validation=self._migration_receipt(verified=False),
            )

    def test_transfer_intent_binds_desktop_validation_and_is_one_time(self) -> None:
        self._register()
        with self.assertRaisesRegex(ServiceError, "verified staged migration"):
            self.repository.create_transfer_intent(
                school_id="123456",
                destination_installation_id=self.DESTINATION_ID,
                transfer_mode="server_and_data",
                data_validation=self._migration_receipt(verified=False),
            )

        created = self.repository.create_transfer_intent(
            school_id="123456",
            destination_installation_id=self.DESTINATION_ID,
            transfer_mode="server_and_data",
            data_validation=self._migration_receipt(),
        )
        intent = self.repository.transfer_intent(created["intent_id"])
        self.assertEqual(intent["school_id"], "123456")
        self.assertEqual(intent["transfer_mode"], "server_and_data")
        self.assertEqual(intent["data_validation"]["record_counts"], {"responses": 27})
        self.repository.transfer_intent(created["intent_id"], consume=True)
        with self.assertRaisesRegex(ServiceError, "expired or was already used"):
            self.repository.transfer_intent(created["intent_id"])

        with self.assertRaisesRegex(ServiceError, "warning confirmation"):
            self.repository.create_transfer_intent(
                school_id="123456",
                destination_installation_id=self.DESTINATION_ID,
                transfer_mode="server_only",
                data_validation={},
            )

    def test_transfer_receipts_are_bound_to_live_or_backup_mode(self) -> None:
        self._register()
        backup = self.repository.create_transfer_intent(
            school_id="123456",
            destination_installation_id=self.DESTINATION_ID,
            transfer_mode="backup_assisted",
            data_validation=self._backup_receipt(),
        )
        inspected = self.repository.transfer_intent(backup["intent_id"])
        self.assertEqual(inspected["data_validation"]["source_kind"], "portable_backup")
        self.assertEqual(
            inspected["data_validation"]["backup_manifest_sha256"], "c" * 64
        )

        with self.assertRaisesRegex(ServiceError, "selected transfer mode"):
            self.repository.create_transfer_intent(
                school_id="123456",
                destination_installation_id=self.DESTINATION_ID,
                transfer_mode="server_and_data",
                data_validation=self._backup_receipt(),
            )
        with self.assertRaisesRegex(ServiceError, "selected transfer mode"):
            self.repository.create_transfer_intent(
                school_id="123456",
                destination_installation_id=self.DESTINATION_ID,
                transfer_mode="backup_assisted",
                data_validation=self._migration_receipt(),
            )

    def test_cutover_keeps_one_active_server_and_signed_retirement_state(self) -> None:
        source = self._register()
        transfer = self.service.complete_transfer(
            school_id="123456",
            destination_installation_id=self.DESTINATION_ID,
            credential_id="passkey-one",
            new_sign_count=1,
            transfer_mode="server_and_data",
            data_validation=self._migration_receipt(),
        )
        with self.repository.read() as connection:
            rows = connection.execute(
                "SELECT installation_id,state FROM installations WHERE school_id=? ORDER BY installation_id",
                ("123456",),
            ).fetchall()
            active_count = connection.execute(
                "SELECT COUNT(*) FROM installations WHERE school_id=? AND state='active'",
                ("123456",),
            ).fetchone()[0]
        self.assertEqual(active_count, 1)
        self.assertEqual({row["state"] for row in rows}, {"active", "transferred"})
        self.assertNotEqual(source.tunnel_id, transfer["tunnel_id"])
        self.assertEqual(self.provisioner.revoked.count(source.tunnel_id), 1)

        source_status = self.service.signed_authorization(
            source.installation_id, source.installation_secret
        )
        public = self.private_key.public_key().public_bytes_raw()
        config = parse_gateway_provider_config(
            {
                "schema_version": "1.0",
                "registration_service_url": "https://registration.example.gov.ph",
                "managed_domain": "csm.example.gov.ph",
                "authorization_signing_keys": [
                    {
                        "key_id": "service-2026",
                        "public_key_base64": base64.b64encode(public).decode("ascii"),
                    }
                ],
                "trusted_proxy_addresses": ["127.0.0.1"],
            }
        )
        verified = verify_authorization_envelope(
            source_status,
            config,
            expected_school_id="123456",
            expected_installation_id=source.installation_id,
        )
        self.assertEqual(verified.state, "transferred")
        self.assertEqual(verified.transaction_id, transfer["transaction_id"])

    def test_school_registration_and_transfer_authority_are_isolated(self) -> None:
        first = self._register()
        second_school = "654321"
        second_installation = "55555555-5555-4555-8555-555555555555"
        activation = self.repository.issue_activation_code(second_school)
        second = self.service.complete_initial_registration(
            school_id=second_school,
            school_name="Second Elementary School",
            installation_id=second_installation,
            activation_code=activation,
            credential_id="second-school-passkey",
            credential_public_key=b"second-school-public-key",
            sign_count=0,
        )

        self.assertEqual(first.public_host, "123456.csm.example.gov.ph")
        self.assertEqual(second.public_host, "654321.csm.example.gov.ph")
        with self.assertRaisesRegex(ServiceError, "passkey"):
            self.service.complete_transfer(
                school_id="123456",
                destination_installation_id=self.DESTINATION_ID,
                credential_id="second-school-passkey",
                new_sign_count=1,
                transfer_mode="server_only",
                data_validation={"warning_confirmed": True},
            )

        with self.repository.read() as connection:
            second_row = connection.execute(
                "SELECT active_installation_id,tunnel_id FROM schools WHERE school_id=?",
                (second_school,),
            ).fetchone()
        self.assertEqual(second_row["active_installation_id"], second_installation)
        self.assertEqual(second_row["tunnel_id"], second.tunnel_id)

    def test_failed_tunnel_rotation_keeps_source_active_and_cleans_replacement(self) -> None:
        source = self._register()

        def incomplete_rotation(**_kwargs):
            return {"tunnel_id": "replacement-incomplete", "tunnel_token": ""}

        self.provisioner.rotate = incomplete_rotation
        with self.assertRaisesRegex(ServiceError, "rotation failed"):
            self.service.complete_transfer(
                school_id="123456",
                destination_installation_id=self.DESTINATION_ID,
                credential_id="passkey-one",
                new_sign_count=1,
                transfer_mode="server_only",
                data_validation={"warning_confirmed": True},
            )
        with self.repository.read() as connection:
            active = connection.execute(
                "SELECT active_installation_id,tunnel_id FROM schools WHERE school_id='123456'"
            ).fetchone()
        self.assertEqual(active["active_installation_id"], self.SOURCE_ID)
        self.assertEqual(active["tunnel_id"], source.tunnel_id)
        self.assertIn("replacement-incomplete", self.provisioner.revoked)

    def test_wrong_installation_secret_never_returns_status(self) -> None:
        result = self._register()
        with self.assertRaisesRegex(ServiceError, "authorization failed"):
            self.service.signed_authorization(result.installation_id, "wrong-secret")

    def test_tampered_secret_hash_cannot_select_unbounded_scrypt_cost(self) -> None:
        hostile = "scrypt$1073741824$8$1$" + ("00" * 16) + "$" + ("00" * 32)
        with patch(
            "registration_service.core.hashlib.scrypt",
            side_effect=AssertionError("scrypt must not run"),
        ):
            self.assertFalse(_verify_secret("secret", hostile))

    def test_completion_handoff_is_encrypted_bound_and_acknowledged(self) -> None:
        vault = CredentialHandoffVault(self.repository, b"h" * 32)
        code = vault.issue(
            self.SOURCE_ID,
            {"installation_secret": "secret-value", "tunnel_token": "tunnel-value"},
        )
        database_bytes = Path(self.repository.path).read_bytes()
        self.assertNotIn(b"secret-value", database_bytes)
        self.assertNotIn(b"tunnel-value", database_bytes)
        with self.assertRaises(ServiceError):
            vault.redeem(code, self.DESTINATION_ID)
        first = vault.redeem(code, self.SOURCE_ID)
        second = vault.redeem(code, self.SOURCE_ID)
        self.assertEqual(first["installation_secret"], "secret-value")
        self.assertEqual(second["installation_secret"], "secret-value")
        self.assertEqual(
            first["acknowledgement_token"], second["acknowledgement_token"]
        )
        acknowledged = vault.acknowledge(
            code,
            self.SOURCE_ID,
            first["acknowledgement_token"],
        )
        self.assertTrue(acknowledged["acknowledged"])
        self.assertFalse(acknowledged["already_acknowledged"])
        repeated = vault.acknowledge(
            code,
            self.SOURCE_ID,
            first["acknowledgement_token"],
        )
        self.assertTrue(repeated["already_acknowledged"])
        with self.assertRaises(ServiceError):
            vault.redeem(code, self.SOURCE_ID)

    def test_transactional_handoff_code_is_exactly_recoverable_for_a_retry(self) -> None:
        vault = CredentialHandoffVault(self.repository, b"h" * 32)
        request = {"school_id": "123456", "installation_id": self.SOURCE_ID}
        fingerprint = vault.request_fingerprint(request)
        with self.repository.transaction() as connection:
            first = vault.issue_in_transaction(
                connection,
                self.SOURCE_ID,
                {"installation_secret": "secret-value"},
                operation_id="registration:ceremony-one",
                request_fingerprint=fingerprint,
            )
        with self.repository.transaction() as connection:
            second = vault.issue_in_transaction(
                connection,
                self.SOURCE_ID,
                {"installation_secret": "different-payload-is-not-rewritten"},
                operation_id="registration:ceremony-one",
                request_fingerprint=fingerprint,
            )
        self.assertEqual(first, second)
        self.assertEqual(
            vault.recover_code(
                "registration:ceremony-one", self.SOURCE_ID, fingerprint
            ),
            first,
        )
        with self.assertRaisesRegex(ServiceError, "does not match"):
            vault.recover_code(
                "registration:ceremony-one",
                self.SOURCE_ID,
                vault.request_fingerprint({**request, "school_id": "654321"}),
            )

    def test_failed_initial_handoff_rolls_back_registration_and_tunnel(self) -> None:
        activation_code = self.repository.issue_activation_code("123456")
        with self.assertRaisesRegex(RuntimeError, "durable handoff failure"):
            self.service.complete_initial_registration(
                school_id="123456",
                school_name="Example Elementary School",
                installation_id=self.SOURCE_ID,
                activation_code=activation_code,
                credential_id="passkey-one",
                credential_public_key=b"public-key",
                sign_count=0,
                handoff_writer=_FailingHandoffWriter(),
                handoff_operation_id="registration:ceremony-one",
                handoff_request_fingerprint="a" * 64,
            )
        with self.repository.read() as connection:
            self.assertIsNone(
                connection.execute(
                    "SELECT 1 FROM schools WHERE school_id='123456'"
                ).fetchone()
            )
            activation = connection.execute(
                "SELECT used_at FROM activation_codes WHERE school_id='123456'"
            ).fetchone()
        self.assertIsNone(activation["used_at"])
        self.assertEqual(self.provisioner.revoked, ["tunnel-1"])

    def test_failed_transfer_handoff_keeps_source_authoritative(self) -> None:
        source = self._register()
        with self.assertRaisesRegex(RuntimeError, "durable handoff failure"):
            self.service.complete_transfer(
                school_id="123456",
                destination_installation_id=self.DESTINATION_ID,
                credential_id="passkey-one",
                new_sign_count=9,
                transfer_mode="server_only",
                data_validation={"warning_confirmed": True},
                handoff_writer=_FailingHandoffWriter(),
                handoff_operation_id="transfer:ceremony-one:intent-one",
                handoff_request_fingerprint="b" * 64,
            )
        with self.repository.read() as connection:
            school = connection.execute(
                "SELECT active_installation_id,tunnel_id FROM schools WHERE school_id='123456'"
            ).fetchone()
            destination = connection.execute(
                "SELECT 1 FROM installations WHERE installation_id=?",
                (self.DESTINATION_ID,),
            ).fetchone()
            sign_count = connection.execute(
                "SELECT sign_count FROM passkeys WHERE credential_id='passkey-one'"
            ).fetchone()[0]
        self.assertEqual(school["active_installation_id"], self.SOURCE_ID)
        self.assertEqual(school["tunnel_id"], source.tunnel_id)
        self.assertIsNone(destination)
        self.assertEqual(sign_count, 0)
        self.assertIn("tunnel-2", self.provisioner.revoked)
        self.assertNotIn(source.tunnel_id, self.provisioner.revoked)

    def test_passkey_add_revoke_and_lost_key_recovery_preserve_admin_access(self) -> None:
        self._register()
        self.service.add_passkey(
            school_id="123456",
            credential_id="passkey-two",
            credential_public_key=b"second-public-key",
            sign_count=0,
            label="Backup security key",
            authorized_by="passkey-one",
        )
        self.assertEqual(len(self.service.list_passkeys("123456")), 2)
        self.service.revoke_passkey(school_id="123456", credential_id="passkey-one")
        with self.assertRaisesRegex(ServiceError, "last administrator passkey"):
            self.service.revoke_passkey(school_id="123456", credential_id="passkey-two")

        reset_code = self.repository.issue_passkey_reset_code("123456")
        reset_hash = self.repository.validate_passkey_reset_code("123456", reset_code)
        self.service.reset_passkeys(
            school_id="123456",
            reset_code_hash=reset_hash,
            credential_id="passkey-recovered",
            credential_public_key=b"recovered-public-key",
            sign_count=0,
            label="Recovered Windows Hello",
        )
        passkeys = self.service.list_passkeys("123456")
        self.assertEqual([row["credential_id"] for row in passkeys], ["passkey-recovered"])
        with self.assertRaisesRegex(ServiceError, "invalid or expired"):
            self.repository.validate_passkey_reset_code("123456", reset_code)


if __name__ == "__main__":
    unittest.main()
