"""Machine-readable survey form registry and physical-copy accounting.

The registry is separate from accepted survey responses.  It tracks every
control number from generation through printing, reprinting, scanning,
validity, and analysis eligibility.  A public control number may have many
print and scan attempts, but only one accepted valid response.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timezone
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence
from uuid import uuid4

from school_csm_control_center.runtime_paths import storage_root_for
from school_csm_control_center.storage.control_center_settings import validate_school_id
from school_csm_control_center.storage.file_safety import (
    InterProcessRLock,
    atomic_write_json,
    preserve_corrupt_file,
)


MRS_CONTROL_PATTERN = re.compile(
    r"^CSM-MRS-(?P<school>\d{4,12})-(?P<year>\d{4})-(?P<month>\d{2})-(?P<sequence>\d{4})$"
)
BARCODE_STATUSES = {"normal", "crossed_out", "damaged", "unreadable", "uncertain"}
VALIDITY_STATUSES = {"valid", "invalid", "duplicate", "unknown", "held", "rejected"}
ANALYSIS_STATUSES = {"included", "excluded", "pending", "not_applicable"}
PRINT_STATUSES = {
    "Generated", "Print Submitted", "Printed", "Print Failed", "Pending Reprint",
    "Reprint Submitted", "Administratively Voided",
}

def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _coerce_datetime(value: Any = None) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    text = str(value or "").strip()
    if text:
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def normalize_mrs_control_number(value: Any) -> str:
    return "-".join(part for part in str(value or "").strip().upper().split("-") if part)


def parse_mrs_control_number(value: Any) -> dict[str, Any] | None:
    control = normalize_mrs_control_number(value)
    match = MRS_CONTROL_PATTERN.fullmatch(control)
    if not match:
        return None
    parts = match.groupdict()
    return {
        "control_number": control,
        "school_id": parts["school"],
        "year": int(parts["year"]),
        "month": int(parts["month"]),
        "sequence": int(parts["sequence"]),
    }


class MRSRegistryError(RuntimeError):
    """Raised when an MRS lifecycle transition is invalid or unsafe."""


class MRSRegistryStore:
    FILE_TYPE = "School CSM Control Center MRS Registry"
    SCHEMA_VERSION = "0.4"
    TEMPLATE_ID = "CSM-MRS-A4-2026-04-EN"

    def __init__(
        self,
        project_root: str | Path,
        path: str | Path | None = None,
        *,
        data_root: str | Path | None = None,
    ) -> None:
        self.install_root = Path(project_root)
        self.project_root = storage_root_for(project_root, data_root)
        self.path = Path(path) if path else self.project_root / "data" / "csm_survey" / "mrs_registry.mossjson"
        self._lock = InterProcessRLock(self.path)
        self.last_read_error: str | None = None
        self.last_recovery_copy: Path | None = None

    def list_forms(self) -> list[dict[str, Any]]:
        with self._lock:
            records = [deepcopy(value) for value in self._read_document()["forms"] if isinstance(value, dict)]
        records.sort(key=lambda row: (str(row.get("original_date_generated") or ""), str(row.get("control_number") or "")), reverse=True)
        return records

    def list_print_attempts(self, control_number: str | None = None) -> list[dict[str, Any]]:
        wanted = normalize_mrs_control_number(control_number) if control_number else ""
        with self._lock:
            rows = [
                deepcopy(value) for value in self._read_document()["print_attempts"]
                if isinstance(value, dict) and (not wanted or normalize_mrs_control_number(value.get("control_number")) == wanted)
            ]
        rows.sort(key=lambda row: str(row.get("date_time") or ""), reverse=True)
        return rows

    def list_scan_attempts(self, control_number: str | None = None) -> list[dict[str, Any]]:
        wanted = normalize_mrs_control_number(control_number) if control_number else ""
        with self._lock:
            rows = [
                deepcopy(value) for value in self._read_document()["scan_attempts"]
                if isinstance(value, dict) and (not wanted or normalize_mrs_control_number(value.get("control_number")) == wanted)
            ]
        rows.sort(key=lambda row: str(row.get("scanned_at") or ""), reverse=True)
        return rows

    def find_scan_attempt_by_submission(
        self,
        scanner_submission_id: str,
    ) -> dict[str, Any] | None:
        """Return the registry attempt linked to a scanner submission."""

        wanted = str(scanner_submission_id or "").strip()
        if not wanted:
            return None
        with self._lock:
            for row in reversed(self._read_document()["scan_attempts"]):
                if str(row.get("scanner_submission_id") or "") == wanted:
                    return deepcopy(row)
        return None

    def list_print_batches(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = [
                deepcopy(value) for value in self._read_document()["print_batches"]
                if isinstance(value, dict)
            ]
        rows.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
        return rows

    def pending_print_confirmations(self) -> list[dict[str, Any]]:
        return [
            row for row in self.list_print_batches()
            if str(row.get("status") or "") == "Awaiting Post-Print Confirmation"
        ]

    def get_form(self, control_number: Any) -> dict[str, Any] | None:
        wanted = normalize_mrs_control_number(control_number)
        if not wanted:
            return None
        return next((row for row in self.list_forms() if normalize_mrs_control_number(row.get("control_number")) == wanted), None)

    def pending_reprints(self) -> list[dict[str, Any]]:
        records = [row for row in self.list_forms() if bool(row.get("pending_reprint"))]
        records.sort(key=lambda row: (str(row.get("reprint_queued_at") or ""), str(row.get("control_number") or "")))
        return records

    def preview_new_control_numbers(self, count: int, school_id: Any, generated_at: Any = None) -> list[str]:
        quantity = max(0, min(500, int(count or 0)))
        if not quantity:
            return []
        school = validate_school_id(school_id, required=True)
        stamp = _coerce_datetime(generated_at)
        with self._lock:
            records = self._read_document()["forms"]
            last = self._highest_sequence(records, school, stamp.year)
            return [f"CSM-MRS-{school}-{stamp.year:04d}-{stamp.month:02d}-{last + offset:04d}" for offset in range(1, quantity + 1)]

    def create_print_batch(
        self,
        *,
        school_id: Any,
        language: str,
        template_version: str,
        new_form_count: int,
        printer: Mapping[str, Any],
        printed_by: str,
        generated_at: Any = None,
        include_pending_reprints: bool = True,
    ) -> dict[str, Any]:
        """Create registry entries and print attempts before direct submission.

        Pending reprints are placed first. New forms then consume the next annual
        MRS sequence. The returned page list is authoritative for rendering and
        post-print confirmation.
        """

        school = validate_school_id(school_id, required=True)
        count = max(0, min(500, int(new_form_count or 0)))
        stamp = _coerce_datetime(generated_at)
        language_text = str(language or "English").strip() or "English"
        template = str(template_version or self.TEMPLATE_ID).strip() or self.TEMPLATE_ID
        printer_name = str(printer.get("name") or "").strip()
        if not printer_name:
            raise MRSRegistryError("A connected physical printer must be selected.")
        with self._lock:
            document = self._read_document()
            if self.path.exists() and self.last_read_error:
                raise MRSRegistryError("The existing MRS registry could not be read and was left unchanged.")
            forms = document["forms"]
            pending = []
            if include_pending_reprints:
                pending = [row for row in forms if bool(row.get("pending_reprint"))]
                pending.sort(key=lambda row: (str(row.get("reprint_queued_at") or ""), str(row.get("control_number") or "")))
            last_sequence = self._highest_sequence(forms, school, stamp.year)
            new_controls: list[str] = []
            for index in range(count):
                sequence = last_sequence + index + 1
                control = f"CSM-MRS-{school}-{stamp.year:04d}-{stamp.month:02d}-{sequence:04d}"
                form = {
                    "id": str(uuid4()),
                    "control_number": control,
                    "barcode_value": control,
                    "school_id": school,
                    "language": language_text,
                    "template_version": template,
                    "sequence_year": stamp.year,
                    "sequence_month": stamp.month,
                    "sequence_number": sequence,
                    "original_date_generated": stamp.isoformat(timespec="seconds"),
                    "original_date_printed": "",
                    "current_print_status": "Generated",
                    "current_scan_status": "Not Scanned",
                    "current_font_state": "regular",
                    "number_of_print_attempts": 0,
                    "number_of_invalid_scans": 0,
                    "valid_response_received": False,
                    "pending_reprint": False,
                    "reprint_reason": "",
                    "reprint_queued_at": "",
                    "last_date_printed": "",
                    "last_date_scanned": "",
                    "analysis_status": "Pending",
                    "printed_copy_status": "Not Printed",
                    "remarks": "",
                    "created_at": _utc_now(),
                    "updated_at": _utc_now(),
                }
                forms.append(form)
                new_controls.append(control)
            batch_id = self._next_internal_id(document["print_batches"], "MRS-PB", stamp.year, digits=5)
            pages: list[dict[str, Any]] = []
            for form in [*pending, *[row for row in forms if row.get("control_number") in new_controls]]:
                attempt_number = int(form.get("number_of_print_attempts") or 0) + 1
                attempt_id = self._next_internal_id(document["print_attempts"], "MRS-PA", stamp.year, digits=6)
                is_reprint = attempt_number > 1 or bool(form.get("pending_reprint"))
                attempt = {
                    "id": str(uuid4()),
                    "print_attempt_id": attempt_id,
                    "control_number": form["control_number"],
                    "print_batch_id": batch_id,
                    "attempt_number": attempt_number,
                    "printer_name": printer_name,
                    "printer_driver": str(printer.get("driver") or ""),
                    "printer_port": str(printer.get("port") or ""),
                    "connection_type": str(printer.get("connection_type") or "Unknown"),
                    "computer_name": str(printer.get("computer_name") or ""),
                    "availability_status_before_printing": str(printer.get("status") or "Ready"),
                    "a4_capable": bool(printer.get("a4_capable", True)),
                    "print_submission_result": "Pending",
                    "date_time": stamp.isoformat(timespec="seconds"),
                    "printed_by": str(printed_by or "Control Center Operator").strip(),
                    "outcome": "Awaiting Confirmation",
                    "failure_reason": "",
                    "post_print_confirmation_result": "Pending",
                    "is_reprint": is_reprint,
                }
                document["print_attempts"].append(attempt)
                form["number_of_print_attempts"] = attempt_number
                form["current_print_status"] = "Reprint Submitted" if is_reprint else "Print Submitted"
                form["updated_at"] = _utc_now()
                pages.append({
                    "control_number": form["control_number"],
                    "language": form["language"],
                    "template_version": form["template_version"],
                    "print_attempt_id": attempt_id,
                    "attempt_number": attempt_number,
                    "is_reprint": is_reprint,
                    "reprint_reason": str(form.get("reprint_reason") or ""),
                })
            if not pages:
                raise MRSRegistryError("There are no new forms or pending reprints to print.")
            batch = {
                "id": str(uuid4()),
                "print_batch_id": batch_id,
                "created_at": stamp.isoformat(timespec="seconds"),
                "printed_by": str(printed_by or "Control Center Operator").strip(),
                "printer": deepcopy(dict(printer)),
                "language": language_text,
                "template_version": template,
                "pending_reprint_count": len(pending),
                "new_form_count": len(new_controls),
                "total_pages": len(pages),
                "new_control_number_start": new_controls[0] if new_controls else "",
                "new_control_number_end": new_controls[-1] if new_controls else "",
                "page_control_numbers": [page["control_number"] for page in pages],
                "status": "Awaiting Print Submission",
                "closed_at": "",
            }
            document["print_batches"].append(batch)
            self._write_document(document)
            return {**deepcopy(batch), "pages": deepcopy(pages)}

    def mark_batch_submitted(self, batch_id: str, *, accepted: bool, error: str = "") -> dict[str, Any]:
        with self._lock:
            document = self._read_document()
            batch = self._find_batch(document, batch_id)
            batch["status"] = "Awaiting Post-Print Confirmation" if accepted else "Print Submission Failed"
            batch["submission_error"] = str(error or "")[:1000]
            controls = set(batch.get("page_control_numbers") or [])
            for attempt in document["print_attempts"]:
                if str(attempt.get("print_batch_id") or "") == batch_id:
                    attempt["print_submission_result"] = "Accepted by Windows" if accepted else "Rejected"
                    if error:
                        attempt["failure_reason"] = str(error)[:500]
            if not accepted:
                # Keep the batch confirmable so the operator can explicitly account for every page.
                batch["status"] = "Awaiting Post-Print Confirmation"
            self._write_document(document)
            return deepcopy(batch)

    def confirm_print_results(
        self,
        batch_id: str,
        failed_control_numbers: Sequence[str],
        *,
        confirmed_by: str = "Control Center Operator",
        failure_reason: str = "Unusable printed output",
    ) -> dict[str, Any]:
        failed = {normalize_mrs_control_number(value) for value in failed_control_numbers if normalize_mrs_control_number(value)}
        with self._lock:
            document = self._read_document()
            batch = self._find_batch(document, batch_id)
            page_controls = [normalize_mrs_control_number(value) for value in batch.get("page_control_numbers") or []]
            unknown = failed.difference(page_controls)
            if unknown:
                raise MRSRegistryError("Failed-page selection contains a control number outside this print batch.")
            now = _utc_now()
            forms_by_control = {normalize_mrs_control_number(row.get("control_number")): row for row in document["forms"]}
            for control in page_controls:
                form = forms_by_control[control]
                attempt = next(
                    row for row in reversed(document["print_attempts"])
                    if normalize_mrs_control_number(row.get("control_number")) == control
                    and str(row.get("print_batch_id") or "") == batch_id
                )
                is_failed = control in failed
                attempt["outcome"] = "Print Failed" if is_failed else "Successful"
                attempt["failure_reason"] = str(failure_reason if is_failed else "")[:500]
                attempt["post_print_confirmation_result"] = "Failed selected" if is_failed else "Printed successfully"
                attempt["confirmed_by"] = str(confirmed_by or "")
                attempt["confirmed_at"] = now
                if is_failed:
                    form["current_print_status"] = "Print Failed"
                    form["current_scan_status"] = "Not Applicable"
                    form["analysis_status"] = "Not Applicable"
                    form["current_font_state"] = "regular"
                    form["printed_copy_status"] = "Print Failed"
                    form["pending_reprint"] = True
                    form["reprint_reason"] = "Failed print"
                    form["reprint_queued_at"] = now
                else:
                    form["current_print_status"] = "Printed"
                    form["current_scan_status"] = "Not Scanned"
                    form["analysis_status"] = "Pending"
                    form["current_font_state"] = "red"
                    form["printed_copy_status"] = "Awaiting Scan"
                    form["pending_reprint"] = False
                    form["reprint_reason"] = ""
                    form["reprint_queued_at"] = ""
                    if not form.get("original_date_printed"):
                        form["original_date_printed"] = now
                    form["last_date_printed"] = now
                form["updated_at"] = now
            batch["status"] = "Closed"
            batch["failed_control_numbers"] = sorted(failed)
            batch["successful_control_numbers"] = [value for value in page_controls if value not in failed]
            batch["closed_at"] = now
            self._write_document(document)
            return deepcopy(batch)

    def queue_reprint(self, control_number: Any, reason: str, *, scan_reference: str = "") -> dict[str, Any]:
        control = normalize_mrs_control_number(control_number)
        with self._lock:
            document = self._read_document()
            form = self._find_form(document, control)
            form["pending_reprint"] = True
            form["current_print_status"] = "Pending Reprint"
            form["reprint_reason"] = str(reason or "Replacement required")[:300]
            form["reprint_queued_at"] = _utc_now()
            if scan_reference:
                form["invalid_scan_reference"] = str(scan_reference)[:160]
            form["updated_at"] = _utc_now()
            self._write_document(document)
            return deepcopy(form)

    def record_scan_attempt(
        self,
        *,
        control_number: Any,
        barcode_status: str,
        scanner_submission_id: str,
        scanner_operator: Mapping[str, Any] | None = None,
        validity: str = "held",
        invalid_reason: str = "",
        analysis_status: str = "pending",
        response_record_id: str = "",
        print_attempt_reference: str = "",
        scanned_at: Any = None,
        remarks: str = "",
        image_sha256: str = "",
    ) -> dict[str, Any]:
        control = normalize_mrs_control_number(control_number)
        barcode = str(barcode_status or "uncertain").strip().casefold().replace(" ", "_")
        if barcode not in BARCODE_STATUSES:
            raise MRSRegistryError("Unsupported barcode classification.")
        validity_key = str(validity or "held").strip().casefold()
        if validity_key not in VALIDITY_STATUSES:
            raise MRSRegistryError("Unsupported scan validity status.")
        analysis_key = str(analysis_status or "pending").strip().casefold()
        if analysis_key not in ANALYSIS_STATUSES:
            raise MRSRegistryError("Unsupported analysis status.")
        stamp = _coerce_datetime(scanned_at).isoformat(timespec="seconds")
        operator = dict(scanner_operator or {})
        submission_id = str(scanner_submission_id or "").strip()[:160]
        with self._lock:
            document = self._read_document()
            self._raise_if_corrupt()
            if submission_id:
                existing_attempt = next(
                    (
                        row
                        for row in reversed(document["scan_attempts"])
                        if str(row.get("scanner_submission_id") or "") == submission_id
                    ),
                    None,
                )
                if existing_attempt is not None:
                    return deepcopy(existing_attempt)
            form = next((row for row in document["forms"] if normalize_mrs_control_number(row.get("control_number")) == control), None)
            if form is None:
                validity_key = "unknown"
                analysis_key = "excluded"
            elif bool(form.get("valid_response_received")) and validity_key == "valid":
                validity_key = "duplicate"
                analysis_key = "excluded"
                invalid_reason = invalid_reason or "Control number already has an accepted valid response"
            elif str(form.get("current_print_status") or "") in {"Print Failed", "Generated", "Print Submitted", "Reprint Submitted"} and validity_key == "valid":
                validity_key = "held"
                analysis_key = "pending"
                invalid_reason = invalid_reason or "No later confirmed successful print attempt exists"
            attempt_id = self._next_internal_id(document["scan_attempts"], "MRS-SA", _coerce_datetime(scanned_at).year, digits=6)
            attempt = {
                "id": str(uuid4()),
                "scan_attempt_id": attempt_id,
                "control_number": control,
                "registered": form is not None,
                "scanner_submission_id": submission_id,
                "scanner_operator_user_id": str(operator.get("user_id") or ""),
                "scanner_operator_username": str(operator.get("username") or ""),
                "scanner_operator_display_name": str(operator.get("display_name") or ""),
                "scanned_at": stamp,
                "barcode_status": barcode.replace("_", " ").title(),
                "response_validity": validity_key.title(),
                "invalid_reason": str(invalid_reason or "")[:500],
                "analysis_status": analysis_key.replace("_", " ").title(),
                "response_record_id": str(response_record_id or ""),
                "print_attempt_reference": str(print_attempt_reference or ""),
                "remarks": str(remarks or "")[:1000],
                "image_sha256": str(image_sha256 or "").strip().casefold()[:128],
            }
            document["scan_attempts"].append(attempt)
            if form is not None:
                form["last_date_scanned"] = stamp
                form["current_font_state"] = "regular"
                form["printed_copy_status"] = "Accounted"
                form["current_scan_status"] = attempt["response_validity"]
                form["analysis_status"] = attempt["analysis_status"]
                if validity_key == "valid":
                    form["valid_response_received"] = True
                    form["accepted_response_record_id"] = str(response_record_id or "")
                    form["pending_reprint"] = False
                    form["reprint_reason"] = ""
                    form["reprint_queued_at"] = ""
                elif validity_key == "invalid":
                    form["number_of_invalid_scans"] = int(form.get("number_of_invalid_scans") or 0) + 1
                    if barcode == "crossed_out":
                        form["pending_reprint"] = True
                        form["current_print_status"] = "Pending Reprint"
                        form["reprint_reason"] = invalid_reason or "Respondent mistake or spoiled form"
                        form["reprint_queued_at"] = stamp
                form["updated_at"] = _utc_now()
            self._write_document(document)
            return deepcopy(attempt)

    def attach_response_record(self, scanner_submission_id: str, response_record_id: str) -> dict[str, Any] | None:
        wanted = str(scanner_submission_id or "").strip()
        if not wanted:
            return None
        with self._lock:
            document = self._read_document()
            for attempt in reversed(document["scan_attempts"]):
                if str(attempt.get("scanner_submission_id") or "") == wanted:
                    attempt["response_record_id"] = str(response_record_id or "")
                    control = normalize_mrs_control_number(attempt.get("control_number"))
                    form = next((row for row in document["forms"] if normalize_mrs_control_number(row.get("control_number")) == control), None)
                    if form is not None and str(attempt.get("response_validity") or "").casefold() == "valid":
                        form["accepted_response_record_id"] = str(response_record_id or "")
                    self._write_document(document)
                    return deepcopy(attempt)
        return None

    def stats(self) -> dict[str, int]:
        forms = self.list_forms()
        attempts = self.list_scan_attempts()
        return {
            "generated": len(forms),
            "printed": sum(1 for row in forms if row.get("current_print_status") == "Printed"),
            "print_failed": sum(1 for row in forms if row.get("current_print_status") == "Print Failed"),
            "pending_reprint": sum(1 for row in forms if row.get("pending_reprint")),
            "awaiting_scan": sum(1 for row in forms if row.get("current_font_state") == "red"),
            "scanned_valid": sum(1 for row in attempts if str(row.get("response_validity") or "").casefold() == "valid"),
            "scanned_invalid": sum(1 for row in attempts if str(row.get("response_validity") or "").casefold() == "invalid"),
            "duplicates": sum(1 for row in attempts if str(row.get("response_validity") or "").casefold() == "duplicate"),
            "unknown": sum(1 for row in attempts if not bool(row.get("registered"))),
            "included": sum(1 for row in attempts if str(row.get("analysis_status") or "").casefold() == "included"),
            "excluded": sum(1 for row in attempts if str(row.get("analysis_status") or "").casefold() == "excluded"),
        }

    def _find_form(self, document: Mapping[str, Any], control: str) -> dict[str, Any]:
        for row in document["forms"]:
            if normalize_mrs_control_number(row.get("control_number")) == control:
                return row
        raise MRSRegistryError(f"MRS control number is not registered: {control or 'blank'}")

    @staticmethod
    def _find_batch(document: Mapping[str, Any], batch_id: str) -> dict[str, Any]:
        for row in document["print_batches"]:
            if str(row.get("print_batch_id") or "") == str(batch_id or ""):
                return row
        raise MRSRegistryError(f"MRS print batch not found: {batch_id}")

    @staticmethod
    def _highest_sequence(forms: Sequence[Mapping[str, Any]], school_id: str, year: int) -> int:
        highest = 0
        for row in forms:
            parsed = parse_mrs_control_number(row.get("control_number"))
            if parsed and parsed["school_id"] == school_id and parsed["year"] == year:
                highest = max(highest, int(parsed["sequence"]))
        return highest

    @staticmethod
    def _next_internal_id(rows: Sequence[Mapping[str, Any]], prefix: str, year: int, *, digits: int) -> str:
        pattern = re.compile(rf"^{re.escape(prefix)}-{year:04d}-(\d{{{digits}}})$")
        highest = 0
        key_name = {"MRS-PB": "print_batch_id", "MRS-PA": "print_attempt_id", "MRS-SA": "scan_attempt_id"}.get(prefix, "id")
        for row in rows:
            match = pattern.fullmatch(str(row.get(key_name) or ""))
            if match:
                highest = max(highest, int(match.group(1)))
        return f"{prefix}-{year:04d}-{highest + 1:0{digits}d}"

    def _read_document(self) -> dict[str, Any]:
        self.last_read_error = None
        empty = {
            "file_type": self.FILE_TYPE,
            "schema_version": self.SCHEMA_VERSION,
            "forms": [],
            "print_attempts": [],
            "print_batches": [],
            "scan_attempts": [],
        }
        if not self.path.is_file():
            return empty
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._set_read_error(str(exc))
            return empty
        if not isinstance(loaded, Mapping):
            self._set_read_error("Registry root is not an object.")
            return empty
        result = deepcopy(empty)
        for key in ("forms", "print_attempts", "print_batches", "scan_attempts"):
            value = loaded.get(key)
            if not isinstance(value, list):
                self._set_read_error(f"Registry value '{key}' is not a list.")
                return empty
            result[key] = [deepcopy(row) for row in value if isinstance(row, Mapping)]
        return result

    def _write_document(self, document: Mapping[str, Any]) -> None:
        self._raise_if_corrupt()
        payload = deepcopy(dict(document))
        payload["file_type"] = self.FILE_TYPE
        payload["schema_version"] = self.SCHEMA_VERSION
        try:
            atomic_write_json(self.path, payload)
        except (OSError, TypeError, ValueError) as exc:
            raise MRSRegistryError(
                f"Unable to save the MRS registry to {self.path}: {exc}"
            ) from exc

    def _raise_if_corrupt(self) -> None:
        if self.path.exists() and self.last_read_error:
            raise MRSRegistryError(
                "The existing MRS registry could not be read and was left unchanged. "
                "A recovery copy was preserved."
            )

    def _set_read_error(self, message: str) -> None:
        self.last_read_error = str(message)
        self.last_recovery_copy = preserve_corrupt_file(self.path)
