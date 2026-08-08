"""CSV survey-history import support for Control Center exports and legacy files."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from school_csm_control_center.demographics import normalize_demographics
from school_csm_control_center.school_services import normalize_service_values


SUPPORTED_SOURCE_CODES = {"WBS", "MRS", "SCC"}


@dataclass(frozen=True)
class ParsedSurveyCsv:
    path: Path
    records: tuple[dict[str, Any], ...]
    errors: tuple[str, ...]
    headers: tuple[str, ...]

    @property
    def valid_count(self) -> int:
        return len(self.records)

    @property
    def error_count(self) -> int:
        return len(self.errors)


def parse_survey_history_csv(path: str | Path) -> ParsedSurveyCsv:
    """Parse a Control Center or legacy DepEd CSMS history CSV.

    Calculated columns such as positive rate and performance band are ignored;
    the Control Center recomputes them from the imported responses.
    """

    csv_path = Path(path)
    records: list[dict[str, Any]] = []
    errors: list[str] = []
    try:
        stream = csv_path.open("r", encoding="utf-8-sig", newline="")
    except OSError as exc:
        raise ValueError(f"Unable to open CSV file: {exc}") from exc

    with stream:
        reader = csv.DictReader(stream)
        headers = tuple(str(item or "").strip() for item in (reader.fieldnames or []))
        required_any = {"survey_date", "created_at", "control_number"}
        if not headers:
            raise ValueError("The selected CSV file has no column headers.")
        if not required_any.intersection(headers):
            raise ValueError(
                "The selected CSV is not a recognized CSM survey-history file."
            )
        for row_number, raw in enumerate(reader, start=2):
            try:
                records.append(_row_to_record(raw, csv_path.name, row_number))
            except ValueError as exc:
                errors.append(f"Row {row_number}: {exc}")

    return ParsedSurveyCsv(csv_path, tuple(records), tuple(errors), headers)


def _row_to_record(raw: dict[str, Any], file_name: str, row_number: int) -> dict[str, Any]:
    row = {str(key or "").strip(): _clean(value) for key, value in raw.items()}
    mode = (row.get("mode") or "onsite").casefold()
    if mode not in {"onsite", "online"}:
        raise ValueError(f"unsupported survey mode {row.get('mode')!r}")

    source_code = (row.get("source_code") or "SCC").upper()
    if source_code not in SUPPORTED_SOURCE_CODES:
        source_code = "SCC"

    meta: dict[str, Any] = {
        "client_type": row.get("client_type"),
        "sex": row.get("sex"),
        "region": row.get("region"),
        "age_bracket": row.get("age_bracket"),
        "agency_visited": row.get("agency_visited"),
        "service_availed": _services(row.get("service_availed")),
        "imported_from_csv": True,
        "import_source_file": file_name,
        "import_source_row": row_number,
        "import_original_record_id": row.get("id"),
        "import_original_created_at": row.get("created_at"),
    }
    scanner_aliases = {
        "scanned_by": "scanner_operator_display_name",
        "scanner_username": "scanner_operator_username",
        "scanner_user_id": "scanner_operator_user_id",
        "scanner_session_id": "scanner_session_id",
        "scanner_job_id": "scanner_job_id",
        "source_form_control_number": "source_form_control_number",
    }
    for source, target in scanner_aliases.items():
        if row.get(source):
            meta[target] = row[source]
    age = row.get("age")
    if age:
        meta["age"] = age
    try:
        meta = normalize_demographics(meta, strict=True)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc
    for key in list(meta):
        if meta[key] in (None, "", []):
            meta.pop(key)

    cc = {
        key: _numeric_or_text(row.get(key))
        for key in ("cc1", "cc2", "cc3")
        if row.get(key) != ""
    }
    if row.get("cc3_reason"):
        cc["cc3_reason"] = row["cc3_reason"]

    sqd = {
        f"sqd{number}": _rating(row.get(f"sqd{number}"))
        for number in range(9)
        if row.get(f"sqd{number}") != ""
    }

    payload: dict[str, Any] = {
        "id": row.get("id"),
        "created_at": row.get("created_at"),
        "control_number": row.get("control_number"),
        "source_code": source_code,
        "mode": mode,
        "survey_date": row.get("survey_date"),
        "meta": meta,
        "cc": cc,
        "sqd": sqd,
        "feedback": {
            "comments": row.get("feedback"),
            "email": row.get("email") if mode == "onsite" else "",
        },
    }
    return payload


def _clean(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.casefold() in {"nan", "none", "null"} else text


def _services(value: str) -> list[str]:
    if not value:
        return []
    return normalize_service_values(
        [item.strip() for item in value.split(";") if item.strip()]
    )


def _numeric_or_text(value: str) -> Any:
    if not value:
        return None
    try:
        number = float(value)
    except ValueError:
        return value
    return int(number) if number.is_integer() else number


def _rating(value: str) -> Any:
    text = _clean(value)
    if not text:
        return None
    if text.casefold() in {"n/a", "na", "not applicable"}:
        return "N/A"
    return _numeric_or_text(text)
