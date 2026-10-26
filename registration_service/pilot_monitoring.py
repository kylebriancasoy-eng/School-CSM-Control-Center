"""Explicit, activation-approved monitoring of an existing direct pilot.

This grants no tunnel, transfer, passkey or managed-installation authority.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import hmac
import json
from typing import Any, Mapping

from .core import ServiceError, timestamp, utc_now, validate_school_id, validate_installation_id


def initialize(connection) -> None:
    connection.execute("""CREATE TABLE IF NOT EXISTS pilot_monitors (
        school_id TEXT PRIMARY KEY REFERENCES district_schools(school_id),
        installation_id TEXT NOT NULL UNIQUE,
        secret_hash TEXT NOT NULL,
        activation_hash TEXT NOT NULL,
        public_host TEXT NOT NULL,
        registered_at TEXT NOT NULL,
        received_at TEXT,
        payload_json TEXT NOT NULL DEFAULT '{}'
    )""")


def link(service, *, school_id: str, installation_id: str, public_host: str,
         activation_code: str, monitoring_secret: str) -> Mapping[str, Any]:
    school = validate_school_id(school_id)
    installation = validate_installation_id(installation_id)
    host = str(public_host).strip().casefold()
    if host != f"{school}.{service.managed_domain}":
        raise ServiceError("The pilot hostname does not match this district and School ID.", code="invalid_pilot_link")
    if len(monitoring_secret) != 64 or any(c not in "0123456789abcdef" for c in monitoring_secret):
        raise ServiceError("A device-generated monitoring secret is required.", code="invalid_pilot_link")
    digest = hashlib.sha256(monitoring_secret.encode()).hexdigest()
    activation = hashlib.sha256(str(activation_code).strip().upper().encode()).hexdigest()
    with service.repository.transaction() as connection:
        directory = connection.execute("SELECT status FROM district_schools WHERE school_id=?", (school,)).fetchone()
        if directory is None or directory["status"] != "approved":
            raise ServiceError("The district must approve this school first.", code="school_not_active")
        if connection.execute("SELECT 1 FROM schools WHERE school_id=?", (school,)).fetchone():
            raise ServiceError("This school already has managed registration; use its existing connection.", code="school_already_registered")
        existing = connection.execute("SELECT * FROM pilot_monitors WHERE school_id=?", (school,)).fetchone()
        other_device = connection.execute("SELECT school_id FROM pilot_monitors WHERE installation_id=?", (installation,)).fetchone()
        if other_device is not None and other_device["school_id"] != school:
            raise ServiceError("This monitoring device is already linked.", code="pilot_already_linked")
        if existing is not None:
            if (existing["installation_id"] == installation and existing["public_host"] == host
                    and hmac.compare_digest(existing["secret_hash"], digest)
                    and hmac.compare_digest(existing["activation_hash"], activation)):
                return {"linked": True, "school_id": school, "public_host": host, "mode": "monitoring_only"}
            raise ServiceError("A different pilot device is already linked. Contact the district administrator.", code="pilot_already_linked")
        code = connection.execute("SELECT * FROM activation_codes WHERE code_hash=? AND school_id=?", (activation, school)).fetchone()
        if code is None or code["used_at"] or datetime.fromisoformat(code["expires_at"]) <= utc_now():
            raise ServiceError("The district activation code is invalid, expired, or already used.", code="activation_invalid")
        now = timestamp()
        connection.execute("INSERT INTO pilot_monitors(school_id,installation_id,secret_hash,activation_hash,public_host,registered_at) VALUES(?,?,?,?,?,?)",
                           (school, installation, digest, activation, host, now))
        connection.execute("UPDATE activation_codes SET used_at=? WHERE code_hash=?", (now, activation))
        service.repository._audit(connection, event_type="pilot_monitor_linked", school_id=school,
                                  installation_id=installation, detail={"mode": "monitoring_only"})
    return {"linked": True, "school_id": school, "public_host": host, "mode": "monitoring_only"}


def heartbeat(service, *, installation_id: str, secret: str, payload: Mapping[str, Any]):
    installation = validate_installation_id(installation_id)
    version = str(payload.get("application_version") or "")
    allowed = {"local_server_state": {"running", "stopped", "starting", "stopping", "error"},
               "gateway_state": {"connected", "disconnected", "connecting", "error", "not_configured"},
               "survey_status": {"online", "offline", "maintenance"},
               "error_code": {"", "authorization_offline", "gateway_error", "local_server_error", "tunnel_start_failed"}}
    clean = {key: str(payload.get(key) or "") for key in allowed}
    if not version or len(version) > 40 or any(c.isspace() for c in version) or any(clean[k] not in values for k, values in allowed.items()):
        raise ServiceError("Pilot status report is invalid.", code="invalid_heartbeat")
    clean["application_version"] = version
    with service.repository.transaction() as connection:
        row = connection.execute("SELECT p.*,d.status FROM pilot_monitors p JOIN district_schools d USING(school_id) WHERE p.installation_id=?", (installation,)).fetchone()
        if (row is None or row["status"] != "approved" or not secret
                or not hmac.compare_digest(row["secret_hash"], hashlib.sha256(secret.encode()).hexdigest())
                or connection.execute("SELECT 1 FROM schools WHERE school_id=?", (row["school_id"],)).fetchone()):
            raise ServiceError("Pilot monitoring authorization failed.", code="authorization_failed")
        received = timestamp()
        connection.execute("UPDATE pilot_monitors SET received_at=?,payload_json=? WHERE installation_id=?",
                           (received, json.dumps(clean), installation))
    return {"received_at": received, "mode": "monitoring_only"}


def unlink(service, school_id: str):
    school = validate_school_id(school_id)
    with service.repository.transaction() as connection:
        row = connection.execute("SELECT installation_id FROM pilot_monitors WHERE school_id=?", (school,)).fetchone()
        if row is None:
            raise ServiceError("No existing pilot monitoring link was found.", code="school_not_found")
        connection.execute("DELETE FROM pilot_monitors WHERE school_id=?", (school,))
        connection.execute("UPDATE activation_codes SET used_at=? WHERE school_id=? AND used_at IS NULL", (timestamp(), school))
        service.repository._audit(connection, school, row["installation_id"], "pilot_monitor_unlinked", {"mode": "monitoring_only"})
    return {"unlinked": True, "school_id": school}


def overlay(service, documents):
    """Reuse the existing health classifier, without creating managed authority."""
    if not documents:
        return documents
    ids = [document["school_id"] for document in documents]
    with service.repository.read() as connection:
        rows = {row["school_id"]: dict(row) for row in connection.execute(
            "SELECT * FROM pilot_monitors WHERE school_id IN (" + ",".join("?" for _ in ids) + ")", ids)}
    for document in documents:
        monitor = rows.get(document["school_id"])
        if monitor is None or document["registration_state"] == "registered":
            continue
        payload = json.loads(monitor["payload_json"])
        classified = service._district_school_document({
            **document, **payload, "status": document["directory_status"],
            "public_host": monitor["public_host"], "received_at": monitor["received_at"],
            "registered_at": monitor["registered_at"],
        })
        document.update(classified, monitoring_mode="external_pilot",
                        monitoring_installation_id=monitor["installation_id"])
    return documents
