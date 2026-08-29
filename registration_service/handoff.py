"""Short-lived one-time credential handoff from browser WebAuthn to desktop."""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
from typing import Any, Mapping

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .core import RegistrationRepository, ServiceError, timestamp, validate_installation_id


class CredentialHandoffVault:
    """Keep newly issued device credentials encrypted for at most ten minutes."""

    MAX_DELIVERIES = 3

    def __init__(self, repository: RegistrationRepository, key: bytes) -> None:
        if not isinstance(key, bytes) or len(key) != 32:
            raise ValueError("Credential handoff encryption requires a 32-byte key.")
        self.repository = repository
        self._key = bytes(key)
        self._cipher = AESGCM(key)

    @classmethod
    def from_environment(cls, repository: RegistrationRepository) -> "CredentialHandoffVault":
        encoded = os.environ.get("SCHOOL_CSM_HANDOFF_ENCRYPTION_KEY_BASE64", "").strip()
        try:
            key = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError):
            raise RuntimeError("Credential handoff encryption is not configured.") from None
        return cls(repository, key)

    def issue(
        self,
        installation_id: str,
        payload: Mapping[str, Any],
        *,
        valid_for: timedelta = timedelta(minutes=10),
    ) -> str:
        installation = validate_installation_id(installation_id)
        raw_code = secrets.token_bytes(20)
        compact, display_code = self._format_code(raw_code)
        code_hash = hashlib.sha256(compact.encode("ascii")).hexdigest()
        now = datetime.now(timezone.utc)
        with self.repository.transaction() as connection:
            self._insert(
                connection,
                code_hash=code_hash,
                installation_id=installation,
                payload=payload,
                created_at=now,
                expires_at=now + valid_for,
            )
        return display_code

    def request_fingerprint(self, payload: Mapping[str, Any]) -> str:
        """Return a keyed digest so low-entropy request fields are not exposed."""

        canonical = json.dumps(
            dict(payload),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return hmac.new(self._key, b"handoff-request-v1\0" + canonical, hashlib.sha256).hexdigest()

    def issue_in_transaction(
        self,
        connection: sqlite3.Connection,
        installation_id: str,
        payload: Mapping[str, Any],
        *,
        operation_id: str,
        request_fingerprint: str,
        valid_for: timedelta = timedelta(minutes=10),
    ) -> str:
        """Atomically stage a deterministic, retryable one-time handoff."""

        installation = validate_installation_id(installation_id)
        operation = self._validate_operation(operation_id)
        fingerprint = self._validate_fingerprint(request_fingerprint)
        compact, display_code = self._operation_code(operation, installation, fingerprint)
        code_hash = hashlib.sha256(compact.encode("ascii")).hexdigest()
        now = datetime.now(timezone.utc)
        existing = connection.execute(
            "SELECT * FROM credential_handoffs WHERE operation_id=?", (operation,)
        ).fetchone()
        if existing is not None:
            self._validate_retry_row(
                existing,
                installation=installation,
                fingerprint=fingerprint,
                code_hash=code_hash,
                now=now,
            )
            return display_code
        self._insert(
            connection,
            code_hash=code_hash,
            installation_id=installation,
            payload=payload,
            created_at=now,
            expires_at=now + valid_for,
            operation_id=operation,
            request_fingerprint=fingerprint,
        )
        return display_code

    def recover_code(
        self,
        operation_id: str,
        installation_id: str,
        request_fingerprint: str,
    ) -> str | None:
        """Recover only the same unused code for the exact completed request."""

        recovered = self.recover_operation_code(
            operation_id,
            request_fingerprint,
            expected_installation_id=installation_id,
        )
        return recovered[0] if recovered is not None else None

    def recover_operation_code(
        self,
        operation_id: str,
        request_fingerprint: str,
        *,
        expected_installation_id: str | None = None,
    ) -> tuple[str, str] | None:
        """Recover a code and its bound installation for an exact request retry."""

        operation = self._validate_operation(operation_id)
        fingerprint = self._validate_fingerprint(request_fingerprint)
        with self.repository.read() as connection:
            row = connection.execute(
                "SELECT * FROM credential_handoffs WHERE operation_id=?", (operation,)
            ).fetchone()
        if row is None:
            return None
        installation = validate_installation_id(str(row["installation_id"]))
        if expected_installation_id is not None:
            expected = validate_installation_id(expected_installation_id)
            if not hmac.compare_digest(installation, expected):
                raise ServiceError(
                    "The completion request does not match.", code="handoff_invalid"
                )
        compact, display_code = self._operation_code(operation, installation, fingerprint)
        code_hash = hashlib.sha256(compact.encode("ascii")).hexdigest()
        self._validate_retry_row(
            row,
            installation=installation,
            fingerprint=fingerprint,
            code_hash=code_hash,
            now=datetime.now(timezone.utc),
        )
        return display_code, installation

    def operation_exists(self, operation_id: str) -> bool:
        """Report durable staging without exposing any handoff material."""

        operation = self._validate_operation(operation_id)
        with self.repository.read() as connection:
            row = connection.execute(
                "SELECT 1 FROM credential_handoffs WHERE operation_id=?", (operation,)
            ).fetchone()
        return row is not None

    def redeem(self, code: str, installation_id: str) -> Mapping[str, Any]:
        installation = validate_installation_id(installation_id)
        compact = "".join(character for character in str(code or "").upper() if character.isalnum())
        if len(compact) != 32:
            raise ServiceError("The completion code is invalid or expired.", code="handoff_invalid")
        code_hash = hashlib.sha256(compact.encode("ascii")).hexdigest()
        now = datetime.now(timezone.utc)
        with self.repository.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM credential_handoffs WHERE code_hash=?", (code_hash,)
            ).fetchone()
            if (
                row is None
                or row["installation_id"] != installation
                or row["used_at"]
                or datetime.fromisoformat(row["expires_at"]) <= now
                or int(row["delivery_count"] or 0) >= self.MAX_DELIVERIES
            ):
                raise ServiceError("The completion code is invalid or expired.", code="handoff_invalid")
            try:
                plaintext = self._cipher.decrypt(
                    bytes(row["nonce"]), bytes(row["ciphertext"]), code_hash.encode("ascii")
                )
                payload = json.loads(plaintext.decode("utf-8"))
            except Exception:
                raise ServiceError("The completion code is invalid or expired.", code="handoff_invalid") from None
            if not isinstance(payload, Mapping):
                raise ServiceError("The completion code is invalid or expired.", code="handoff_invalid")
            acknowledgement = self._acknowledgement_token(code_hash, installation)
            acknowledgement_hash = hashlib.sha256(
                acknowledgement.encode("ascii")
            ).hexdigest()
            changed = connection.execute(
                """UPDATE credential_handoffs
                   SET delivered_at=COALESCE(delivered_at,?),
                       acknowledgement_hash=?,delivery_count=delivery_count+1
                   WHERE code_hash=? AND used_at IS NULL AND delivery_count<?""",
                (
                    timestamp(now),
                    acknowledgement_hash,
                    code_hash,
                    self.MAX_DELIVERIES,
                ),
            ).rowcount
            if changed != 1:
                raise ServiceError(
                    "The completion code is invalid or expired.", code="handoff_invalid"
                )
            return {
                **dict(payload),
                "acknowledgement_token": acknowledgement,
                "handoff_expires_at": str(row["expires_at"]),
            }

    def acknowledge(
        self, code: str, installation_id: str, acknowledgement_token: str
    ) -> Mapping[str, Any]:
        """Finalize delivery only after the destination persisted its credentials."""

        installation = validate_installation_id(installation_id)
        compact = "".join(
            character for character in str(code or "").upper() if character.isalnum()
        )
        if len(compact) != 32:
            raise ServiceError(
                "The completion acknowledgement is invalid.", code="handoff_invalid"
            )
        code_hash = hashlib.sha256(compact.encode("ascii")).hexdigest()
        expected = self._acknowledgement_token(code_hash, installation)
        supplied = str(acknowledgement_token or "").strip()
        supplied_hash = hashlib.sha256(supplied.encode("ascii", "ignore")).hexdigest()
        now = datetime.now(timezone.utc)
        with self.repository.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM credential_handoffs WHERE code_hash=?", (code_hash,)
            ).fetchone()
            if (
                row is None
                or str(row["installation_id"]) != installation
                or not hmac.compare_digest(supplied, expected)
                or not hmac.compare_digest(
                    str(row["acknowledgement_hash"] or ""), supplied_hash
                )
                or not row["delivered_at"]
            ):
                raise ServiceError(
                    "The completion acknowledgement is invalid.", code="handoff_invalid"
                )
            if row["used_at"]:
                return {"acknowledged": True, "already_acknowledged": True}
            if datetime.fromisoformat(str(row["expires_at"])) <= now:
                raise ServiceError(
                    "The completion acknowledgement is invalid or expired.",
                    code="handoff_invalid",
                )
            changed = connection.execute(
                """UPDATE credential_handoffs SET used_at=?,ciphertext=?
                   WHERE code_hash=? AND used_at IS NULL""",
                (timestamp(now), b"", code_hash),
            ).rowcount
            if changed != 1:
                raise ServiceError(
                    "The completion acknowledgement is invalid.", code="handoff_invalid"
                )
            return {"acknowledged": True, "already_acknowledged": False}

    @staticmethod
    def _format_code(raw_code: bytes) -> tuple[str, str]:
        compact = base64.b32encode(bytes(raw_code)).decode("ascii").rstrip("=")
        display = "-".join(
            compact[index : index + 4] for index in range(0, len(compact), 4)
        )
        return compact, display

    def _operation_code(
        self, operation_id: str, installation_id: str, request_fingerprint: str
    ) -> tuple[str, str]:
        material = (
            b"handoff-code-v1\0"
            + operation_id.encode("utf-8")
            + b"\0"
            + installation_id.encode("ascii")
            + b"\0"
            + request_fingerprint.encode("ascii")
        )
        return self._format_code(hmac.new(self._key, material, hashlib.sha256).digest()[:20])

    def _acknowledgement_token(self, code_hash: str, installation_id: str) -> str:
        material = (
            b"handoff-ack-v1\0"
            + code_hash.encode("ascii")
            + b"\0"
            + installation_id.encode("ascii")
        )
        return base64.urlsafe_b64encode(
            hmac.new(self._key, material, hashlib.sha256).digest()
        ).decode("ascii").rstrip("=")

    @staticmethod
    def _validate_operation(value: str) -> str:
        operation = str(value or "").strip()
        if not 8 <= len(operation) <= 200 or any(ord(character) < 32 for character in operation):
            raise ServiceError("Credential handoff identity is invalid.", code="handoff_invalid")
        return operation

    @staticmethod
    def _validate_fingerprint(value: str) -> str:
        fingerprint = str(value or "").strip().casefold()
        if len(fingerprint) != 64 or any(
            character not in "0123456789abcdef" for character in fingerprint
        ):
            raise ServiceError("Credential handoff identity is invalid.", code="handoff_invalid")
        return fingerprint

    @staticmethod
    def _validate_retry_row(
        row: Mapping[str, Any],
        *,
        installation: str,
        fingerprint: str,
        code_hash: str,
        now: datetime,
    ) -> None:
        if (
            str(row["installation_id"]) != installation
            or str(row["request_fingerprint"] or "") != fingerprint
            or not hmac.compare_digest(str(row["code_hash"]), code_hash)
        ):
            raise ServiceError("The completion request does not match.", code="handoff_invalid")
        if row["used_at"]:
            raise ServiceError(
                "The completion credentials were already collected.",
                code="handoff_already_redeemed",
            )
        if datetime.fromisoformat(str(row["expires_at"])) <= now:
            raise ServiceError("The completion code is invalid or expired.", code="handoff_invalid")

    def _insert(
        self,
        connection: sqlite3.Connection,
        *,
        code_hash: str,
        installation_id: str,
        payload: Mapping[str, Any],
        created_at: datetime,
        expires_at: datetime,
        operation_id: str | None = None,
        request_fingerprint: str | None = None,
    ) -> None:
        nonce = secrets.token_bytes(12)
        plaintext = json.dumps(
            dict(payload),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        if len(plaintext) > 32 * 1024:
            raise ServiceError("Credential handoff payload is unexpectedly large.")
        ciphertext = self._cipher.encrypt(nonce, plaintext, code_hash.encode("ascii"))
        connection.execute(
            """INSERT INTO credential_handoffs(
                code_hash,installation_id,operation_id,request_fingerprint,
                nonce,ciphertext,created_at,expires_at
            ) VALUES(?,?,?,?,?,?,?,?)""",
            (
                code_hash,
                installation_id,
                operation_id,
                request_fingerprint,
                nonce,
                ciphertext,
                timestamp(created_at),
                timestamp(expires_at),
            ),
        )
