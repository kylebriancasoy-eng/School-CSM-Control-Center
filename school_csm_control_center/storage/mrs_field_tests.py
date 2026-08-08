"""Persistent administrative MRS field-test results.

Field-test scans exercise the real scanner pipeline but never create an official
survey response, consume a public control number, or affect CSM analysis.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Mapping

from school_csm_control_center.runtime_paths import storage_root_for
from school_csm_control_center.storage.file_safety import (
    InterProcessRLock,
    atomic_write_json,
    preserve_corrupt_file,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class MRSFieldTestStore:
    FILE_TYPE = "School CSM Control Center MRS Field Tests"
    SCHEMA_VERSION = "1.0"

    def __init__(
        self,
        project_root: str | Path,
        *,
        data_root: str | Path | None = None,
    ) -> None:
        self.install_root = Path(project_root)
        self.project_root = storage_root_for(project_root, data_root)
        self.path = self.project_root / "data" / "csm_survey" / "mrs_field_tests.mossjson"
        self._lock = InterProcessRLock(self.path)
        self.last_read_error: str | None = None
        self.last_recovery_copy: Path | None = None

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = [deepcopy(row) for row in self._read()["tests"] if isinstance(row, dict)]
        rows.sort(key=lambda row: str(row.get("recorded_at") or ""), reverse=True)
        return rows

    def get(self, test_result_id: str) -> dict[str, Any] | None:
        wanted = str(test_result_id or "").strip()
        if not wanted:
            return None
        with self._lock:
            for row in self._read()["tests"]:
                if str(row.get("test_result_id") or "") == wanted:
                    return deepcopy(row)
        return None

    def record(
        self,
        *,
        context: Mapping[str, Any],
        operator: Mapping[str, Any],
        session_id: str,
        notes: str = "",
        test_context: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        scanner_job = context.get("scanner_job") if isinstance(context.get("scanner_job"), Mapping) else {}
        recognition = context.get("recognition") if isinstance(context.get("recognition"), Mapping) else {}
        template = context.get("template") if isinstance(context.get("template"), Mapping) else {}
        responses = context.get("responses") if isinstance(context.get("responses"), Mapping) else {}
        test_meta = dict(test_context or {})
        outcome = str(test_meta.get("outcome") or "needs_review").strip().casefold().replace(" ", "_")
        if outcome not in {"pass", "needs_calibration", "fail", "needs_review"}:
            outcome = "needs_review"
        with self._lock:
            document = self._read()
            self._raise_if_corrupt()
            sequence = int(document.get("next_sequence") or 1)
            year = datetime.now(timezone.utc).year
            test_result_id = f"MRS-FT-{year}-{sequence:05d}"
            document["next_sequence"] = sequence + 1
            row = {
                "test_result_id": test_result_id,
                "recorded_at": _utc_now(),
                "scanner_job_id": str(context.get("scanner_job_id") or scanner_job.get("job_id") or ""),
                "scanner_session_id": str(session_id or scanner_job.get("capture_session_id") or ""),
                "operator_user_id": str(operator.get("user_id") or ""),
                "operator_username": str(operator.get("username") or ""),
                "operator_display_name": str(operator.get("display_name") or ""),
                "template_id": str(template.get("template_id") or ""),
                "language": str(template.get("language") or ""),
                "engine_version": str(recognition.get("engine_version") or ""),
                "coordinate_map_version": str(template.get("coordinate_map_version") or ""),
                "recognized_control_number": str(responses.get("control_number") or ""),
                "barcode_status": str(context.get("barcode_status") or responses.get("barcode_status") or ""),
                "overall_confidence": recognition.get("overall_confidence"),
                "detected_fields": recognition.get("detected_fields"),
                "ambiguous_fields": recognition.get("ambiguous_fields"),
                "blank_fields": recognition.get("blank_fields"),
                "image_sha256": str(scanner_job.get("image_sha256") or ""),
                "source_path": str(scanner_job.get("source_path") or scanner_job.get("original_path") or ""),
                "canonical_path": str(scanner_job.get("canonical_path") or ""),
                "corrected_path": str(scanner_job.get("corrected_path") or ""),
                "responses": deepcopy(dict(responses)),
                "recognition": deepcopy(dict(recognition)),
                "printer_or_source": " ".join(str(test_meta.get("printer_or_source") or "").split())[:200],
                "device": " ".join(str(test_meta.get("device") or "").split())[:300],
                "capture_condition": " ".join(str(test_meta.get("capture_condition") or "").split())[:120],
                "expected_result": " ".join(str(test_meta.get("expected_result") or "").split())[:1000],
                "outcome": outcome,
                "notes": " ".join(str(test_meta.get("notes") or notes or "").split())[:2000],
                "analysis_status": "excluded_test",
                "official_response_created": False,
            }
            document["tests"].append(row)
            self._write(document)
            return deepcopy(row)

    def summary(self) -> dict[str, Any]:
        rows = self.list()
        languages: dict[str, int] = {}
        confidence_values: list[float] = []
        outcomes: dict[str, int] = {}
        for row in rows:
            language = str(row.get("language") or "Unknown")
            languages[language] = languages.get(language, 0) + 1
            outcome = str(row.get("outcome") or "needs_review")
            outcomes[outcome] = outcomes.get(outcome, 0) + 1
            try:
                confidence_values.append(float(row.get("overall_confidence")))
            except (TypeError, ValueError):
                pass
        return {
            "total": len(rows),
            "languages": languages,
            "outcomes": outcomes,
            "average_confidence": (
                round(sum(confidence_values) / len(confidence_values), 6) if confidence_values else None
            ),
            "latest": rows[0] if rows else None,
        }

    def _read(self) -> dict[str, Any]:
        self.last_read_error = None
        default = {
            "file_type": self.FILE_TYPE,
            "schema_version": self.SCHEMA_VERSION,
            "next_sequence": 1,
            "tests": [],
        }
        if not self.path.is_file():
            return default
        try:
            parsed = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._set_read_error(str(exc))
            return default
        if not isinstance(parsed, dict):
            self._set_read_error("The MRS field-test file root is not an object.")
            return default
        tests = parsed.get("tests")
        if not isinstance(tests, list):
            self._set_read_error("The MRS field-test records value is not a list.")
            return default
        parsed["tests"] = tests
        parsed["file_type"] = self.FILE_TYPE
        parsed["schema_version"] = self.SCHEMA_VERSION
        parsed["next_sequence"] = max(1, int(parsed.get("next_sequence") or 1))
        return parsed

    def _write(self, document: Mapping[str, Any]) -> None:
        self._raise_if_corrupt()
        atomic_write_json(self.path, dict(document))

    def _raise_if_corrupt(self) -> None:
        if self.path.exists() and self.last_read_error:
            raise RuntimeError(
                "The existing MRS field-test file could not be read and was left unchanged. "
                "A recovery copy was preserved."
            )

    def _set_read_error(self, message: str) -> None:
        self.last_read_error = str(message)
        self.last_recovery_copy = preserve_corrupt_file(self.path)
