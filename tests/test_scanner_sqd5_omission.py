from __future__ import annotations

from copy import deepcopy
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from school_csm_control_center.web_server.scanner_jobs import ScannerJobManager


ROOT = Path(__file__).resolve().parents[1]


def _recognized_fields(sqd5_value: str | None) -> dict[str, dict[str, object]]:
    values = {
        "age_bracket": "25_34",
        "sex": "1",
        "client_type": "1",
        "region": "region_08",
        "service_availed": "S03",
        "cc1": "1",
        "cc2": "1",
        "cc3": "1",
        **{f"sqd{number}": "5" for number in range(9)},
    }
    values["sqd5"] = sqd5_value
    fields: dict[str, dict[str, object]] = {}
    for field_name, value in values.items():
        options = {str(value): 1.0} if value is not None else {str(number): 0.0 for number in range(6)}
        fields[field_name] = {
            "value": value,
            "scores": options,
            "status": "detected" if value is not None else "blank",
            "confidence": 0.99 if value is not None else 0.0,
        }
    return fields


class ScannerSQD5OmissionTests(unittest.TestCase):
    def _finalize(self, sqd5_value: str | None) -> dict[str, object]:
        with TemporaryDirectory() as temporary:
            manager = ScannerJobManager(
                temporary,
                processing_limit_provider=lambda: 1,
                worker_count=1,
            )
            try:
                job_id = "SCNJ-2026-SQD5TEST0001"
                job = {
                    "job_id": job_id,
                    "status": "ready_for_review",
                    "operator_user_id": "SCOP-0001",
                    "recognition_available": True,
                    "test_mode": True,
                    "access_transport": "local",
                    "scanner_session_id": "SCNS-TEST",
                    "recognition": {
                        "fields": _recognized_fields(sqd5_value),
                        "barcode": {"value": "", "status": "Normal"},
                        "date_recognition": {"value": date.today().isoformat()},
                    },
                }
                with manager._lock:  # test-only deterministic recognized job
                    manager._jobs[job_id] = deepcopy(job)
                return manager.finalization_context(
                    job_id,
                    "SCOP-0001",
                    {
                        "operator_review_completed": True,
                        "barcode_status_confirmed": True,
                        "responses": {},
                        "manual": {"barcode_status": "Normal"},
                    },
                )
            finally:
                manager.close()

    def test_blank_sqd5_is_saved_as_na_without_operator_correction(self) -> None:
        context = self._finalize(None)
        self.assertEqual(context["responses"]["sqd5"], "0")

    def test_recognized_legacy_sqd5_answer_is_preserved(self) -> None:
        context = self._finalize("5")
        self.assertEqual(context["responses"]["sqd5"], "5")

    def test_remote_review_hides_only_a_blank_sqd5_result(self) -> None:
        html = (
            ROOT / "school_csm_control_center" / "web_server" / "static" / "scanner_remote.html"
        ).read_text(encoding="utf-8")
        self.assertIn("Legacy fees/costs item (excluded from current scoring).", html)
        self.assertIn("field==='sqd5'&&!String(result?.value??'').trim()", html)


if __name__ == "__main__":
    unittest.main()
