from __future__ import annotations

from pathlib import Path

from school_csm_control_center.storage.survey_store import SurveyStore


def record(control: str, day: str):
    return {
        "control_number": control,
        "source_code": "SCC",
        "mode": "onsite",
        "survey_date": day,
        "meta": {"client_type": "Citizen", "service_availed": []},
        "cc": {},
        "sqd": {},
        "feedback": {},
    }


def test_delete_date_range_is_inclusive_and_atomic(tmp_path: Path):
    store = SurveyStore(tmp_path)
    store.add(record("A", "2026-06-30"))
    store.add(record("B", "2026-07-01"))
    store.add(record("C", "2026-07-31"))
    store.add(record("D", "2026-08-01"))
    result = store.delete_date_range("2026-07-01", "2026-07-31")
    assert result["removed_count"] == 2
    assert {item["control_number"] for item in store.list()} == {"A", "D"}


def test_delete_date_range_reverses_bounds(tmp_path: Path):
    store = SurveyStore(tmp_path)
    store.add(record("A", "2026-07-10"))
    result = store.delete_date_range("2026-07-31", "2026-07-01")
    assert result["removed_count"] == 1


def test_clear_all_records(tmp_path: Path):
    store = SurveyStore(tmp_path)
    store.add(record("A", "2026-07-10"))
    store.add(record("B", "2026-08-10"))
    assert store.delete_date_range()["removed_count"] == 2
    assert store.list() == []
