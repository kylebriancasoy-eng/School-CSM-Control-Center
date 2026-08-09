"""Durable Dashboard print-audit reservations and lifecycle transitions."""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
from enum import Enum
import json
from pathlib import Path
import re
from typing import Any, Mapping
from uuid import UUID, uuid4

from school_csm_control_center.runtime_paths import storage_root_for
from school_csm_control_center.storage.file_safety import (
    InterProcessRLock,
    atomic_write_json,
    preserve_corrupt_file,
)


class PrintHistoryStoreError(RuntimeError):
    """Raised when Dashboard print history cannot be saved safely."""


class PrintHistoryStore:
    """Reserve audit identities before preview and retain every terminal outcome."""

    FILE_TYPE = "School CSM Control Center Dashboard Print History"
    SCHEMA_VERSION = "3.0"
    CONTROL_PREFIX = "CSMS-PRN"
    STATUSES = {
        "reserved",
        "submitting",
        "submitted",
        "confirmed",
        "failed",
        "cancelled",
        "interrupted",
    }
    TERMINAL_STATUSES = {"confirmed", "failed", "cancelled", "interrupted"}
    SUCCESS_STATUSES = {"submitted", "confirmed"}
    ALLOWED_TRANSITIONS = {
        "reserved": {"submitting", "submitted", "cancelled", "failed", "interrupted"},
        "submitting": {"submitted", "failed", "cancelled", "interrupted"},
        "submitted": {"confirmed", "failed"},
        "confirmed": set(),
        "failed": set(),
        "cancelled": set(),
        "interrupted": set(),
    }

    def __init__(
        self,
        project_root: str | Path,
        path: str | Path | None = None,
        *,
        data_root: str | Path | None = None,
    ) -> None:
        self.install_root = Path(project_root)
        self.project_root = storage_root_for(project_root, data_root)
        self.path = (
            Path(path)
            if path is not None
            else self.project_root
            / "data"
            / "csm_survey"
            / "print_history.mossjson"
        )
        self._lock = InterProcessRLock(self.path)
        self.last_read_error: str | None = None
        self.last_recovery_copy: Path | None = None

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            records = [
                deepcopy(record)
                for record in self._read_document()["records"]
                if isinstance(record, dict)
            ]
        records.sort(key=_record_sort_key, reverse=True)
        return records

    def list_all(self) -> list[dict[str, Any]]:
        return self.list()

    @classmethod
    def narrative_eligibility(
        cls, record: Mapping[str, Any] | None
    ) -> tuple[bool, str]:
        """Return whether a print can be reprinted or used for a narrative.

        Build 11 records remain readable after migration, but a historical row
        without a durable snapshot must never be reconstructed from live data.
        ``submitted`` is the current accepted-by-spooler success state, while
        ``confirmed`` covers legacy and explicitly confirmed successful rows.
        """

        if not isinstance(record, Mapping):
            return False, "Dashboard print record is unavailable."
        status = str(record.get("status") or "confirmed").strip().casefold()
        if status not in cls.SUCCESS_STATUSES:
            return False, f"Dashboard print status is {status or 'unavailable'}."
        snapshot = record.get("dashboard_snapshot")
        if not isinstance(snapshot, Mapping):
            return False, "Snapshot unavailable for this legacy print record."
        if not _clean_text(snapshot.get("snapshot_id")):
            return False, "Snapshot identity is unavailable."
        manifest_path = _clean_text(snapshot.get("manifest_path"))
        if not manifest_path:
            return False, "Snapshot manifest is unavailable."
        parsed_path = Path(manifest_path)
        if parsed_path.is_absolute() or ".." in parsed_path.parts:
            return False, "Snapshot manifest path is unsafe."
        digest = _clean_text(snapshot.get("digest_sha256")).casefold()
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            return False, "Snapshot integrity information is unavailable."
        return True, "Snapshot available."

    def get(self, record_id_or_control: str) -> dict[str, Any] | None:
        wanted = str(record_id_or_control or "").strip()
        if not wanted:
            return None
        return next(
            (
                record
                for record in self.list()
                if str(record.get("id") or "") == wanted
                or str(record.get("control_number") or "").casefold() == wanted.casefold()
            ),
            None,
        )

    def next_control_number(self, generated_at: Any = None) -> str:
        """Calculate the next number without reserving it.

        New print-preview code should call :meth:`reserve` instead.
        """

        timestamp = _normalize_timestamp(generated_at)
        with self._lock:
            return self._next_control_number(
                self._read_document()["records"], timestamp[:7]
            )

    def reserve(
        self,
        record: Mapping[str, Any] | None = None,
        *,
        generated_at: Any = None,
    ) -> dict[str, Any]:
        """Durably reserve the number and barcode used by a print preview."""

        supplied = dict(record or {})
        timestamp = _normalize_timestamp(
            generated_at if generated_at is not None else supplied.get("generated_at")
        )
        with self._lock:
            document = self._read_document()
            self._raise_if_corrupt()
            control_number = self._next_control_number(
                document["records"], timestamp[:7]
            )
            now = _local_now()
            reserved: dict[str, Any] = {
                "id": str(uuid4()),
                "control_number": control_number,
                "barcode_value": control_number,
                "generated_at": timestamp,
                "created_at": now,
                "updated_at": now,
                "status": "reserved",
                "status_message": _clean_text(supplied.get("status_message")),
                "status_history": [
                    {
                        "status": "reserved",
                        "at": now,
                        "message": _clean_text(supplied.get("status_message")),
                    }
                ],
                "scope": _clean_text(supplied.get("scope"))
                or "Complete current Dashboard",
            }
            if supplied.get("survey_count") is not None:
                reserved["survey_count"] = _nonnegative_int(
                    supplied.get("survey_count"), "Survey count"
                )
            for key in ("filter_summary", "report_title", "requested_by"):
                if supplied.get(key) not in (None, ""):
                    reserved[key] = _json_value(supplied.get(key))
            document["records"].append(reserved)
            self._write_document(document)
            return deepcopy(reserved)

    reserve_print = reserve

    def transition_status(
        self,
        record_id_or_control: str,
        status: str,
        updates: Mapping[str, Any] | None = None,
        *,
        message: str = "",
    ) -> dict[str, Any]:
        """Apply one validated lifecycle transition and retain its audit trail."""

        target_status = str(status or "").strip().casefold()
        if target_status not in self.STATUSES:
            raise PrintHistoryStoreError(
                f"Unsupported print-audit status: {target_status or 'blank'}"
            )
        with self._lock:
            document = self._read_document()
            self._raise_if_corrupt()
            record = self._find_record(document, record_id_or_control)
            current = str(record.get("status") or "confirmed").casefold()
            if current == target_status:
                return deepcopy(record)
            if target_status not in self.ALLOWED_TRANSITIONS.get(current, set()):
                raise PrintHistoryStoreError(
                    f"Print audit cannot change from {current} to {target_status}."
                )

            changes = dict(updates or {})
            if target_status == "submitted":
                normalized = self._normalize_success(
                    {
                        **record,
                        **changes,
                        "control_number": record["control_number"],
                        "barcode_value": record["control_number"],
                    },
                    control_number=str(record["control_number"]),
                    generated_at=str(record["generated_at"]),
                )
                record.update(normalized)
                record["submitted_at"] = _normalize_timestamp(
                    changes.get("submitted_at")
                    or changes.get("printed_at")
                    or None
                )
            else:
                self._merge_audit_updates(record, changes)

            now = _local_now()
            record["status"] = target_status
            record["updated_at"] = now
            status_message = _clean_text(
                message
                or changes.get("status_message")
                or changes.get("error")
                or changes.get("reason")
            )
            record["status_message"] = status_message
            history = record.get("status_history")
            if not isinstance(history, list):
                history = []
                record["status_history"] = history
            history.append(
                {"status": target_status, "at": now, "message": status_message}
            )
            timestamp_key = {
                "submitting": "submitting_at",
                "confirmed": "confirmed_at",
                "failed": "failed_at",
                "cancelled": "cancelled_at",
                "interrupted": "interrupted_at",
            }.get(target_status)
            if timestamp_key:
                record[timestamp_key] = now
            self._write_document(document)
            return deepcopy(record)

    def mark_submitting(
        self,
        record_id_or_control: str,
        updates: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.transition_status(record_id_or_control, "submitting", updates)

    def mark_submitted(
        self,
        record_id_or_control: str,
        record: Mapping[str, Any],
    ) -> dict[str, Any]:
        return self.transition_status(record_id_or_control, "submitted", record)

    def mark_confirmed(
        self,
        record_id_or_control: str,
        updates: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.transition_status(record_id_or_control, "confirmed", updates)

    def record_reprint_attempt(
        self,
        record_id_or_control: str,
        outcome: str,
        details: Mapping[str, Any] | None = None,
        *,
        message: str = "",
        expected_snapshot_reference: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Append one immutable reprint audit to the original print record.

        A reprint deliberately keeps the original Dashboard control number and
        therefore never consumes a new print-history sequence number.
        """

        normalized_outcome = str(outcome or "").strip().casefold()
        if normalized_outcome not in {"submitted", "failed", "cancelled"}:
            raise PrintHistoryStoreError(
                "A reprint outcome must be submitted, failed, or cancelled."
            )
        with self._lock:
            document = self._read_document()
            self._raise_if_corrupt()
            record = self._find_record(document, record_id_or_control)
            eligible, reason = self.narrative_eligibility(record)
            if not eligible:
                raise PrintHistoryStoreError(
                    f"This Dashboard print cannot be reprinted: {reason}"
                )
            if expected_snapshot_reference is not None:
                expected_reference = _normalize_snapshot_reference(
                    expected_snapshot_reference
                )
                current_reference = _normalize_snapshot_reference(
                    record.get("dashboard_snapshot")
                )
                identity_keys = ("snapshot_id", "manifest_path", "digest_sha256")
                if any(
                    str(current_reference.get(key) or "").casefold()
                    != str(expected_reference.get(key) or "").casefold()
                    for key in identity_keys
                ):
                    raise PrintHistoryStoreError(
                        "The Dashboard snapshot changed during the reprint; no "
                        "successful reprint audit was recorded."
                    )
            now = _local_now()
            supplied = dict(details or {})
            attempt = {
                "id": str(uuid4()),
                "original_control_number": str(record.get("control_number") or ""),
                "attempted_at": now,
                "outcome": normalized_outcome,
                "message": _clean_text(message or supplied.pop("message", "")),
                "details": _json_value(supplied),
            }
            attempts = record.get("reprint_attempts")
            if not isinstance(attempts, list):
                attempts = []
                record["reprint_attempts"] = attempts
            attempts.append(attempt)
            record["updated_at"] = now
            self._write_document(document)
            return deepcopy(attempt)

    def mark_failed(
        self,
        record_id_or_control: str,
        error: str,
        updates: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        changes = dict(updates or {})
        changes["error"] = str(error or "")
        return self.transition_status(
            record_id_or_control, "failed", changes, message=str(error or "")
        )

    def mark_cancelled(
        self,
        record_id_or_control: str,
        reason: str = "",
    ) -> dict[str, Any]:
        return self.transition_status(
            record_id_or_control,
            "cancelled",
            {"reason": reason},
            message=reason,
        )

    def interrupt_incomplete(self, message: str = "Interrupted by application restart.") -> list[dict[str, Any]]:
        """Mark reservations that cannot safely be resumed after a restart."""

        interrupted: list[dict[str, Any]] = []
        with self._lock:
            document = self._read_document()
            self._raise_if_corrupt()
            changed = False
            now = _local_now()
            for record in document["records"]:
                status = str(record.get("status") or "confirmed").casefold()
                if status not in {"reserved", "submitting"}:
                    continue
                record["status"] = "interrupted"
                record["status_message"] = _clean_text(message)
                record["interrupted_at"] = now
                record["updated_at"] = now
                history = record.setdefault("status_history", [])
                if isinstance(history, list):
                    history.append(
                        {
                            "status": "interrupted",
                            "at": now,
                            "message": _clean_text(message),
                        }
                    )
                interrupted.append(deepcopy(record))
                changed = True
            if changed:
                self._write_document(document)
        return interrupted

    def record_success(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """Backward-compatible accepted-by-spooler recording.

        If the preview already reserved its control number, that audit advances
        to ``submitted``. Legacy callers without a reservation atomically append
        a submitted record after checking the next number.
        """

        if not isinstance(record, Mapping):
            raise TypeError("A successful print record must be a mapping.")
        with self._lock:
            document = self._read_document()
            self._raise_if_corrupt()
            generated_at = _normalize_timestamp(record.get("generated_at"))
            printed_control = _clean_text(record.get("control_number")).upper()
            if not printed_control:
                raise PrintHistoryStoreError(
                    "The control number printed on the Dashboard is required before its history can be saved."
                )
            existing = next(
                (
                    row
                    for row in document["records"]
                    if str(row.get("control_number") or "").upper() == printed_control
                ),
                None,
            )
            if existing is not None:
                current_status = str(
                    existing.get("status") or "confirmed"
                ).strip().casefold()
                if current_status in self.SUCCESS_STATUSES:
                    if record.get("generated_at") not in (None, "") and str(
                        existing.get("generated_at") or ""
                    ) != generated_at:
                        raise PrintHistoryStoreError(
                            "That successful Dashboard control number already has "
                            "different print evidence. Print history was left unchanged."
                        )
                    normalized = self._normalize_success(
                        {**existing, **record},
                        control_number=printed_control,
                        generated_at=str(existing.get("generated_at") or generated_at),
                    )
                    conflicts = [
                        key
                        for key, value in normalized.items()
                        if key in existing and existing.get(key) != value
                    ]
                    if conflicts:
                        raise PrintHistoryStoreError(
                            "That successful Dashboard control number already has "
                            "different print evidence. Print history was left unchanged."
                        )
                    return deepcopy(existing)
                # Release and reuse the public transition logic; the lock is
                # process-reentrant and remains cross-process exclusive.
                return self.transition_status(
                    str(existing.get("id") or printed_control),
                    "submitted",
                    record,
                )

            expected_control = self._next_control_number(
                document["records"], generated_at[:7]
            )
            if printed_control != expected_control:
                raise PrintHistoryStoreError(
                    f"Printed control number {printed_control} cannot be recorded because "
                    f"the next available number is {expected_control}. Print history was left unchanged."
                )
            normalized = self._normalize_success(
                record,
                control_number=printed_control,
                generated_at=generated_at,
            )
            now = _local_now()
            normalized.update(
                {
                    "id": str(uuid4()),
                    "created_at": now,
                    "updated_at": now,
                    "submitted_at": _normalize_timestamp(
                        record.get("printed_at") or None
                    ),
                    "status": "submitted",
                    "status_message": "",
                    "status_history": [
                        {
                            "status": "submitted",
                            "at": now,
                            "message": "Recorded by compatibility print workflow.",
                        }
                    ],
                }
            )
            document["records"].append(normalized)
            self._write_document(document)
            return deepcopy(normalized)

    def add(self, record: Mapping[str, Any]) -> dict[str, Any]:
        return self.record_success(record)

    def _normalize_success(
        self,
        record: Mapping[str, Any],
        *,
        control_number: str,
        generated_at: str,
    ) -> dict[str, Any]:
        settings_value = record.get("settings") or {}
        if not isinstance(settings_value, Mapping):
            raise ValueError("Print settings must be a mapping.")
        printer_name = _clean_text(
            record.get("printer_name")
            or record.get("printer")
            or settings_value.get("printer_name")
        )
        if not printer_name:
            raise ValueError("A successful print requires a printer name.")
        page_count = _positive_int(record.get("page_count"), "Page count")
        scope = _clean_text(record.get("scope")) or "Complete current Dashboard"
        settings = _json_value(settings_value)
        if not isinstance(settings, dict):
            settings = {}
        settings.setdefault("printer_name", printer_name)
        barcode_value = _clean_text(
            record.get("barcode_value") or record.get("barcode_payload")
        ) or control_number
        if barcode_value.upper() != control_number:
            raise ValueError("The barcode value must match the printed control number.")
        normalized: dict[str, Any] = {
            "control_number": control_number,
            "generated_at": generated_at,
            "printed_at": _normalize_timestamp(record.get("printed_at")),
            "scope": scope,
            "page_count": page_count,
            "printer_name": printer_name,
            "settings": settings,
            "barcode_value": control_number,
            "copies": _positive_int(
                record.get("copies", settings.get("copies", 1)),
                "Copies",
            ),
        }
        if "selected_pages" in record:
            normalized["selected_pages"] = _normalize_selected_pages(
                record.get("selected_pages")
            )
        elif "selected_pages" in settings:
            normalized["selected_pages"] = _normalize_selected_pages(
                settings.get("selected_pages")
            )
        if record.get("survey_count") is not None:
            normalized["survey_count"] = _nonnegative_int(
                record.get("survey_count"), "Survey count"
            )
        snapshot = record.get("dashboard_snapshot")
        if snapshot is not None:
            normalized["dashboard_snapshot"] = _normalize_snapshot_reference(snapshot)
        return normalized

    def _merge_audit_updates(
        self,
        record: dict[str, Any],
        updates: Mapping[str, Any],
    ) -> None:
        allowed = {
            "printer_name",
            "page_count",
            "copies",
            "selected_pages",
            "survey_count",
            "settings",
            "scope",
            "error",
            "reason",
            "driver_job_id",
            "spooler_job_id",
            "confirmation_note",
            "dashboard_snapshot",
        }
        for key in allowed:
            if key in updates:
                record[key] = (
                    _normalize_snapshot_reference(updates[key])
                    if key == "dashboard_snapshot"
                    else _json_value(updates[key])
                )

    def _next_control_number(
        self,
        records: list[dict[str, Any]],
        month: str,
    ) -> str:
        pattern = re.compile(
            rf"^{re.escape(self.CONTROL_PREFIX)}-{re.escape(month)}-(\d{{4}})$"
        )
        used = {
            int(match.group(1))
            for record in records
            for match in [
                pattern.fullmatch(str(record.get("control_number") or ""))
            ]
            if match is not None
        }
        sequence = max(used, default=0) + 1
        if sequence > 9999:
            raise PrintHistoryStoreError(
                f"The Dashboard print sequence for {month} has reached 9999."
            )
        return f"{self.CONTROL_PREFIX}-{month}-{sequence:04d}"

    def _find_record(
        self,
        document: Mapping[str, Any],
        record_id_or_control: str,
    ) -> dict[str, Any]:
        wanted = str(record_id_or_control or "").strip()
        for record in document["records"]:
            if str(record.get("id") or "") == wanted:
                return record
            if str(record.get("control_number") or "").casefold() == wanted.casefold():
                return record
        raise PrintHistoryStoreError(f"Print audit was not found: {wanted or 'blank'}")

    def _blank_document(self) -> dict[str, Any]:
        return {
            "file_type": self.FILE_TYPE,
            "schema_version": self.SCHEMA_VERSION,
            "updated_at": _local_now(),
            "records": [],
        }

    def _read_document(self) -> dict[str, Any]:
        self.last_read_error = None
        if not self.path.exists():
            return self._blank_document()
        try:
            parsed = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._set_read_error(str(exc))
            return self._blank_document()
        if isinstance(parsed, list):
            records = parsed
            document = self._blank_document()
        elif isinstance(parsed, dict):
            document = parsed
            records = parsed.get("records", [])
            file_type = str(parsed.get("file_type") or "").strip()
            if file_type and file_type != self.FILE_TYPE:
                self._set_read_error("The print-history file type is not recognized.")
                return self._blank_document()
            schema = str(parsed.get("schema_version") or "").strip()
            if schema and schema not in {"1.0", "2.0", self.SCHEMA_VERSION}:
                self._set_read_error(
                    "The print-history schema version is not supported."
                )
                return self._blank_document()
        else:
            self._set_read_error(
                "The print-history file root is not an object or list."
            )
            return self._blank_document()
        if not isinstance(records, list):
            self._set_read_error("The print-history records value is not a list.")
            records = []
        normalized_records: list[dict[str, Any]] = []
        for value in records:
            if not isinstance(value, dict) or not _valid_existing_id(value.get("id")):
                continue
            record = deepcopy(value)
            # Build 10 records represented successful physical submissions
            # without a status field. Preserve them as confirmed history.
            status = str(record.get("status") or "confirmed").casefold()
            record["status"] = (
                status if status in self.STATUSES else "confirmed"
            )
            normalized_records.append(record)
        document["file_type"] = str(document.get("file_type") or self.FILE_TYPE)
        document["schema_version"] = str(
            document.get("schema_version") or self.SCHEMA_VERSION
        )
        document["records"] = normalized_records
        return document

    def _write_document(self, document: Mapping[str, Any]) -> None:
        self._raise_if_corrupt()
        payload = deepcopy(dict(document))
        payload["file_type"] = self.FILE_TYPE
        payload["schema_version"] = self.SCHEMA_VERSION
        payload["updated_at"] = _local_now()
        try:
            atomic_write_json(self.path, payload, allow_nan=False)
        except (OSError, TypeError, ValueError) as exc:
            raise PrintHistoryStoreError(
                f"Unable to save Dashboard print history to {self.path}: {exc}"
            ) from exc

    def _raise_if_corrupt(self) -> None:
        if self.path.exists() and self.last_read_error:
            raise PrintHistoryStoreError(
                "The existing print-history file could not be read and was left unchanged. "
                "A recovery copy was preserved."
            )

    def _set_read_error(self, message: str) -> None:
        self.last_read_error = str(message)
        self.last_recovery_copy = preserve_corrupt_file(self.path)


def _normalize_timestamp(value: Any = None) -> str:
    if value in (None, ""):
        return _local_now()
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, datetime.min.time()).astimezone()
    else:
        text = str(value).strip()
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("Generated timestamp must use ISO 8601 format.") from exc
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed.isoformat(timespec="seconds")


def _local_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a positive whole number.")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a positive whole number.") from exc
    if number < 1 or str(value).strip() not in {str(number), f"{number}.0"}:
        raise ValueError(f"{label} must be a positive whole number.")
    return number


def _nonnegative_int(value: Any, label: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a non-negative whole number.")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a non-negative whole number.") from exc
    if number < 0 or str(value).strip() not in {str(number), f"{number}.0"}:
        raise ValueError(f"{label} must be a non-negative whole number.")
    return number


def _normalize_selected_pages(value: Any) -> list[int]:
    if isinstance(value, str):
        raw_values = [part.strip() for part in value.split(",") if part.strip()]
    elif isinstance(value, (list, tuple, set)):
        raw_values = list(value)
    else:
        raise ValueError("Selected pages must be a list of positive page numbers.")
    pages = sorted({_positive_int(page, "Selected page") for page in raw_values})
    if not pages:
        raise ValueError("Selected pages must include at least one page.")
    return pages


def _normalize_snapshot_reference(value: Any) -> dict[str, Any]:
    """Validate the portable identity of immutable Dashboard evidence.

    The snapshot store performs the full manifest/blob verification when the
    evidence is opened.  Print history still validates the reference before it
    is persisted so malformed or path-traversing references cannot become part
    of the audit record.
    """

    if not isinstance(value, Mapping):
        raise ValueError("The Dashboard snapshot reference must be a mapping.")
    normalized = _json_value(value)
    if not isinstance(normalized, dict):  # Defensive; mappings normalize to dicts.
        raise ValueError("The Dashboard snapshot reference must be a mapping.")

    snapshot_id = _clean_text(normalized.get("snapshot_id"))
    if not snapshot_id:
        raise ValueError("The Dashboard snapshot identity is required.")
    manifest_path = _clean_text(normalized.get("manifest_path"))
    if not manifest_path:
        raise ValueError("The Dashboard snapshot manifest path is required.")
    parsed_path = Path(manifest_path)
    if parsed_path.is_absolute() or ".." in parsed_path.parts:
        raise ValueError("The Dashboard snapshot manifest path must be relative and safe.")
    digest = _clean_text(normalized.get("digest_sha256")).casefold()
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("The Dashboard snapshot SHA-256 digest is invalid.")

    normalized["snapshot_id"] = snapshot_id
    normalized["manifest_path"] = manifest_path.replace("\\", "/")
    normalized["digest_sha256"] = digest
    return normalized


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return deepcopy(value)
    if isinstance(value, Enum):
        return _json_value(value.value)
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_value(item) for item in value]
    return str(value)


def _valid_existing_id(value: Any) -> bool:
    try:
        UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return False
    return True


def _record_sort_key(record: Mapping[str, Any]) -> tuple[str, str]:
    return (
        str(record.get("generated_at") or ""),
        str(record.get("id") or ""),
    )
