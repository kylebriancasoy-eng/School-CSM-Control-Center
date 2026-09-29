"""Transactional School-ID registration and transfer domain model."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
from pathlib import Path
import secrets
import sqlite3
from typing import Any, Iterator, Mapping, Protocol
from uuid import UUID, uuid4


SCHEMA_VERSION = 2
ACTIVE = "active"
TRANSFERRED = "transferred"
REVOKED = "revoked"
DIRECTORY_APPROVED = "approved"
DIRECTORY_SUSPENDED = "suspended"
DIRECTORY_STATUSES = {DIRECTORY_APPROVED, DIRECTORY_SUSPENDED}


class ServiceError(RuntimeError):
    def __init__(self, message: str, *, code: str = "service_error") -> None:
        super().__init__(message)
        self.code = code


class TunnelProvisioner(Protocol):
    def provision(self, *, school_id: str, public_host: str) -> Mapping[str, str]: ...

    def rotate(self, *, tunnel_id: str, school_id: str, public_host: str) -> Mapping[str, str]: ...

    def revoke(self, *, tunnel_id: str) -> None: ...

    def rollback_rotation(
        self,
        *,
        source_tunnel_id: str,
        replacement_tunnel_id: str,
        public_host: str,
    ) -> None: ...

    def credential(
        self, *, tunnel_id: str, public_host: str, origin_port: int
    ) -> Mapping[str, str]: ...


class AuthorizationSigner(Protocol):
    def sign(self, payload: Mapping[str, Any]) -> Mapping[str, Any]: ...


class CredentialHandoffWriter(Protocol):
    """Write an encrypted completion handoff inside an existing transaction."""

    def issue_in_transaction(
        self,
        connection: sqlite3.Connection,
        installation_id: str,
        payload: Mapping[str, Any],
        *,
        operation_id: str,
        request_fingerprint: str,
    ) -> str: ...


class UnconfiguredTunnelProvisioner:
    def provision(self, **_kwargs: Any) -> Mapping[str, str]:
        raise ServiceError(
            "Tunnel provisioning is not configured on the registration service.",
            code="provisioner_unavailable",
        )

    def rotate(self, **_kwargs: Any) -> Mapping[str, str]:
        raise ServiceError(
            "Tunnel provisioning is not configured on the registration service.",
            code="provisioner_unavailable",
        )

    def revoke(self, **_kwargs: Any) -> None:
        return

    def credential(self, **_kwargs: Any) -> Mapping[str, str]:
        raise ServiceError(
            "Tunnel provisioning is not configured on the registration service.",
            code="provisioner_unavailable",
        )


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def timestamp(value: datetime | None = None) -> str:
    return (value or utc_now()).astimezone(timezone.utc).isoformat(timespec="seconds")


def validate_school_id(value: Any) -> str:
    school_id = str(value or "").strip()
    if not school_id.isdigit() or not 4 <= len(school_id) <= 12:
        raise ServiceError("School ID must contain 4 to 12 digits.", code="invalid_school_id")
    return school_id


def _clean_required_text(value: Any, *, label: str, maximum: int = 200) -> str:
    text = " ".join(str(value or "").split())[:maximum]
    if not text:
        raise ServiceError(f"{label} is required.", code="invalid_school_directory")
    return text


def _directory_status(value: Any) -> str:
    status = str(value or "").strip().casefold()
    if status not in DIRECTORY_STATUSES:
        raise ServiceError(
            "School directory status must be approved or suspended.",
            code="invalid_school_directory",
        )
    return status


def validate_installation_id(value: Any) -> str:
    installation_id = str(value or "").strip()
    try:
        parsed = UUID(installation_id)
    except (ValueError, TypeError, AttributeError):
        raise ServiceError("Installation identity is invalid.", code="invalid_installation_id")
    if str(parsed) != installation_id.casefold():
        raise ServiceError("Installation identity is invalid.", code="invalid_installation_id")
    return str(parsed)


def _hash_secret(secret: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    derived = hashlib.scrypt(
        str(secret).encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32
    )
    return f"scrypt$16384$8$1${salt.hex()}${derived.hex()}"


def _verify_secret(secret: str, stored: str) -> bool:
    try:
        algorithm, n, r, p, salt, expected = stored.split("$", 5)
        work_factor = int(n)
        block_size = int(r)
        parallelism = int(p)
        salt_bytes = bytes.fromhex(salt)
        expected_bytes = bytes.fromhex(expected)
        # Never let database corruption or tampering select attacker-controlled
        # scrypt costs.  Stored installation secrets use one exact format.
        if (
            algorithm != "scrypt"
            or (work_factor, block_size, parallelism) != (2**14, 8, 1)
            or len(salt_bytes) != 16
            or len(expected_bytes) != 32
        ):
            return False
        actual = hashlib.scrypt(
            str(secret).encode("utf-8"),
            salt=salt_bytes,
            n=work_factor,
            r=block_size,
            p=parallelism,
            dklen=len(expected_bytes),
        )
        return hmac.compare_digest(actual, expected_bytes)
    except (ValueError, TypeError):
        return False


def _validated_transfer_intent(
    transfer_mode: Any, data_validation: Mapping[str, Any]
) -> tuple[str, dict[str, Any]]:
    mode = str(transfer_mode or "").strip().casefold()
    if mode not in {"server_and_data", "server_only", "backup_assisted"}:
        raise ServiceError("Transfer mode is invalid.", code="invalid_transfer_mode")
    if not isinstance(data_validation, Mapping):
        raise ServiceError("Transfer validation is invalid.", code="data_validation_required")

    if mode == "server_only":
        if data_validation.get("warning_confirmed") is not True:
            raise ServiceError(
                "Server Only requires an explicit data-loss warning confirmation.",
                code="server_only_confirmation_required",
            )
        if set(data_validation) != {"warning_confirmed"}:
            raise ServiceError(
                "Server Only validation contains unsupported fields.",
                code="data_validation_required",
            )
        return mode, {"warning_confirmed": True}

    common_fields = {
        "verified",
        "transaction_id",
        "manifest_sha256",
        "active_tree_sha256",
        "record_counts",
    }
    backup_fields = {
        "source_kind",
        "backup_id",
        "backup_manifest_sha256",
    }
    expected_fields = common_fields | (backup_fields if mode == "backup_assisted" else set())
    if set(data_validation) != expected_fields:
        raise ServiceError(
            "The verified migration receipt does not match the selected transfer mode.",
            code="data_validation_required",
        )
    if data_validation.get("verified") is not True:
        raise ServiceError(
            "Server and Data transfer requires a verified staged migration package.",
            code="data_validation_required",
        )
    transaction_id = validate_installation_id(data_validation.get("transaction_id"))
    manifest_sha256 = str(data_validation.get("manifest_sha256") or "").strip().casefold()
    active_tree_sha256 = str(data_validation.get("active_tree_sha256") or "").strip().casefold()
    if not _is_sha256(manifest_sha256) or not _is_sha256(active_tree_sha256):
        raise ServiceError(
            "The verified migration receipt is incomplete.",
            code="data_validation_required",
        )
    counts = data_validation.get("record_counts")
    if not isinstance(counts, Mapping) or len(counts) > 32:
        raise ServiceError("Migration record counts are invalid.", code="data_validation_required")
    clean_counts: dict[str, int] = {}
    for key, value in counts.items():
        name = str(key)
        if not name or len(name) > 80 or not isinstance(value, int) or isinstance(value, bool):
            raise ServiceError("Migration record counts are invalid.", code="data_validation_required")
        if value < 0 or value > 100_000_000:
            raise ServiceError("Migration record counts are invalid.", code="data_validation_required")
        clean_counts[name] = value
    normalized = {
        "verified": True,
        "transaction_id": transaction_id,
        "manifest_sha256": manifest_sha256,
        "active_tree_sha256": active_tree_sha256,
        "record_counts": dict(sorted(clean_counts.items())),
    }
    if mode == "backup_assisted":
        if str(data_validation.get("source_kind") or "") != "portable_backup":
            raise ServiceError(
                "The verified receipt is not a School CSM recovery backup.",
                code="data_validation_required",
            )
        backup_id = validate_installation_id(data_validation.get("backup_id"))
        backup_manifest_sha256 = str(
            data_validation.get("backup_manifest_sha256") or ""
        ).strip().casefold()
        if not _is_sha256(backup_manifest_sha256):
            raise ServiceError(
                "The verified recovery-backup receipt is incomplete.",
                code="data_validation_required",
            )
        normalized.update(
            {
                "source_kind": "portable_backup",
                "backup_id": backup_id,
                "backup_manifest_sha256": backup_manifest_sha256,
            }
        )
    return mode, normalized


def _is_sha256(value: Any) -> bool:
    text = str(value or "")
    return len(text) == 64 and all(character in "0123456789abcdef" for character in text)


class RegistrationRepository:
    """SQLite repository with immediate transactions for one-active cutover."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.transaction() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS service_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS activation_codes (
                    code_hash TEXT PRIMARY KEY,
                    school_id TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    used_at TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS schools (
                    school_id TEXT PRIMARY KEY,
                    school_name TEXT NOT NULL,
                    public_host TEXT NOT NULL UNIQUE,
                    registration_id TEXT NOT NULL UNIQUE,
                    active_installation_id TEXT,
                    tunnel_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS district_schools (
                    school_id TEXT PRIMARY KEY,
                    school_name TEXT NOT NULL,
                    school_district TEXT NOT NULL,
                    school_division TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('approved','suspended')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS installations (
                    installation_id TEXT PRIMARY KEY,
                    school_id TEXT NOT NULL REFERENCES schools(school_id),
                    state TEXT NOT NULL CHECK(state IN ('active','transferred','revoked')),
                    secret_hash TEXT NOT NULL,
                    registered_at TEXT NOT NULL,
                    retired_at TEXT,
                    retirement_transaction_id TEXT
                );
                CREATE TABLE IF NOT EXISTS installation_heartbeats (
                    installation_id TEXT PRIMARY KEY REFERENCES installations(installation_id),
                    school_id TEXT NOT NULL REFERENCES schools(school_id),
                    received_at TEXT NOT NULL,
                    application_version TEXT NOT NULL,
                    local_server_state TEXT NOT NULL,
                    gateway_state TEXT NOT NULL,
                    survey_status TEXT NOT NULL,
                    error_code TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_installation_per_school
                    ON installations(school_id) WHERE state='active';
                CREATE TABLE IF NOT EXISTS passkeys (
                    credential_id TEXT PRIMARY KEY,
                    school_id TEXT NOT NULL REFERENCES schools(school_id),
                    public_key BLOB NOT NULL,
                    sign_count INTEGER NOT NULL DEFAULT 0,
                    label TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    last_used_at TEXT
                );
                CREATE TABLE IF NOT EXISTS ceremonies (
                    ceremony_id TEXT PRIMARY KEY,
                    purpose TEXT NOT NULL CHECK(purpose IN ('registration','transfer')),
                    school_id TEXT NOT NULL,
                    installation_id TEXT NOT NULL,
                    challenge BLOB NOT NULL,
                    context_json TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    consumed_at TEXT
                );
                CREATE TABLE IF NOT EXISTS transfers (
                    transaction_id TEXT PRIMARY KEY,
                    school_id TEXT NOT NULL REFERENCES schools(school_id),
                    source_installation_id TEXT NOT NULL,
                    destination_installation_id TEXT NOT NULL,
                    transfer_mode TEXT NOT NULL CHECK(transfer_mode IN ('server_and_data','server_only','backup_assisted')),
                    status TEXT NOT NULL CHECK(status IN ('pending','completed','cancelled')),
                    data_validation_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    completed_at TEXT
                );
                CREATE TABLE IF NOT EXISTS transfer_intents (
                    intent_hash TEXT PRIMARY KEY,
                    school_id TEXT NOT NULL,
                    destination_installation_id TEXT NOT NULL,
                    transfer_mode TEXT NOT NULL CHECK(transfer_mode IN ('server_and_data','server_only','backup_assisted')),
                    data_validation_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    consumed_at TEXT
                );
                CREATE TABLE IF NOT EXISTS passkey_ceremonies (
                    ceremony_id TEXT PRIMARY KEY,
                    purpose TEXT NOT NULL CHECK(purpose IN ('add_authorization','add_registration','reset_registration')),
                    school_id TEXT NOT NULL,
                    challenge BLOB NOT NULL,
                    context_json TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    consumed_at TEXT
                );
                CREATE TABLE IF NOT EXISTS passkey_reset_codes (
                    code_hash TEXT PRIMARY KEY,
                    school_id TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    used_at TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS audit_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    occurred_at TEXT NOT NULL,
                    school_id TEXT,
                    installation_id TEXT,
                    event_type TEXT NOT NULL,
                    detail_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS credential_handoffs (
                    code_hash TEXT PRIMARY KEY,
                    installation_id TEXT NOT NULL,
                    operation_id TEXT,
                    request_fingerprint TEXT,
                    nonce BLOB NOT NULL,
                    ciphertext BLOB NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    used_at TEXT,
                    delivered_at TEXT,
                    acknowledgement_hash TEXT,
                    delivery_count INTEGER NOT NULL DEFAULT 0
                );
                """
            )
            handoff_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(credential_handoffs)")
            }
            if "operation_id" not in handoff_columns:
                connection.execute(
                    "ALTER TABLE credential_handoffs ADD COLUMN operation_id TEXT"
                )
            if "request_fingerprint" not in handoff_columns:
                connection.execute(
                    "ALTER TABLE credential_handoffs ADD COLUMN request_fingerprint TEXT"
                )
            if "delivered_at" not in handoff_columns:
                connection.execute(
                    "ALTER TABLE credential_handoffs ADD COLUMN delivered_at TEXT"
                )
            if "acknowledgement_hash" not in handoff_columns:
                connection.execute(
                    "ALTER TABLE credential_handoffs ADD COLUMN acknowledgement_hash TEXT"
                )
            if "delivery_count" not in handoff_columns:
                connection.execute(
                    """ALTER TABLE credential_handoffs
                       ADD COLUMN delivery_count INTEGER NOT NULL DEFAULT 0"""
                )
            connection.execute(
                """CREATE UNIQUE INDEX IF NOT EXISTS one_handoff_per_operation
                   ON credential_handoffs(operation_id) WHERE operation_id IS NOT NULL"""
            )
            connection.execute(
                """INSERT OR IGNORE INTO district_schools(
                       school_id,school_name,school_district,school_division,
                       status,created_at,updated_at
                   )
                   SELECT school_id,school_name,'Schools District of Motiong',
                          'Schools Division of Samar','approved',created_at,updated_at
                   FROM schools"""
            )
            schema_row = connection.execute(
                "SELECT value FROM service_meta WHERE key='schema_version'"
            ).fetchone()
            if schema_row is not None:
                try:
                    existing_schema = int(str(schema_row["value"]))
                except (TypeError, ValueError) as exc:
                    raise ServiceError(
                        "The registration database schema version is invalid.",
                        code="schema_invalid",
                    ) from exc
                if existing_schema > SCHEMA_VERSION:
                    raise ServiceError(
                        "The registration database was created by a newer service version.",
                        code="schema_newer",
                    )
            connection.execute(
                """INSERT INTO service_meta(key,value) VALUES('schema_version',?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
                (str(SCHEMA_VERSION),),
            )

    def issue_activation_code(
        self,
        school_id: str,
        *,
        valid_for: timedelta = timedelta(days=14),
        replace_existing: bool = False,
    ) -> str:
        school_id = validate_school_id(school_id)
        with self.transaction() as connection:
            return self._issue_activation_code_in_transaction(
                connection,
                school_id,
                valid_for=valid_for,
                replace_existing=replace_existing,
            )

    def _issue_activation_code_in_transaction(
        self,
        connection: sqlite3.Connection,
        school_id: str,
        *,
        valid_for: timedelta,
        replace_existing: bool,
    ) -> str:
        """Issue a code while retaining the caller's write lock."""

        code = "-".join(secrets.token_hex(3).upper() for _ in range(3))
        digest = hashlib.sha256(code.encode("utf-8")).hexdigest()
        now = utc_now()
        superseded = 0
        if replace_existing:
            superseded = connection.execute(
                """UPDATE activation_codes SET used_at=?
                   WHERE school_id=? AND used_at IS NULL AND expires_at>?""",
                (timestamp(now), school_id, timestamp(now)),
            ).rowcount
        connection.execute(
            "INSERT INTO activation_codes(code_hash,school_id,expires_at,created_at) VALUES(?,?,?,?)",
            (digest, school_id, timestamp(now + valid_for), timestamp(now)),
        )
        self._audit(
            connection,
            school_id,
            "",
            "activation_code_issued",
            {"superseded_unused_codes": int(superseded)},
        )
        return code

    def create_ceremony(
        self,
        *,
        purpose: str,
        school_id: str,
        installation_id: str,
        challenge: bytes,
        context: Mapping[str, Any],
        valid_for: timedelta = timedelta(minutes=5),
    ) -> str:
        if purpose not in {"registration", "transfer"}:
            raise ServiceError("Ceremony purpose is invalid.")
        ceremony_id = str(uuid4())
        now = utc_now()
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO ceremonies(
                    ceremony_id,purpose,school_id,installation_id,challenge,
                    context_json,expires_at
                ) VALUES(?,?,?,?,?,?,?)""",
                (
                    ceremony_id,
                    purpose,
                    validate_school_id(school_id),
                    validate_installation_id(installation_id),
                    bytes(challenge),
                    json.dumps(dict(context), sort_keys=True, separators=(",", ":")),
                    timestamp(now + valid_for),
                ),
            )
        return ceremony_id

    def consume_ceremony(self, ceremony_id: str, purpose: str) -> Mapping[str, Any]:
        now = utc_now()
        consumed_at = timestamp(now)
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM ceremonies WHERE ceremony_id=?", (str(ceremony_id),)
            ).fetchone()
            if row is None or row["purpose"] != purpose:
                raise ServiceError("The passkey ceremony was not found.", code="ceremony_not_found")
            if row["consumed_at"] or datetime.fromisoformat(row["expires_at"]) <= now:
                raise ServiceError("The passkey ceremony expired or was already used.", code="ceremony_expired")
            connection.execute(
                "UPDATE ceremonies SET consumed_at=? WHERE ceremony_id=? AND consumed_at IS NULL",
                (consumed_at, str(ceremony_id)),
            )
            return {
                **dict(row),
                "challenge": bytes(row["challenge"]),
                "context": json.loads(row["context_json"]),
                "consumption_token": consumed_at,
            }

    def release_ceremony(
        self, ceremony_id: str, purpose: str, consumption_token: str
    ) -> None:
        """Release only the caller's failed ceremony claim for a safe retry."""

        with self.transaction() as connection:
            connection.execute(
                """UPDATE ceremonies SET consumed_at=NULL
                   WHERE ceremony_id=? AND purpose=? AND consumed_at=?""",
                (str(ceremony_id), str(purpose), str(consumption_token)),
            )

    def create_transfer_intent(
        self,
        *,
        school_id: str,
        destination_installation_id: str,
        transfer_mode: str,
        data_validation: Mapping[str, Any],
        valid_for: timedelta = timedelta(minutes=10),
    ) -> Mapping[str, Any]:
        """Create a short-lived bearer intent after desktop-side validation.

        The browser receives only the opaque intent identifier.  It cannot edit
        the transfer mode or claim that a migration package was verified.
        """

        school = validate_school_id(school_id)
        destination = validate_installation_id(destination_installation_id)
        mode, validation = _validated_transfer_intent(transfer_mode, data_validation)
        with self.read() as connection:
            row = connection.execute(
                "SELECT active_installation_id FROM schools WHERE school_id=?",
                (school,),
            ).fetchone()
        if row is None or not row["active_installation_id"]:
            raise ServiceError(
                "The School ID does not have an active Internet Server.",
                code="school_not_active",
            )
        if str(row["active_installation_id"]) == destination:
            raise ServiceError(
                "The destination is already the active Internet Server.",
                code="destination_already_active",
            )

        intent_id = secrets.token_urlsafe(32)
        digest = hashlib.sha256(intent_id.encode("ascii")).hexdigest()
        now = utc_now()
        expires_at = timestamp(now + valid_for)
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO transfer_intents(
                    intent_hash,school_id,destination_installation_id,transfer_mode,
                    data_validation_json,created_at,expires_at
                ) VALUES(?,?,?,?,?,?,?)""",
                (
                    digest,
                    school,
                    destination,
                    mode,
                    json.dumps(validation, sort_keys=True, separators=(",", ":")),
                    timestamp(now),
                    expires_at,
                ),
            )
            self._audit(
                connection,
                school,
                destination,
                "transfer_intent_created",
                {
                    "transfer_mode": mode,
                    "transaction_id": validation.get("transaction_id", ""),
                    "manifest_sha256": validation.get("manifest_sha256", ""),
                },
            )
        return {"intent_id": intent_id, "expires_at": expires_at}

    def transfer_intent(self, intent_id: str, *, consume: bool = False) -> Mapping[str, Any]:
        value = str(intent_id or "").strip()
        if not 32 <= len(value) <= 96 or not value.isascii():
            raise ServiceError("The transfer intent is invalid.", code="transfer_intent_invalid")
        digest = hashlib.sha256(value.encode("ascii")).hexdigest()
        now = utc_now()
        consumed_at = timestamp(now)
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM transfer_intents WHERE intent_hash=?", (digest,)
            ).fetchone()
            if row is None:
                raise ServiceError("The transfer intent was not found.", code="transfer_intent_invalid")
            if row["consumed_at"] or datetime.fromisoformat(row["expires_at"]) <= now:
                raise ServiceError(
                    "The transfer intent expired or was already used.",
                    code="transfer_intent_expired",
                )
            if consume:
                changed = connection.execute(
                    """UPDATE transfer_intents SET consumed_at=?
                       WHERE intent_hash=? AND consumed_at IS NULL""",
                    (consumed_at, digest),
                ).rowcount
                if changed != 1:
                    raise ServiceError(
                        "The transfer intent expired or was already used.",
                        code="transfer_intent_expired",
                    )
            return {
                "school_id": str(row["school_id"]),
                "destination_installation_id": str(row["destination_installation_id"]),
                "transfer_mode": str(row["transfer_mode"]),
                "data_validation": json.loads(row["data_validation_json"]),
                "expires_at": str(row["expires_at"]),
                "consumption_token": consumed_at if consume else "",
            }

    def release_transfer_intent(self, intent_id: str, consumption_token: str) -> None:
        """Release only the caller's failed transfer-intent claim."""

        value = str(intent_id or "").strip()
        if not 32 <= len(value) <= 96 or not value.isascii():
            return
        digest = hashlib.sha256(value.encode("ascii")).hexdigest()
        with self.transaction() as connection:
            connection.execute(
                """UPDATE transfer_intents SET consumed_at=NULL
                   WHERE intent_hash=? AND consumed_at=?""",
                (digest, str(consumption_token)),
            )

    def create_passkey_ceremony(
        self,
        *,
        purpose: str,
        school_id: str,
        challenge: bytes,
        context: Mapping[str, Any],
        valid_for: timedelta = timedelta(minutes=5),
    ) -> str:
        if purpose not in {"add_authorization", "add_registration", "reset_registration"}:
            raise ServiceError("Passkey ceremony purpose is invalid.")
        ceremony_id = str(uuid4())
        now = utc_now()
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO passkey_ceremonies(
                    ceremony_id,purpose,school_id,challenge,context_json,expires_at
                ) VALUES(?,?,?,?,?,?)""",
                (
                    ceremony_id,
                    purpose,
                    validate_school_id(school_id),
                    bytes(challenge),
                    json.dumps(dict(context), sort_keys=True, separators=(",", ":")),
                    timestamp(now + valid_for),
                ),
            )
        return ceremony_id

    def consume_passkey_ceremony(
        self, ceremony_id: str, purpose: str
    ) -> Mapping[str, Any]:
        now = utc_now()
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM passkey_ceremonies WHERE ceremony_id=?", (str(ceremony_id),)
            ).fetchone()
            if row is None or row["purpose"] != purpose:
                raise ServiceError("The passkey ceremony was not found.", code="ceremony_not_found")
            if row["consumed_at"] or datetime.fromisoformat(row["expires_at"]) <= now:
                raise ServiceError(
                    "The passkey ceremony expired or was already used.",
                    code="ceremony_expired",
                )
            changed = connection.execute(
                """UPDATE passkey_ceremonies SET consumed_at=?
                   WHERE ceremony_id=? AND consumed_at IS NULL""",
                (timestamp(now), str(ceremony_id)),
            ).rowcount
            if changed != 1:
                raise ServiceError(
                    "The passkey ceremony expired or was already used.",
                    code="ceremony_expired",
                )
            return {
                **dict(row),
                "challenge": bytes(row["challenge"]),
                "context": json.loads(row["context_json"]),
            }

    def issue_passkey_reset_code(
        self, school_id: str, *, valid_for: timedelta = timedelta(hours=1)
    ) -> str:
        school = validate_school_id(school_id)
        with self.read() as connection:
            if connection.execute(
                "SELECT 1 FROM schools WHERE school_id=?", (school,)
            ).fetchone() is None:
                raise ServiceError("The School ID is not registered.", code="school_not_found")
        code = "-".join(secrets.token_hex(4).upper() for _ in range(3))
        digest = hashlib.sha256(code.encode("ascii")).hexdigest()
        now = utc_now()
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO passkey_reset_codes(code_hash,school_id,expires_at,created_at) VALUES(?,?,?,?)",
                (digest, school, timestamp(now + valid_for), timestamp(now)),
            )
            self._audit(connection, school, "", "passkey_reset_code_issued", {})
        return code

    def validate_passkey_reset_code(self, school_id: str, code: str) -> str:
        school = validate_school_id(school_id)
        digest = hashlib.sha256(str(code or "").strip().upper().encode("utf-8")).hexdigest()
        now = utc_now()
        with self.read() as connection:
            row = connection.execute(
                "SELECT * FROM passkey_reset_codes WHERE code_hash=? AND school_id=?",
                (digest, school),
            ).fetchone()
        if row is None or row["used_at"] or datetime.fromisoformat(row["expires_at"]) <= now:
            raise ServiceError(
                "The passkey recovery code is invalid or expired.",
                code="passkey_reset_invalid",
            )
        return digest

    @staticmethod
    def _audit(
        connection: sqlite3.Connection,
        school_id: str,
        installation_id: str,
        event_type: str,
        detail: Mapping[str, Any],
    ) -> None:
        connection.execute(
            """INSERT INTO audit_events(
                event_id,occurred_at,school_id,installation_id,event_type,detail_json
            ) VALUES(?,?,?,?,?,?)""",
            (
                str(uuid4()),
                timestamp(),
                school_id or None,
                installation_id or None,
                event_type,
                json.dumps(dict(detail), sort_keys=True, separators=(",", ":")),
            ),
        )


@dataclass(frozen=True)
class RegistrationResult:
    registration_id: str
    school_id: str
    installation_id: str
    public_host: str
    tunnel_id: str
    tunnel_token: str
    installation_secret: str
    completion_code: str = ""


class RegistrationService:
    def __init__(
        self,
        repository: RegistrationRepository,
        *,
        managed_domain: str,
        provisioner: TunnelProvisioner | None = None,
        signer: AuthorizationSigner | None = None,
    ) -> None:
        self.repository = repository
        self.managed_domain = str(managed_domain).strip().casefold().strip(".")
        if not self.managed_domain or "." not in self.managed_domain:
            raise ValueError("A managed public domain is required.")
        self.provisioner = provisioner or UnconfiguredTunnelProvisioner()
        self.signer = signer

    def school_lookup(self, school_id: str) -> Mapping[str, Any]:
        school_id = validate_school_id(school_id)
        with self.repository.read() as connection:
            row = connection.execute(
                "SELECT school_id,school_name,public_host,active_installation_id FROM schools WHERE school_id=?",
                (school_id,),
            ).fetchone()
        return {
            "school_id": school_id,
            "registered": row is not None,
            "school_name": str(row["school_name"]) if row else "",
            "public_host": str(row["public_host"]) if row else f"{school_id}.{self.managed_domain}",
            "has_active_server": bool(row and row["active_installation_id"]),
        }

    def save_directory_school(
        self,
        *,
        school_id: str,
        school_name: str,
        school_district: str = "Schools District of Motiong",
        school_division: str = "Schools Division of Samar",
        status: str | None = None,
    ) -> Mapping[str, Any]:
        school = validate_school_id(school_id)
        name = _clean_required_text(school_name, label="School name")
        district = _clean_required_text(
            school_district, label="School district", maximum=160
        )
        division = _clean_required_text(
            school_division, label="School division", maximum=160
        )
        directory_status = (
            _directory_status(status) if status is not None else DIRECTORY_APPROVED
        )
        now = timestamp()
        with self.repository.transaction() as connection:
            connection.execute(
                """INSERT INTO district_schools(
                       school_id,school_name,school_district,school_division,
                       status,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?)
                   ON CONFLICT(school_id) DO UPDATE SET
                       school_name=excluded.school_name,
                       school_district=excluded.school_district,
                       school_division=excluded.school_division,
                       status=CASE WHEN ? IS NULL
                                   THEN district_schools.status
                                   ELSE excluded.status END,
                       updated_at=excluded.updated_at""",
                (
                    school,
                    name,
                    district,
                    division,
                    directory_status,
                    now,
                    now,
                    status,
                ),
            )
            saved = connection.execute(
                "SELECT status FROM district_schools WHERE school_id=?", (school,)
            ).fetchone()
            self.repository._audit(
                connection,
                school,
                "",
                "district_school_saved",
                {"status": str(saved["status"]) if saved else directory_status},
            )
        return self.district_school_detail(school)

    def set_directory_school_status(
        self, school_id: str, status: str
    ) -> Mapping[str, Any]:
        school = validate_school_id(school_id)
        directory_status = _directory_status(status)
        with self.repository.transaction() as connection:
            changed = connection.execute(
                """UPDATE district_schools SET status=?,updated_at=?
                   WHERE school_id=?""",
                (directory_status, timestamp(), school),
            ).rowcount
            if changed != 1:
                raise ServiceError(
                    "The school is not in the district directory.",
                    code="school_not_found",
                )
            self.repository._audit(
                connection,
                school,
                "",
                "district_school_status_changed",
                {"status": directory_status},
            )
        return self.district_school_detail(school)

    def issue_directory_activation_code(
        self, school_id: str, *, valid_for: timedelta = timedelta(days=14)
    ) -> str:
        school = validate_school_id(school_id)
        duration = min(max(valid_for, timedelta(hours=1)), timedelta(days=30))
        with self.repository.transaction() as connection:
            directory = connection.execute(
                "SELECT status FROM district_schools WHERE school_id=?", (school,)
            ).fetchone()
            registered = connection.execute(
                "SELECT 1 FROM schools WHERE school_id=?", (school,)
            ).fetchone()
            if directory is None:
                raise ServiceError(
                    "Add and approve the school in the district directory first.",
                    code="school_not_found",
                )
            if str(directory["status"]) != DIRECTORY_APPROVED:
                raise ServiceError(
                    "The school is suspended and cannot receive an activation code.",
                    code="school_suspended",
                )
            if registered is not None:
                raise ServiceError(
                    "This School ID is already registered.",
                    code="school_already_registered",
                )
            return self.repository._issue_activation_code_in_transaction(
                connection,
                school,
                valid_for=duration,
                replace_existing=True,
            )

    def district_overview(self) -> Mapping[str, Any]:
        schools = self.list_district_schools(limit=500)
        counts = {
            "total": len(schools),
            "pending": sum(item["health_status"] == "pending" for item in schools),
            "registered": sum(item["registration_state"] == "registered" for item in schools),
            "connected": sum(item["health_status"] == "connected" for item in schools),
            "attention": sum(
                item["health_status"]
                in {"attention", "disconnected", "stale", "unseen", "suspended"}
                for item in schools
            ),
        }
        return {"counts": counts, "generated_at": timestamp()}

    def list_district_schools(
        self,
        *,
        query: str = "",
        status: str = "all",
        limit: int = 200,
    ) -> list[Mapping[str, Any]]:
        search = " ".join(str(query or "").split())[:160]
        requested_status = str(status or "all").strip().casefold()
        allowed_filters = {
            "all",
            DIRECTORY_APPROVED,
            DIRECTORY_SUSPENDED,
            "registered",
            "pending",
            "connected",
            "attention",
            "offline",
            "maintenance",
        }
        if requested_status not in allowed_filters:
            raise ServiceError("The school filter is invalid.", code="invalid_filter")
        maximum = min(max(1, int(limit)), 500)
        parameters: list[Any] = []
        where = ""
        if search:
            where = "WHERE d.school_id LIKE ? OR d.school_name LIKE ?"
            pattern = f"%{search}%"
            parameters.extend((pattern, pattern))
        # Apply filtered result limits after classification so a matching
        # school cannot disappear merely because earlier alphabetical rows had
        # different health states.
        parameters.append(maximum if requested_status == "all" else 500)
        with self.repository.read() as connection:
            rows = connection.execute(
                f"""SELECT d.*,s.public_host,s.registration_id,
                            s.active_installation_id,s.tunnel_id,
                            i.state AS installation_state,i.registered_at,
                            h.received_at,h.application_version,
                            h.local_server_state,h.gateway_state,
                            h.survey_status,h.error_code,
                            (SELECT COUNT(*) FROM passkeys p
                             WHERE p.school_id=d.school_id) AS passkey_count,
                            (SELECT COUNT(*) FROM activation_codes a
                             WHERE a.school_id=d.school_id AND a.used_at IS NULL
                               AND a.expires_at>?) AS pending_activation_count
                     FROM district_schools d
                     LEFT JOIN schools s ON s.school_id=d.school_id
                     LEFT JOIN installations i
                            ON i.installation_id=s.active_installation_id
                     LEFT JOIN installation_heartbeats h
                            ON h.installation_id=s.active_installation_id
                     {where}
                     ORDER BY d.school_name COLLATE NOCASE,d.school_id
                     LIMIT ?""",
                (timestamp(), *parameters),
            ).fetchall()
        documents = [self._district_school_document(row) for row in rows]
        if requested_status == "all":
            return documents
        if requested_status in DIRECTORY_STATUSES:
            filtered = [
                item for item in documents if item["directory_status"] == requested_status
            ]
        elif requested_status == "registered":
            filtered = [
                item for item in documents if item["registration_state"] == "registered"
            ]
        elif requested_status == "attention":
            filtered = [
                item
                for item in documents
                if item["health_status"]
                in {"attention", "disconnected", "stale", "unseen", "suspended"}
            ]
        elif requested_status in {"offline", "maintenance"}:
            filtered = [
                item
                for item in documents
                if item["survey_status"] == requested_status
            ]
        else:
            filtered = [
                item for item in documents if item["health_status"] == requested_status
            ]
        return filtered[:maximum]

    def district_school_detail(self, school_id: str) -> Mapping[str, Any]:
        school = validate_school_id(school_id)
        with self.repository.read() as connection:
            row = connection.execute(
                """SELECT d.*,s.public_host,s.registration_id,
                          s.active_installation_id,s.tunnel_id,
                          i.state AS installation_state,i.registered_at,
                          h.received_at,h.application_version,
                          h.local_server_state,h.gateway_state,
                          h.survey_status,h.error_code,
                          (SELECT COUNT(*) FROM passkeys p
                           WHERE p.school_id=d.school_id) AS passkey_count,
                          (SELECT COUNT(*) FROM activation_codes a
                           WHERE a.school_id=d.school_id AND a.used_at IS NULL
                             AND a.expires_at>?) AS pending_activation_count
                   FROM district_schools d
                   LEFT JOIN schools s ON s.school_id=d.school_id
                   LEFT JOIN installations i
                          ON i.installation_id=s.active_installation_id
                   LEFT JOIN installation_heartbeats h
                          ON h.installation_id=s.active_installation_id
                   WHERE d.school_id=?""",
                (timestamp(), school),
            ).fetchone()
            if row is None:
                raise ServiceError(
                    "The school is not in the district directory.",
                    code="school_not_found",
                )
            installations = [
                dict(item)
                for item in connection.execute(
                    """SELECT installation_id,state,registered_at,retired_at,
                              retirement_transaction_id
                       FROM installations WHERE school_id=?
                       ORDER BY registered_at DESC,installation_id""",
                    (school,),
                ).fetchall()
            ]
            passkeys = [
                dict(item)
                for item in connection.execute(
                    """SELECT label,created_at,last_used_at FROM passkeys
                       WHERE school_id=? ORDER BY created_at,label""",
                    (school,),
                ).fetchall()
            ]
        document = dict(self._district_school_document(row))
        document["installations"] = installations
        document["passkeys"] = passkeys
        return document

    def list_audit_events(
        self,
        *,
        school_id: str = "",
        event_type: str = "",
        limit: int = 100,
    ) -> list[Mapping[str, Any]]:
        school = validate_school_id(school_id) if str(school_id or "").strip() else ""
        event = str(event_type or "").strip()[:80]
        if event and not all(character.isalnum() or character in "_-" for character in event):
            raise ServiceError("The audit event filter is invalid.", code="invalid_filter")
        where: list[str] = []
        parameters: list[Any] = []
        if school:
            where.append("school_id=?")
            parameters.append(school)
        if event:
            where.append("event_type=?")
            parameters.append(event)
        clause = " WHERE " + " AND ".join(where) if where else ""
        parameters.append(min(max(1, int(limit)), 500))
        with self.repository.read() as connection:
            rows = connection.execute(
                """SELECT sequence,event_id,occurred_at,school_id,
                          installation_id,event_type,detail_json
                   FROM audit_events"""
                + clause
                + " ORDER BY sequence DESC LIMIT ?",
                parameters,
            ).fetchall()
        result: list[Mapping[str, Any]] = []
        for row in rows:
            document = dict(row)
            try:
                detail = json.loads(str(document.pop("detail_json") or "{}"))
            except json.JSONDecodeError:
                detail = {}
            document["detail"] = detail if isinstance(detail, Mapping) else {}
            result.append(document)
        return result

    def record_heartbeat(
        self,
        *,
        installation_id: str,
        installation_secret: str,
        application_version: str,
        local_server_state: str,
        gateway_state: str,
        survey_status: str,
        error_code: str = "",
    ) -> Mapping[str, Any]:
        installation = self.authenticate_installation(
            installation_id, installation_secret
        )
        if str(installation["state"]) != ACTIVE:
            raise ServiceError(
                "This installation is not the active school server.",
                code="authorization_failed",
            )
        application = str(application_version or "").strip()[:40]
        server = str(local_server_state or "").strip().casefold()
        gateway = str(gateway_state or "").strip().casefold()
        survey = str(survey_status or "").strip().casefold()
        error = str(error_code or "").strip()[:80]
        if not application or any(character.isspace() for character in application):
            raise ServiceError("Application version is invalid.", code="invalid_heartbeat")
        if server not in {"running", "stopped", "starting", "stopping", "error"}:
            raise ServiceError("Local server state is invalid.", code="invalid_heartbeat")
        if gateway not in {
            "connected",
            "disconnected",
            "connecting",
            "error",
            "not_configured",
        }:
            raise ServiceError("Gateway state is invalid.", code="invalid_heartbeat")
        if survey not in {"online", "offline", "maintenance"}:
            raise ServiceError("Survey status is invalid.", code="invalid_heartbeat")
        if error not in {
            "",
            "authorization_offline",
            "gateway_error",
            "local_server_error",
            "tunnel_start_failed",
        }:
            raise ServiceError("Heartbeat error code is invalid.", code="invalid_heartbeat")
        received = timestamp()
        with self.repository.transaction() as connection:
            connection.execute(
                """INSERT INTO installation_heartbeats(
                       installation_id,school_id,received_at,application_version,
                       local_server_state,gateway_state,survey_status,error_code
                   ) VALUES(?,?,?,?,?,?,?,?)
                   ON CONFLICT(installation_id) DO UPDATE SET
                       school_id=excluded.school_id,
                       received_at=excluded.received_at,
                       application_version=excluded.application_version,
                       local_server_state=excluded.local_server_state,
                       gateway_state=excluded.gateway_state,
                       survey_status=excluded.survey_status,
                       error_code=excluded.error_code""",
                (
                    str(installation["installation_id"]),
                    str(installation["school_id"]),
                    received,
                    application,
                    server,
                    gateway,
                    survey,
                    error,
                ),
            )
        return {"ok": True, "received_at": received}

    @staticmethod
    def _district_school_document(row: Mapping[str, Any]) -> Mapping[str, Any]:
        values = dict(row)
        registered = bool(values.get("public_host"))
        last_seen = str(values.get("received_at") or "")
        fresh = False
        if last_seen:
            try:
                observed = datetime.fromisoformat(last_seen)
                fresh = utc_now() - observed.astimezone(timezone.utc) <= timedelta(minutes=20)
            except (ValueError, TypeError):
                fresh = False
        if str(values.get("status")) == DIRECTORY_SUSPENDED:
            health = "suspended"
        elif not registered:
            health = "pending"
        elif not last_seen:
            health = "unseen"
        elif not fresh:
            health = "stale"
        elif (
            str(values.get("gateway_state")) == "error"
            or bool(values.get("error_code"))
            or str(values.get("local_server_state")) != "running"
        ):
            health = "attention"
        elif str(values.get("gateway_state")) == "connected":
            health = "connected"
        else:
            health = "disconnected"
        return {
            "school_id": str(values.get("school_id") or ""),
            "school_name": str(values.get("school_name") or ""),
            "school_district": str(values.get("school_district") or ""),
            "school_division": str(values.get("school_division") or ""),
            "directory_status": str(values.get("status") or DIRECTORY_APPROVED),
            "registration_state": "registered" if registered else "not_registered",
            "public_host": str(values.get("public_host") or ""),
            "registration_id": str(values.get("registration_id") or ""),
            "active_installation_id": str(values.get("active_installation_id") or ""),
            "installation_state": str(values.get("installation_state") or ""),
            "registered_at": str(values.get("registered_at") or ""),
            "last_seen_at": last_seen,
            "application_version": str(values.get("application_version") or ""),
            "local_server_state": str(values.get("local_server_state") or "unknown"),
            "gateway_state": str(values.get("gateway_state") or "unknown"),
            "survey_status": str(values.get("survey_status") or "unknown"),
            "error_code": str(values.get("error_code") or ""),
            "passkey_count": int(values.get("passkey_count") or 0),
            "pending_activation_count": int(values.get("pending_activation_count") or 0),
            "health_status": health,
        }

    def list_passkeys(self, school_id: str) -> list[Mapping[str, Any]]:
        school = validate_school_id(school_id)
        with self.repository.read() as connection:
            rows = connection.execute(
                """SELECT credential_id,label,created_at,last_used_at
                   FROM passkeys WHERE school_id=? ORDER BY created_at,credential_id""",
                (school,),
            ).fetchall()
        return [dict(row) for row in rows]

    def add_passkey(
        self,
        *,
        school_id: str,
        credential_id: str,
        credential_public_key: bytes,
        sign_count: int,
        label: str,
        authorized_by: str,
    ) -> None:
        school = validate_school_id(school_id)
        credential = str(credential_id or "").strip()
        name = " ".join(str(label or "Additional administrator passkey").split())[:120]
        if not credential or len(credential) > 1024 or not credential_public_key:
            raise ServiceError("The new passkey is invalid.", code="passkey_invalid")
        try:
            with self.repository.transaction() as connection:
                if connection.execute(
                    "SELECT 1 FROM schools WHERE school_id=?", (school,)
                ).fetchone() is None:
                    raise ServiceError("The School ID is not registered.", code="school_not_found")
                connection.execute(
                    """INSERT INTO passkeys(
                        credential_id,school_id,public_key,sign_count,label,created_at
                    ) VALUES(?,?,?,?,?,?)""",
                    (
                        credential,
                        school,
                        bytes(credential_public_key),
                        max(0, int(sign_count)),
                        name or "Additional administrator passkey",
                        timestamp(),
                    ),
                )
                self.repository._audit(
                    connection,
                    school,
                    "",
                    "administrator_passkey_added",
                    {
                        "authorized_by_sha256": hashlib.sha256(
                            str(authorized_by).encode("utf-8")
                        ).hexdigest(),
                        "label": name,
                    },
                )
        except sqlite3.IntegrityError:
            raise ServiceError("This passkey is already registered.", code="passkey_exists") from None

    def reset_passkeys(
        self,
        *,
        school_id: str,
        reset_code_hash: str,
        credential_id: str,
        credential_public_key: bytes,
        sign_count: int,
        label: str,
    ) -> None:
        school = validate_school_id(school_id)
        credential = str(credential_id or "").strip()
        now = utc_now()
        if not credential or len(credential) > 1024 or not credential_public_key:
            raise ServiceError("The new passkey is invalid.", code="passkey_invalid")
        with self.repository.transaction() as connection:
            reset = connection.execute(
                "SELECT * FROM passkey_reset_codes WHERE code_hash=? AND school_id=?",
                (str(reset_code_hash), school),
            ).fetchone()
            if (
                reset is None
                or reset["used_at"]
                or datetime.fromisoformat(reset["expires_at"]) <= now
            ):
                raise ServiceError(
                    "The passkey recovery code is invalid or expired.",
                    code="passkey_reset_invalid",
                )
            collision = connection.execute(
                "SELECT school_id FROM passkeys WHERE credential_id=?", (credential,)
            ).fetchone()
            if collision is not None and str(collision["school_id"]) != school:
                raise ServiceError("This passkey is already registered.", code="passkey_exists")
            removed = int(
                connection.execute(
                    "SELECT COUNT(*) FROM passkeys WHERE school_id=?", (school,)
                ).fetchone()[0]
            )
            connection.execute("DELETE FROM passkeys WHERE school_id=?", (school,))
            connection.execute(
                """INSERT INTO passkeys(
                    credential_id,school_id,public_key,sign_count,label,created_at
                ) VALUES(?,?,?,?,?,?)""",
                (
                    credential,
                    school,
                    bytes(credential_public_key),
                    max(0, int(sign_count)),
                    " ".join(str(label or "Recovered administrator passkey").split())[:120],
                    timestamp(now),
                ),
            )
            connection.execute(
                "UPDATE passkey_reset_codes SET used_at=? WHERE code_hash=? AND used_at IS NULL",
                (timestamp(now), str(reset_code_hash)),
            )
            self.repository._audit(
                connection,
                school,
                "",
                "administrator_passkeys_recovered",
                {"revoked_passkey_count": removed},
            )

    def revoke_passkey(self, *, school_id: str, credential_id: str) -> None:
        school = validate_school_id(school_id)
        credential = str(credential_id or "").strip()
        with self.repository.transaction() as connection:
            count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM passkeys WHERE school_id=?", (school,)
                ).fetchone()[0]
            )
            if count <= 1:
                raise ServiceError(
                    "The last administrator passkey cannot be revoked; use recovery instead.",
                    code="last_passkey",
                )
            changed = connection.execute(
                "DELETE FROM passkeys WHERE school_id=? AND credential_id=?",
                (school, credential),
            ).rowcount
            if changed != 1:
                raise ServiceError("The passkey was not found.", code="passkey_not_found")
            self.repository._audit(
                connection,
                school,
                "",
                "administrator_passkey_revoked",
                {"credential_id_sha256": hashlib.sha256(credential.encode()).hexdigest()},
            )

    def complete_initial_registration(
        self,
        *,
        school_id: str,
        school_name: str,
        installation_id: str,
        activation_code: str,
        credential_id: str,
        credential_public_key: bytes,
        sign_count: int,
        passkey_label: str = "Initial administrator passkey",
        handoff_writer: CredentialHandoffWriter | None = None,
        handoff_operation_id: str = "",
        handoff_request_fingerprint: str = "",
    ) -> RegistrationResult:
        school_id = validate_school_id(school_id)
        installation_id = validate_installation_id(installation_id)
        name = " ".join(str(school_name or "").split())[:200]
        if not name:
            raise ServiceError("School name is required.", code="invalid_school_name")
        if handoff_writer is not None and (
            not handoff_operation_id or not handoff_request_fingerprint
        ):
            raise ServiceError(
                "Credential handoff identity is incomplete.",
                code="handoff_invalid",
            )
        code_hash = hashlib.sha256(str(activation_code).strip().upper().encode("utf-8")).hexdigest()
        public_host = f"{school_id}.{self.managed_domain}"
        provisioned = self.provisioner.provision(school_id=school_id, public_host=public_host)
        tunnel_id = str(provisioned.get("tunnel_id") or "")
        tunnel_token = str(provisioned.get("tunnel_token") or "")
        if not tunnel_id or not tunnel_token:
            raise ServiceError("Tunnel provisioning returned an incomplete result.")
        registration_id = str(uuid4())
        installation_secret = secrets.token_urlsafe(48)
        now = utc_now()
        completion_code = ""
        handoff_payload = {
            "registration_id": registration_id,
            "school_id": school_id,
            "installation_id": installation_id,
            "public_host": public_host,
            "tunnel_id": tunnel_id,
            "tunnel_token": tunnel_token,
            "installation_secret": installation_secret,
        }
        try:
            with self.repository.transaction() as connection:
                directory = connection.execute(
                    "SELECT school_name,status FROM district_schools WHERE school_id=?",
                    (school_id,),
                ).fetchone()
                if directory is not None and str(directory["status"]) != DIRECTORY_APPROVED:
                    raise ServiceError(
                        "This school's Internet Gateway enrollment is suspended.",
                        code="school_suspended",
                    )
                if directory is not None:
                    # The district roster, not browser-supplied form text, is
                    # authoritative for the identity attached to this School ID.
                    name = str(directory["school_name"])
                activation = connection.execute(
                    "SELECT * FROM activation_codes WHERE code_hash=? AND school_id=?",
                    (code_hash, school_id),
                ).fetchone()
                if (
                    activation is None
                    or activation["used_at"]
                    or datetime.fromisoformat(activation["expires_at"]) <= now
                ):
                    raise ServiceError(
                        "The activation code is invalid, expired, or already used.",
                        code="activation_invalid",
                    )
                if connection.execute(
                    "SELECT 1 FROM schools WHERE school_id=?", (school_id,)
                ).fetchone():
                    raise ServiceError(
                        "This School ID is already registered. Use Transfer Existing School Server.",
                        code="school_already_registered",
                    )
                connection.execute(
                    """INSERT INTO schools(
                        school_id,school_name,public_host,registration_id,
                        active_installation_id,tunnel_id,created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?)""",
                    (
                        school_id,
                        name,
                        public_host,
                        registration_id,
                        installation_id,
                        tunnel_id,
                        timestamp(now),
                        timestamp(now),
                    ),
                )
                connection.execute(
                    """INSERT OR IGNORE INTO district_schools(
                           school_id,school_name,school_district,school_division,
                           status,created_at,updated_at
                       ) VALUES(?,?,?,?,?,?,?)""",
                    (
                        school_id,
                        name,
                        "Schools District of Motiong",
                        "Schools Division of Samar",
                        DIRECTORY_APPROVED,
                        timestamp(now),
                        timestamp(now),
                    ),
                )
                connection.execute(
                    """INSERT INTO installations(
                        installation_id,school_id,state,secret_hash,registered_at
                    ) VALUES(?,?,?,?,?)""",
                    (
                        installation_id,
                        school_id,
                        ACTIVE,
                        _hash_secret(installation_secret),
                        timestamp(now),
                    ),
                )
                connection.execute(
                    """INSERT INTO passkeys(
                        credential_id,school_id,public_key,sign_count,label,created_at
                    ) VALUES(?,?,?,?,?,?)""",
                    (
                        str(credential_id),
                        school_id,
                        bytes(credential_public_key),
                        max(0, int(sign_count)),
                        str(passkey_label)[:120],
                        timestamp(now),
                    ),
                )
                connection.execute(
                    "UPDATE activation_codes SET used_at=? WHERE code_hash=?",
                    (timestamp(now), code_hash),
                )
                self.repository._audit(
                    connection,
                    school_id,
                    installation_id,
                    "initial_registration_completed",
                    {"registration_id": registration_id, "tunnel_id": tunnel_id},
                )
                if handoff_writer is not None:
                    completion_code = handoff_writer.issue_in_transaction(
                        connection,
                        installation_id,
                        handoff_payload,
                        operation_id=handoff_operation_id,
                        request_fingerprint=handoff_request_fingerprint,
                    )
        except Exception:
            try:
                self.provisioner.revoke(tunnel_id=tunnel_id)
            except Exception:
                pass
            raise
        return RegistrationResult(
            registration_id,
            school_id,
            installation_id,
            public_host,
            tunnel_id,
            tunnel_token,
            installation_secret,
            completion_code,
        )

    def complete_transfer(
        self,
        *,
        school_id: str,
        destination_installation_id: str,
        credential_id: str,
        new_sign_count: int,
        transfer_mode: str,
        data_validation: Mapping[str, Any] | None = None,
        handoff_writer: CredentialHandoffWriter | None = None,
        handoff_operation_id: str = "",
        handoff_request_fingerprint: str = "",
    ) -> Mapping[str, Any]:
        school_id = validate_school_id(school_id)
        destination = validate_installation_id(destination_installation_id)
        transfer_mode, validation = _validated_transfer_intent(
            transfer_mode,
            data_validation if isinstance(data_validation, Mapping) else {},
        )
        if handoff_writer is not None and (
            not handoff_operation_id or not handoff_request_fingerprint
        ):
            raise ServiceError(
                "Credential handoff identity is incomplete.",
                code="handoff_invalid",
            )
        transaction_id = str(uuid4())
        installation_secret = secrets.token_urlsafe(48)
        now = utc_now()
        source = ""
        source_tunnel_id = ""
        replacement_tunnel_id = ""
        tunnel_token = ""
        public_host = ""
        school: Mapping[str, Any] | None = None
        result: dict[str, Any] = {}
        try:
            # BEGIN IMMEDIATE serializes the one-active-server cutover.  The
            # external route change is compensated before this transaction is
            # allowed to roll back.
            with self.repository.transaction() as connection:
                school = connection.execute(
                    "SELECT * FROM schools WHERE school_id=?", (school_id,)
                ).fetchone()
                if school is None or not school["active_installation_id"]:
                    raise ServiceError("The registered school has no active server.", code="school_not_active")
                source = str(school["active_installation_id"])
                source_tunnel_id = str(school["tunnel_id"])
                public_host = str(school["public_host"])
                if source == destination:
                    raise ServiceError("The destination is already the active school server.")
                passkey = connection.execute(
                    "SELECT * FROM passkeys WHERE credential_id=? AND school_id=?",
                    (str(credential_id), school_id),
                ).fetchone()
                if passkey is None:
                    raise ServiceError("The registered passkey was not found.", code="passkey_not_found")
                provisioned = self.provisioner.rotate(
                    tunnel_id=source_tunnel_id,
                    school_id=school_id,
                    public_host=public_host,
                )
                tunnel_token = str(provisioned.get("tunnel_token") or "")
                replacement_tunnel_id = str(provisioned.get("tunnel_id") or "")
                if not tunnel_token or not replacement_tunnel_id:
                    raise ServiceError("Tunnel credential rotation failed.")
                connection.execute(
                """UPDATE installations SET state='transferred',retired_at=?,
                    retirement_transaction_id=? WHERE installation_id=? AND state='active'""",
                (timestamp(now), transaction_id, source),
            )
                connection.execute(
                """INSERT INTO installations(
                    installation_id,school_id,state,secret_hash,registered_at
                ) VALUES(?,?,?,?,?)""",
                (destination, school_id, ACTIVE, _hash_secret(installation_secret), timestamp(now)),
            )
                connection.execute(
                """UPDATE schools SET active_installation_id=?,tunnel_id=?,updated_at=?
                   WHERE school_id=?""",
                (destination, replacement_tunnel_id, timestamp(now), school_id),
            )
                connection.execute(
                "UPDATE passkeys SET sign_count=?,last_used_at=? WHERE credential_id=?",
                (max(int(new_sign_count), int(passkey["sign_count"])), timestamp(now), str(credential_id)),
            )
                connection.execute(
                """INSERT INTO transfers(
                    transaction_id,school_id,source_installation_id,destination_installation_id,
                    transfer_mode,status,data_validation_json,created_at,completed_at
                ) VALUES(?,?,?,?,?,'completed',?,?,?)""",
                (
                    transaction_id,
                    school_id,
                    source,
                    destination,
                    transfer_mode,
                    json.dumps(validation, sort_keys=True, separators=(",", ":")),
                    timestamp(now),
                    timestamp(now),
                ),
            )
                self.repository._audit(
                    connection,
                    school_id,
                    destination,
                    "authorization_transferred",
                    {
                        "transaction_id": transaction_id,
                        "source_installation_id": source,
                        "destination_installation_id": destination,
                        "transfer_mode": transfer_mode,
                    },
                )
                result = {
                    "transaction_id": transaction_id,
                    "school_id": school_id,
                    "registration_id": str(school["registration_id"]),
                    "source_installation_id": source,
                    "destination_installation_id": destination,
                    "public_host": public_host,
                    "tunnel_id": replacement_tunnel_id,
                    "tunnel_token": tunnel_token,
                    "installation_secret": installation_secret,
                    "authorization_state": ACTIVE,
                }
                if handoff_writer is not None:
                    result["completion_code"] = handoff_writer.issue_in_transaction(
                        connection,
                        destination,
                        result,
                        operation_id=handoff_operation_id,
                        request_fingerprint=handoff_request_fingerprint,
                    )
                else:
                    result["completion_code"] = ""
        except Exception:
            if replacement_tunnel_id:
                rollback = getattr(self.provisioner, "rollback_rotation", None)
                try:
                    if callable(rollback):
                        rollback(
                            source_tunnel_id=source_tunnel_id,
                            replacement_tunnel_id=replacement_tunnel_id,
                            public_host=public_host,
                        )
                    else:
                        self.provisioner.revoke(tunnel_id=replacement_tunnel_id)
                except Exception:
                    pass
            raise

        # The permanent DNS route is already on the replacement tunnel and the
        # database cutover is durable.  Revoking the source connector now avoids
        # an unrecoverable old-server outage if the database commit fails.
        try:
            self.provisioner.revoke(tunnel_id=source_tunnel_id)
        except Exception:
            with self.repository.transaction() as connection:
                self.repository._audit(
                    connection,
                    school_id,
                    source,
                    "source_tunnel_revocation_pending",
                    {"transaction_id": transaction_id, "tunnel_id": source_tunnel_id},
                )
        assert school is not None and result
        return result

    def authenticate_installation(
        self, installation_id: str, installation_secret: str
    ) -> Mapping[str, Any]:
        installation_id = validate_installation_id(installation_id)
        with self.repository.read() as connection:
            row = connection.execute(
                """SELECT i.*,s.registration_id,s.public_host,s.tunnel_id
                   FROM installations i JOIN schools s ON s.school_id=i.school_id
                   WHERE i.installation_id=?""",
                (installation_id,),
            ).fetchone()
        if row is None or not _verify_secret(installation_secret, str(row["secret_hash"])):
            raise ServiceError("Installation authorization failed.", code="authorization_failed")
        return dict(row)

    def signed_authorization(
        self, installation_id: str, installation_secret: str
    ) -> Mapping[str, Any]:
        if self.signer is None:
            raise ServiceError("Authorization signing is not configured.", code="signer_unavailable")
        row = self.authenticate_installation(installation_id, installation_secret)
        now = utc_now()
        payload = {
            "school_id": str(row["school_id"]),
            "installation_id": str(row["installation_id"]),
            "registration_id": str(row["registration_id"]),
            "authorization_state": str(row["state"]),
            "public_host": str(row["public_host"]),
            "tunnel_id": str(row["tunnel_id"]),
            "transaction_id": str(row["retirement_transaction_id"] or ""),
            "issued_at": timestamp(now),
            "expires_at": timestamp(now + timedelta(hours=24)),
        }
        return self.signer.sign(payload)
