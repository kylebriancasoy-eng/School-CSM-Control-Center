"""Atomic JSON persistence for standalone CSM survey records."""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timezone
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence
from uuid import UUID, uuid4

from school_csm_control_center.questionnaire import (
    CC_QUESTIONS,
    SQD_QUESTIONS,
    SURVEY_MODES,
    apply_cc_branching,
)
from school_csm_control_center.school_services import normalize_service_values
from school_csm_control_center.runtime_paths import storage_root_for
from school_csm_control_center.storage.control_center_settings import validate_school_id
from school_csm_control_center.storage.file_safety import (
    InterProcessRLock,
    atomic_write_json,
    preserve_corrupt_file,
)


RESPONSE_SOURCE_CODES = {"WBS", "MRS", "SCC"}
SOURCE_LABELS = {
    "WBS": "Browser Survey Form",
    "MRS": "Scanned Hardcopy",
    "SCC": "Control Center Manual Entry",
}


class SurveyStoreError(RuntimeError):
    """Raised when survey persistence cannot be completed safely."""


class DuplicateControlNumberError(ValueError):
    """Raised when a non-empty control number already exists."""


class SurveyStore:
    """Store survey records in one defensive, atomically replaced JSON file."""

    FILE_TYPE = "School CSM Control Center Results"
    SCHEMA_VERSION = "2.0"

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
            / "survey_results.mossjson"
        )
        self._lock = InterProcessRLock(self.path)
        self.last_read_error: str | None = None
        self.last_recovery_copy: Path | None = None

    def list(self) -> list[dict[str, Any]]:
        """Return all valid stored records, newest first, as independent copies."""

        with self._lock:
            document = self._read_document()
            records = [
                deepcopy(record)
                for record in document["records"]
                if isinstance(record, dict)
            ]
        records.sort(key=_record_sort_key, reverse=True)
        return records

    def list_all(self) -> list[dict[str, Any]]:
        """Compatibility alias for ``list``."""

        return self.list()

    def get(self, record_id: str) -> dict[str, Any] | None:
        """Return one record by UUID, or ``None`` when it is not present."""

        wanted = str(record_id or "").strip()
        if not wanted:
            return None
        for record in self.list():
            if str(record.get("id") or "") == wanted:
                return record
        return None

    def next_control_number(
        self,
        survey_date: Any,
        school_id: Any,
        source_code: str = "SCC",
    ) -> str:
        """Return the next monthly response control number.

        This calculates but does not reserve a number. Browser submissions use
        :meth:`add_with_next_control_number`, which performs generation and
        persistence under one lock so simultaneous phones cannot collide.
        """

        normalized_date = _normalize_date(survey_date)
        if normalized_date is None:
            raise ValueError("A survey date is required for a control number.")
        normalized_source = _normalize_source_code(source_code)
        # The compact SCC/WBS format is YYYY-MM-#### and does not contain a
        # school identifier.  Keep those entry paths usable during the neutral
        # first-run state; MRS numbers still require the school identity that is
        # encoded in CSM-MRS-<school-id>-YYYY-MM-####.
        normalized_school_id = validate_school_id(
            school_id,
            required=normalized_source == "MRS",
        )
        with self._lock:
            return _next_control_number_for_records(
                self._read_document()["records"],
                normalized_date,
                normalized_school_id,
                normalized_source,
            )

    def add_with_next_control_number(
        self,
        record: Mapping[str, Any],
        survey_date: Any,
        school_id: Any,
        source_code: str,
    ) -> dict[str, Any]:
        """Atomically generate a source-specific control number and add a record.

        This is intended for concurrent browser submissions. The store lock is
        held across control-number generation, duplicate checking, and the
        atomic file replacement.
        """

        if not isinstance(record, Mapping):
            raise TypeError("A survey record must be a mapping.")
        normalized_date = _normalize_date(survey_date)
        if normalized_date is None:
            raise ValueError("A survey date is required for a control number.")
        normalized_source = _normalize_source_code(source_code)
        normalized_school_id = validate_school_id(
            school_id,
            required=normalized_source == "MRS",
        )
        with self._lock:
            document = self._read_document()
            records = document["records"]
            payload = deepcopy(dict(record))
            payload["survey_date"] = normalized_date
            payload["source_code"] = normalized_source
            payload["control_number"] = _next_control_number_for_records(
                records,
                normalized_date,
                normalized_school_id,
                normalized_source,
            )
            normalized = self._normalize_payload(payload)
            self._reject_duplicate_control_number(
                records, normalized.get("control_number", "")
            )
            now = _utc_now()
            normalized["id"] = str(uuid4())
            normalized["created_at"] = now
            normalized["updated_at"] = now
            records.append(normalized)
            self._write_document(document)
            return deepcopy(normalized)

    def add(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """Validate, assign UUID/timestamps, persist, and return a new record."""

        if not isinstance(record, Mapping):
            raise TypeError("A survey record must be a mapping.")
        with self._lock:
            document = self._read_document()
            records = document["records"]
            normalized = self._normalize_payload(record)
            self._reject_duplicate_control_number(
                records, normalized.get("control_number", "")
            )
            now = _utc_now()
            normalized["id"] = str(uuid4())
            normalized["created_at"] = now
            normalized["updated_at"] = now
            records.append(normalized)
            self._write_document(document)
            return deepcopy(normalized)

    def import_records(
        self,
        records: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        """Atomically import normalized survey records with duplicate protection.

        Valid external UUIDs and creation timestamps are preserved when they do
        not conflict with existing data. Calculated analysis fields are never
        imported; they are recomputed from the stored answers.
        """

        if not isinstance(records, Sequence) or isinstance(records, (str, bytes)):
            raise TypeError("Imported survey records must be a sequence of mappings.")
        with self._lock:
            document = self._read_document()
            stored = document["records"]
            existing_ids = {str(item.get("id") or "") for item in stored}
            existing_controls = {
                _control_key(item.get("control_number"))
                for item in stored
                if _control_key(item.get("control_number"))
            }
            imported: list[dict[str, Any]] = []
            skipped: list[dict[str, Any]] = []
            errors: list[dict[str, Any]] = []
            now = _utc_now()

            for index, record in enumerate(records, start=1):
                if not isinstance(record, Mapping):
                    errors.append({"index": index, "error": "Record is not a mapping."})
                    continue
                try:
                    normalized = self._normalize_payload(record)
                except (TypeError, ValueError) as exc:
                    errors.append({"index": index, "error": str(exc)})
                    continue

                control_key = _control_key(normalized.get("control_number"))
                requested_id = str(record.get("id") or "").strip()
                if requested_id and requested_id in existing_ids:
                    skipped.append({
                        "index": index,
                        "reason": "duplicate_id",
                        "id": requested_id,
                        "control_number": normalized.get("control_number", ""),
                    })
                    continue
                if control_key and control_key in existing_controls:
                    skipped.append({
                        "index": index,
                        "reason": "duplicate_control_number",
                        "id": requested_id,
                        "control_number": normalized.get("control_number", ""),
                    })
                    continue

                record_id = requested_id if _valid_existing_id(requested_id) else str(uuid4())
                if record_id in existing_ids:
                    record_id = str(uuid4())
                created_at = _normalize_import_timestamp(record.get("created_at")) or now
                normalized["id"] = record_id
                normalized["created_at"] = created_at
                normalized["updated_at"] = now
                stored.append(normalized)
                imported.append(deepcopy(normalized))
                existing_ids.add(record_id)
                if control_key:
                    existing_controls.add(control_key)

            if imported:
                self._write_document(document)
            return {
                "imported": imported,
                "skipped": skipped,
                "errors": errors,
                "imported_count": len(imported),
                "skipped_count": len(skipped),
                "error_count": len(errors),
            }

    def update(
        self,
        record_id: str,
        changes: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Merge and persist changes while preserving UUID and creation time."""

        wanted = str(record_id or "").strip()
        if not wanted:
            raise KeyError("A survey record ID is required.")
        if not isinstance(changes, Mapping):
            raise TypeError("Survey changes must be a mapping.")

        with self._lock:
            document = self._read_document()
            records = document["records"]
            index = next(
                (
                    index
                    for index, record in enumerate(records)
                    if str(record.get("id") or "") == wanted
                ),
                None,
            )
            if index is None:
                raise KeyError(f"Survey record not found: {wanted}")

            current = records[index]
            merged = _merge_record(current, changes)
            if _source_code_from_record(
                current, current.get("meta") if isinstance(current.get("meta"), Mapping) else {}
            ) == "MRS":
                current_meta = current.get("meta") if isinstance(current.get("meta"), Mapping) else {}
                merged_meta = merged.get("meta") if isinstance(merged.get("meta"), Mapping) else {}
                merged_meta = deepcopy(dict(merged_meta))
                for key in (
                    "scanner_operator_user_id",
                    "scanner_operator_username",
                    "scanner_operator_display_name",
                    "scanner_session_id",
                    "scanner_capture_session_id",
                    "scanner_submission_id",
                    "scanner_job_id",
                    "scanner_image_uploaded_at",
                    "scanner_processing_started_at",
                    "scanner_processing_completed_at",
                    "scanner_review_completed_at",
                    "scanner_finalized_at",
                    "scanner_processing_engine_version",
                    "scanner_image_sha256",
                    "scanner_original_image_path",
                    "scanner_source_image_path",
                    "scanner_canonical_image_path",
                    "scanner_corrected_image_path",
                    "source_form_control_number",
                ):
                    if key in current_meta:
                        merged_meta[key] = deepcopy(current_meta[key])
                merged["meta"] = merged_meta
                merged["source_code"] = "MRS"
            normalized = self._normalize_payload(merged)
            self._reject_duplicate_control_number(
                records,
                normalized.get("control_number", ""),
                exclude_id=wanted,
            )
            normalized["id"] = wanted
            normalized["created_at"] = str(
                current.get("created_at") or _utc_now()
            )
            normalized["updated_at"] = _utc_now()
            records[index] = normalized
            self._write_document(document)
            return deepcopy(normalized)

    def delete_date_range(
        self,
        date_from: Any = None,
        date_to: Any = None,
    ) -> dict[str, Any]:
        """Atomically delete survey records within an inclusive date range.

        The survey date is authoritative; ``created_at`` is used only when an
        older imported record has no survey date. Passing no bounds clears all
        survey-response records. Print history and MRS registry data live in
        separate stores and are never touched by this method.
        """

        start = _normalize_date(date_from) if date_from not in (None, "") else None
        end = _normalize_date(date_to) if date_to not in (None, "") else None
        if date_from not in (None, "") and start is None:
            raise ValueError("The history start date is invalid.")
        if date_to not in (None, "") and end is None:
            raise ValueError("The history end date is invalid.")
        if start and end and start > end:
            start, end = end, start

        with self._lock:
            document = self._read_document()
            records = document["records"]
            removed: list[dict[str, Any]] = []
            remaining: list[dict[str, Any]] = []
            for record in records:
                if start is None and end is None:
                    removed.append(record)
                    continue
                meta = record.get("meta") if isinstance(record.get("meta"), Mapping) else {}
                record_date = _normalize_date(
                    record.get("survey_date")
                    or meta.get("survey_date")
                    or str(record.get("created_at") or "")[:10]
                )
                matches = record_date is not None
                if matches and start and record_date < start:
                    matches = False
                if matches and end and record_date > end:
                    matches = False
                if matches:
                    removed.append(record)
                else:
                    remaining.append(record)
            if removed:
                document["records"] = remaining
                self._write_document(document)
            return {
                "removed_count": len(removed),
                "remaining_count": len(remaining),
                "removed": deepcopy(removed),
                "date_from": start,
                "date_to": end,
            }

    def delete(self, record_id: str) -> bool:
        """Delete one UUID and return whether a record was removed."""

        wanted = str(record_id or "").strip()
        if not wanted:
            return False
        with self._lock:
            document = self._read_document()
            records = document["records"]
            remaining = [
                record
                for record in records
                if str(record.get("id") or "") != wanted
            ]
            if len(remaining) == len(records):
                return False
            document["records"] = remaining
            self._write_document(document)
            return True

    def _normalize_payload(
        self, record: Mapping[str, Any]
    ) -> dict[str, Any]:
        mode = str(record.get("mode") or "onsite").strip().casefold()
        if mode not in SURVEY_MODES:
            raise ValueError(
                f"Survey mode must be one of: {', '.join(SURVEY_MODES)}."
            )

        meta = record.get("meta") or {}
        if not isinstance(meta, Mapping):
            raise ValueError("Survey meta must be a mapping.")
        normalized_meta = deepcopy(dict(meta))
        aliases = {
            "client_type": ("client_type", "customer_type"),
            "sex": ("sex",),
            "age": ("age",),
            "region": ("region", "region_of_residence"),
            "agency_visited": ("agency_visited",),
            "service_availed": ("service_availed", "service"),
            "email": ("email", "email_address"),
        }
        for target, candidates in aliases.items():
            if target in normalized_meta:
                continue
            value, found = _first_present(record, candidates)
            if found:
                normalized_meta[target] = value

        if "age" in normalized_meta:
            raw_age = normalized_meta["age"]
            if raw_age in (None, ""):
                normalized_meta["age"] = None
            else:
                age = _strict_int(raw_age, "Age")
                if age < 0 or age > 130:
                    raise ValueError("Age must be between 0 and 130.")
                normalized_meta["age"] = age
        if "service_availed" in normalized_meta:
            normalized_meta["service_availed"] = normalize_service_values(
                normalized_meta.get("service_availed"),
                strict=True,
            )
        else:
            normalized_meta["service_availed"] = []
        for key, value in list(normalized_meta.items()):
            if isinstance(value, str):
                normalized_meta[key] = " ".join(value.split())

        survey_date = record.get("survey_date")
        if survey_date is None and "survey_date" in normalized_meta:
            survey_date = normalized_meta["survey_date"]
        normalized_meta.pop("survey_date", None)
        normalized_date = _normalize_date(survey_date)

        source_code = _source_code_from_record(record, normalized_meta)
        normalized_meta["submission_source"] = {
            "WBS": "local_web_form",
            "MRS": "scanned_hardcopy",
            "SCC": "manual_entry",
        }[source_code]
        normalized_meta["source_label"] = SOURCE_LABELS[source_code]

        control_number = _control_number(record, normalized_meta)
        normalized_meta.pop("control_number", None)
        normalized_meta.pop("control_no", None)

        cc = self._normalize_cc(record, mode)
        sqd = self._normalize_sqd(record, mode)
        feedback = _normalize_feedback(record, mode, normalized_meta)

        normalized: dict[str, Any] = {
            "control_number": control_number,
            "source_code": source_code,
            "mode": mode,
            "meta": normalized_meta,
            "cc": cc,
            "sqd": sqd,
            "feedback": feedback,
        }
        if normalized_date is not None:
            normalized["survey_date"] = normalized_date
        return normalized

    def _normalize_cc(
        self, record: Mapping[str, Any], mode: str
    ) -> dict[str, Any]:
        raw_cc = record.get("cc") or {}
        if not isinstance(raw_cc, Mapping):
            raise ValueError("Citizen's Charter answers must be a mapping.")
        answers = deepcopy(dict(raw_cc))
        for key in ("cc1", "cc2", "cc3", "cc3_reason"):
            if key not in answers and key in record:
                answers[key] = record[key]

        normalized: dict[str, Any] = {}
        for key in ("cc1", "cc2", "cc3"):
            if key not in answers:
                continue
            raw_value = answers[key]
            if raw_value in (None, ""):
                normalized[key] = None
                continue
            value = _strict_int(raw_value, key.upper())
            valid_values = {
                option["value"]
                for option in CC_QUESTIONS[mode][key]["options"]
            }
            if value not in valid_values:
                allowed = ", ".join(str(item) for item in sorted(valid_values))
                raise ValueError(f"{key.upper()} must be one of: {allowed}.")
            normalized[key] = value

        reason = " ".join(str(answers.get("cc3_reason") or "").split())
        if mode == "online" and normalized.get("cc3") == 2 and reason:
            normalized["cc3_reason"] = reason
        normalized = apply_cc_branching(mode, normalized)
        return normalized

    def _normalize_sqd(
        self, record: Mapping[str, Any], mode: str
    ) -> dict[str, int | None]:
        raw_sqd = record.get("sqd") or {}
        if not isinstance(raw_sqd, Mapping):
            raise ValueError("SQD answers must be a mapping.")
        answers = {str(key).casefold(): value for key, value in raw_sqd.items()}
        for number in range(0, 9):
            key = f"sqd{number}"
            if key not in answers:
                if key in record:
                    answers[key] = record[key]
                elif key.upper() in record:
                    answers[key] = record[key.upper()]

        if mode == "online" and answers.get("sqd0") not in (None, ""):
            raise ValueError("SQD0 is available only for onsite surveys.")

        expected = SQD_QUESTIONS[mode]
        normalized: dict[str, int | None] = {}
        for key in expected:
            if key not in answers:
                continue
            value = _normalize_stored_rating(answers[key], allow_na=mode == "onsite")
            normalized[key] = value
        return normalized

    def _reject_duplicate_control_number(
        self,
        records: list[dict[str, Any]],
        control_number: Any,
        exclude_id: str = "",
    ) -> None:
        wanted = _control_key(control_number)
        if not wanted:
            return
        for record in records:
            if exclude_id and str(record.get("id") or "") == exclude_id:
                continue
            if _control_key(record.get("control_number")) == wanted:
                raise DuplicateControlNumberError(
                    f"Control number already exists: {str(control_number).strip()}"
                )

    def _blank_document(self) -> dict[str, Any]:
        return {
            "file_type": self.FILE_TYPE,
            "schema_version": self.SCHEMA_VERSION,
            "updated_at": _utc_now(),
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
            document["records"] = records
        elif isinstance(parsed, dict):
            document = parsed
            records = document.get("records", document.get("surveys", []))
        else:
            self._set_read_error("The survey file root is not an object or list.")
            return self._blank_document()

        if not isinstance(records, list):
            self._set_read_error("The survey records value is not a list.")
            records = []
        document["file_type"] = str(document.get("file_type") or self.FILE_TYPE)
        document["schema_version"] = str(
            document.get("schema_version") or self.SCHEMA_VERSION
        )
        document["records"] = [
            deepcopy(record)
            for record in records
            if isinstance(record, dict) and _valid_existing_id(record.get("id"))
        ]
        document.pop("surveys", None)
        return document

    def _write_document(self, document: dict[str, Any]) -> None:
        if self.path.exists() and self.last_read_error:
            raise SurveyStoreError(
                "The existing survey-results file could not be read and was left unchanged. "
                "A recovery copy was preserved."
            )
        document = deepcopy(document)
        document["file_type"] = self.FILE_TYPE
        document["schema_version"] = self.SCHEMA_VERSION
        document["updated_at"] = _utc_now()
        try:
            atomic_write_json(self.path, document)
        except (OSError, ValueError, TypeError) as exc:
            raise SurveyStoreError(
                f"Unable to save survey results to {self.path}: {exc}"
            ) from exc

    def _set_read_error(self, message: str) -> None:
        self.last_read_error = str(message)
        self.last_recovery_copy = preserve_corrupt_file(self.path)


def _next_control_number_for_records(
    records: list[dict[str, Any]],
    normalized_date: str,
    school_id: str,
    source_code: str,
) -> str:
    year, month = normalized_date[:7].split("-")
    if source_code == "MRS":
        prefix = f"CSM-MRS-{school_id}-{year}"
        pattern = re.compile(
            rf"^{re.escape(prefix)}-(?P<month>\d{{2}})-(?P<sequence>\d{{4}})$",
            re.IGNORECASE,
        )
    else:
        prefix = year
        pattern = re.compile(
            rf"^(?:{re.escape(year)}-(?P<month>\d{{2}})-"
            rf"|CSM-(?:SCC|WBS)-\d{{4,12}}-{re.escape(year)}-(?P<legacy_month>\d{{2}})-)"
            rf"(?P<sequence>\d{{4}})$",
            re.IGNORECASE,
        )
    used_sequences: set[int] = set()
    used_control_numbers: set[str] = set()
    for record in records:
        control_number = str(record.get("control_number") or "").strip()
        used_control_numbers.add(_control_key(control_number))
        match = pattern.fullmatch(control_number)
        if match is not None and (
            match.groupdict().get("month") == month
            or match.groupdict().get("legacy_month") == month
        ):
            used_sequences.add(int(match.group("sequence")))

    sequence = max(used_sequences, default=0) + 1
    while sequence <= 9999:
        candidate = (
            f"{prefix}-{month}-{sequence:04d}"
            if source_code == "MRS"
            else f"{year}-{month}-{sequence:04d}"
        )
        if _control_key(candidate) not in used_control_numbers:
            return candidate
        sequence += 1
    raise SurveyStoreError(
        f"The response control-number sequence for {year}-{month} has reached 9999."
    )


def _normalize_source_code(value: Any) -> str:
    code = str(value or "").strip().upper()
    if code not in RESPONSE_SOURCE_CODES:
        raise ValueError("Response source must be WBS, MRS, or SCC.")
    return code


def _source_code_from_record(record: Mapping[str, Any], meta: Mapping[str, Any]) -> str:
    explicit = str(record.get("source_code") or meta.get("source_code") or "").strip().upper()
    if explicit:
        return _normalize_source_code(explicit)
    submission_source = str(meta.get("submission_source") or "").strip().casefold()
    return {
        "local_web_form": "WBS",
        "browser_survey": "WBS",
        "scanned_hardcopy": "MRS",
        "scanner": "MRS",
        "manual_entry": "SCC",
        "control_center_manual": "SCC",
    }.get(submission_source, "SCC")


def _merge_record(
    current: Mapping[str, Any], changes: Mapping[str, Any]
) -> dict[str, Any]:
    merged = deepcopy(dict(current))
    for key, value in changes.items():
        if key in {"id", "created_at", "updated_at"}:
            continue
        if key in {"meta", "cc", "sqd", "feedback"} and isinstance(value, Mapping):
            existing = merged.get(key)
            nested = dict(existing) if isinstance(existing, Mapping) else {}
            nested.update(deepcopy(dict(value)))
            merged[key] = nested
        else:
            merged[key] = deepcopy(value)
    return merged


def _normalize_stored_rating(value: Any, allow_na: bool) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError("SQD ratings must be 1-5, N/A, or unanswered.")
    text = " ".join(str(value).split()).casefold()
    if text in {"n/a", "na", "not applicable"}:
        value = 0
    number = _strict_int(value, "SQD rating")
    if number == 0:
        if allow_na:
            return 0
        raise ValueError("Online SQD ratings do not include N/A.")
    if number not in {1, 2, 3, 4, 5}:
        raise ValueError("SQD ratings must be 1, 2, 3, 4, 5, or N/A.")
    return number


def _control_number(
    record: Mapping[str, Any], meta: Mapping[str, Any]
) -> str:
    value = (
        record.get("control_number")
        or record.get("control_no")
        or meta.get("control_number")
        or meta.get("control_no")
        or ""
    )
    return " ".join(str(value).split())


def _control_key(value: Any) -> str:
    return " ".join(str(value or "").split()).casefold()


def _feedback_text(record: Mapping[str, Any]) -> str:
    feedback = record.get("feedback")
    if isinstance(feedback, Mapping):
        for key in ("text", "suggestions", "remarks", "comment", "comments"):
            text = " ".join(str(feedback.get(key) or "").split())
            if text:
                return text
        return ""
    value = feedback or record.get("suggestions") or record.get("remarks") or ""
    return " ".join(str(value).split())


def _normalize_feedback(
    record: Mapping[str, Any],
    mode: str,
    meta: dict[str, Any],
) -> dict[str, str]:
    feedback = record.get("feedback")
    email = ""
    if isinstance(feedback, Mapping):
        email = " ".join(str(feedback.get("email") or "").split())
    if mode == "onsite":
        email = email or " ".join(
            str(
                record.get("email")
                or record.get("email_address")
                or meta.get("email")
                or ""
            ).split()
        )
    meta.pop("email", None)
    return {
        "comments": _feedback_text(record),
        "email": email if mode == "onsite" else "",
    }


def _normalize_date(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError as exc:
        raise ValueError("Survey date must use YYYY-MM-DD format.") from exc


def _strict_int(value: Any, label: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a whole number.")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a whole number.") from exc
    if not number.is_integer():
        raise ValueError(f"{label} must be a whole number.")
    return int(number)


def _first_present(
    record: Mapping[str, Any], keys: tuple[str, ...]
) -> tuple[Any, bool]:
    for key in keys:
        if key in record:
            return record[key], True
    return None, False


def _record_sort_key(record: Mapping[str, Any]) -> tuple[str, str, str]:
    timestamp = str(record.get("updated_at") or record.get("created_at") or "")
    return (
        str(record.get("survey_date") or timestamp[:10]),
        timestamp,
        str(record.get("id") or ""),
    )


def _valid_existing_id(value: Any) -> bool:
    try:
        UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return False
    return True


def _normalize_import_timestamp(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    candidate = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )
