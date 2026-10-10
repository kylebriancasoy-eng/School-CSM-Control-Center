"""Untrusted school identity submissions; approval never grants gateway access."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from typing import Any, Mapping

from .core import RegistrationRepository, ServiceError, timestamp, validate_installation_id


REQUEST_FIELDS = frozenset({
    "request_id", "request_secret", "installation_id", "school_id", "school_name",
    "school_district", "school_division",
})
PUBLIC_FIELDS = (
    "request_id", "installation_id", "school_id", "school_name", "school_district",
    "school_division", "status", "created_at", "updated_at", "reviewed_at",
)
MAX_PENDING_REQUESTS = 1000
MAX_PENDING_PER_SCHOOL = 5


def clean_identity(value: Any, label: str, maximum: int) -> str:
    if not isinstance(value, str) or len(value) > maximum or any(
        ord(character) < 32 or ord(character) == 127 for character in value
    ):
        raise ServiceError(f"{label} is invalid.", code="invalid_directory_request")
    text = " ".join(value.split())
    if not text:
        raise ServiceError(f"{label} is required.", code="invalid_directory_request")
    return text


class DirectoryRegistrationRequests:
    def __init__(self, repository: RegistrationRepository) -> None:
        self.repository = repository

    @staticmethod
    def _receipt(row: Mapping[str, Any]) -> dict[str, str]:
        return {key: str(row[key]) for key in ("request_id", "school_id", "status")}

    @staticmethod
    def _revision(row: Mapping[str, Any]) -> str:
        # Bind the review to exactly the identity displayed, not a seconds-
        # precision timestamp. Pending details may change between listing and review.
        identity = [str(row[key]) for key in (
            "request_id", "installation_id", "school_id", "school_name",
            "school_district", "school_division",
        )]
        return hashlib.sha256(json.dumps(identity, ensure_ascii=True,
            separators=(",", ":")).encode("ascii")).hexdigest()

    def submit(self, payload: Mapping[str, Any]) -> dict[str, str]:
        if set(payload) != REQUEST_FIELDS:
            raise ServiceError("Registration request fields are invalid.", code="invalid_directory_request")
        request_id = validate_installation_id(payload["request_id"])
        installation_id = validate_installation_id(payload["installation_id"])
        school_id = str(payload["school_id"])
        if not re.fullmatch(r"[0-9]{4,12}", school_id):
            raise ServiceError("School ID must contain 4 to 12 digits.", code="invalid_school_id")
        secret = str(payload["request_secret"])
        if not re.fullmatch(r"[0-9a-f]{64}", secret):
            raise ServiceError("Registration receipt is invalid.", code="invalid_directory_request")
        digest = hashlib.sha256(secret.encode("ascii")).hexdigest()
        values = tuple(clean_identity(payload[key], label, maximum) for key, label, maximum in (
            ("school_name", "School name", 200),
            ("school_district", "School district", 160),
            ("school_division", "School division", 160),
        ))
        now = timestamp()
        with self.repository.transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM school_registration_requests WHERE request_id=?", (request_id,)
            ).fetchone()
            if existing is not None:
                if not hmac.compare_digest(digest, existing["request_secret_sha256"]) or (
                    existing["installation_id"], existing["school_id"]
                ) != (installation_id, school_id):
                    raise ServiceError("Registration receipt could not be verified.", code="authorization_failed")
                # Review decisions and authoritative directory entries cannot be
                # changed through this public endpoint, even with a receipt.
                old_values = tuple(existing[key] for key in (
                    "school_name", "school_district", "school_division"
                ))
                if existing["status"] == "pending" and old_values != values:
                    connection.execute(
                        """UPDATE school_registration_requests SET school_name=?,
                           school_district=?,school_division=?,updated_at=? WHERE request_id=?""",
                        (*values, now, request_id),
                    )
                    self.repository._audit(connection, school_id, installation_id,
                        "school_registration_request_updated", {"request_id": request_id})
                return self._receipt(existing)
            total = connection.execute(
                "SELECT COUNT(*) FROM school_registration_requests WHERE status='pending'"
            ).fetchone()[0]
            school_total = connection.execute(
                "SELECT COUNT(*) FROM school_registration_requests WHERE school_id=? AND status='pending'",
                (school_id,),
            ).fetchone()[0]
            if total >= MAX_PENDING_REQUESTS or school_total >= MAX_PENDING_PER_SCHOOL:
                raise ServiceError("The review queue is full. Contact the district administrator.",
                    code="directory_queue_full")
            connection.execute(
                """INSERT INTO school_registration_requests(
                    request_id,request_secret_sha256,installation_id,school_id,
                    school_name,school_district,school_division,status,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,'pending',?,?)""",
                (request_id, digest, installation_id, school_id, *values, now, now),
            )
            self.repository._audit(connection, school_id, installation_id,
                "school_registration_request_submitted", {"request_id": request_id})
        return {"request_id": request_id, "school_id": school_id, "status": "pending"}

    def list(self, status: str = "pending") -> list[dict[str, Any]]:
        if status not in {"all", "pending", "approved", "rejected"}:
            raise ServiceError("Request filter is invalid.", code="invalid_directory_request")
        clause, parameters = ("", ()) if status == "all" else ("WHERE status=?", (status,))
        with self.repository.read() as connection:
            rows = connection.execute(
                f"""SELECT * FROM school_registration_requests {clause}
                    ORDER BY CASE WHEN status='pending' THEN 0 ELSE 1 END,
                    created_at DESC,request_id LIMIT 1000""",
                parameters,
            ).fetchall()
        return [{**{key: row[key] for key in PUBLIC_FIELDS}, "revision": self._revision(row)} for row in rows]

    def counts(self) -> dict[str, int]:
        with self.repository.read() as connection:
            rows = connection.execute(
                "SELECT status,COUNT(*) AS count FROM school_registration_requests GROUP BY status"
            ).fetchall()
        counts = {"pending": 0, "approved": 0, "rejected": 0}
        counts.update({row["status"]: row["count"] for row in rows})
        return counts

    def review(self, request_id: str, decision: str, *, expected_revision: str) -> dict[str, str]:
        request_id = validate_installation_id(request_id)
        if decision not in {"approve", "reject"}:
            raise ServiceError("Review decision is invalid.", code="invalid_directory_request")
        if not isinstance(expected_revision, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_revision):
            raise ServiceError("The review revision is invalid.", code="invalid_directory_request")
        status = "approved" if decision == "approve" else "rejected"
        with self.repository.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM school_registration_requests WHERE request_id=?", (request_id,)
            ).fetchone()
            if row is None:
                raise ServiceError("Registration request was not found.", code="school_not_found")
            if not hmac.compare_digest(expected_revision, self._revision(row)):
                raise ServiceError("The school's details changed after you opened this review. Close this window, refresh, and verify the updated details before reviewing again.",
                    code="directory_request_stale")
            if row["status"] != "pending":
                if row["status"] == status:
                    return self._receipt(row)
                raise ServiceError("This request has already been reviewed.", code="directory_request_reviewed")
            now = timestamp()
            if decision == "approve":
                directory = connection.execute(
                    "SELECT * FROM district_schools WHERE school_id=?", (row["school_id"],)
                ).fetchone()
                if directory is not None:
                    if directory["status"] != "approved":
                        raise ServiceError("This school's enrollment is suspended.", code="school_suspended")
                    keys = ("school_name", "school_district", "school_division")
                    if any(str(directory[key]).casefold() != str(row[key]).casefold() for key in keys):
                        raise ServiceError(
                            "These details conflict with the official roster. Reject this request and ask the school to correct its details.",
                            code="directory_identity_conflict",
                        )
                else:
                    connection.execute(
                        """INSERT INTO district_schools(school_id,school_name,school_district,
                            school_division,status,created_at,updated_at) VALUES(?,?,?,?,'approved',?,?)""",
                        (row["school_id"], row["school_name"], row["school_district"],
                         row["school_division"], now, now),
                    )
                    self.repository._audit(connection, row["school_id"], "",
                        "district_school_saved", {"status": "approved", "source": "reviewed_request"})
            connection.execute(
                "UPDATE school_registration_requests SET status=?,reviewed_at=?,updated_at=? WHERE request_id=?",
                (status, now, now, request_id),
            )
            self.repository._audit(connection, row["school_id"], row["installation_id"],
                "school_registration_request_reviewed", {"request_id": request_id, "status": status})
        return {"request_id": request_id, "school_id": row["school_id"], "status": status}
