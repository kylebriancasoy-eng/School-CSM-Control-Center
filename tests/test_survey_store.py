from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
import tempfile
import unittest
from uuid import UUID

from school_csm_control_center.storage.survey_store import (
    DuplicateControlNumberError,
    SurveyStore,
    SurveyStoreError,
)


def onsite_record(control_number: str = "CTRL-001") -> dict:
    return {
        "control_number": control_number,
        "mode": "onsite",
        "survey_date": "2026-07-17",
        "meta": {
            "client_type": "Citizen",
            "sex": "Female",
            "age": 31,
            "region": "Region VIII",
            "service_availed": "Enrollment",
            "email": "client@example.com",
        },
        "cc": {"cc1": 4},
        "sqd": {
            "sqd0": 5,
            "sqd1": 4,
            "sqd2": 5,
            "sqd3": 3,
            "sqd4": 0,
            "sqd5": None,
            "sqd6": 4,
            "sqd7": 5,
            "sqd8": 4,
        },
        "feedback": {"suggestions": "Keep the signs clear."},
    }


class SurveyStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.project_root = Path(self.temporary.name)
        self.store = SurveyStore(self.project_root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_add_generates_uuid_timestamps_and_applies_onsite_branching(self) -> None:
        saved = self.store.add(onsite_record())
        UUID(saved["id"])
        self.assertTrue(saved["created_at"].endswith("Z"))
        self.assertEqual(saved["created_at"], saved["updated_at"])
        self.assertEqual(saved["meta"]["service_availed"], ["Enrollment"])
        self.assertEqual(saved["cc"], {"cc1": 4, "cc2": 5, "cc3": 4})
        self.assertEqual(saved["sqd"]["sqd4"], 0)
        self.assertIsNone(saved["sqd"]["sqd5"])
        self.assertEqual(
            saved["feedback"],
            {"comments": "Keep the signs clear.", "email": "client@example.com"},
        )
        self.assertNotIn("email", saved["meta"])
        self.assertEqual(self.store.list()[0], saved)

        document = json.loads(self.store.path.read_text(encoding="utf-8"))
        self.assertEqual(document["schema_version"], "2.0")
        self.assertEqual(len(document["records"]), 1)
        self.assertFalse(list(self.store.path.parent.glob("*.tmp")))

    def test_online_record_retains_date_but_has_no_sqd0_or_skipped_cc_answers(self) -> None:
        saved = self.store.add(
            {
                "mode": "online",
                "survey_date": "2026-07-17",
                "meta": {"service_availed": "Records Request"},
                "cc": {"cc1": 3, "cc2": 1, "cc3": 1},
                "sqd": {f"sqd{number}": 5 for number in range(1, 9)},
                "feedback": {"comments": "Fast service."},
            }
        )
        self.assertEqual(saved["survey_date"], "2026-07-17")
        self.assertNotIn("sqd0", saved["sqd"])
        self.assertEqual(saved["meta"]["service_availed"], ["Records Request"])
        self.assertEqual(saved["cc"], {"cc1": 3})
        self.assertEqual(saved["feedback"], {"comments": "Fast service.", "email": ""})

    def test_duplicate_nonempty_control_number_is_case_and_space_insensitive(self) -> None:
        self.store.add(onsite_record(" AbC  100 "))
        with self.assertRaises(DuplicateControlNumberError):
            self.store.add(onsite_record("abc 100"))
        self.store.add(onsite_record(""))
        self.store.add(onsite_record("   "))
        self.assertEqual(len(self.store.list()), 3)

    def test_next_control_number_uses_monthly_shared_format_and_mrs_identity(self) -> None:
        self.assertEqual(
            self.store.next_control_number("2026-07-17", "123627", "WBS"),
            "2026-07-0001",
        )
        july = onsite_record("2026-07-0001")
        july["source_code"] = "WBS"
        self.store.add(july)
        august = onsite_record("2026-08-0007")
        august["source_code"] = "WBS"
        august["survey_date"] = "2026-08-01"
        self.store.add(august)

        self.assertEqual(
            self.store.next_control_number(date(2026, 9, 30), "123627", "WBS"),
            "2026-09-0001",
        )
        self.assertEqual(
            self.store.next_control_number(datetime(2026, 10, 2, 15, 30), "123627", "MRS"),
            "CSM-MRS-123627-2026-10-0001",
        )
        self.assertEqual(
            self.store.next_control_number("2027-01-01", "123627", "WBS"),
            "2027-01-0001",
        )

    def test_browser_and_manual_sources_share_monthly_sequence(self) -> None:
        expected = {
            "WBS": "2026-07-0001",
            "MRS": "CSM-MRS-123627-2026-07-0001",
            "SCC": "2026-07-0002",
        }
        for source in ("WBS", "MRS", "SCC"):
            saved = self.store.add_with_next_control_number(
                {**onsite_record(""), "source_code": source},
                "2026-07-17",
                "123627",
                source,
            )
            self.assertEqual(saved["control_number"], expected[source])

    def test_next_control_number_ignores_legacy_and_malformed_values(self) -> None:
        for control_number in (
            "MANUAL-99",
            "2026-07-0099",
            "CSM-WBS-123627-2026-7-0099",
            "CSM-WBS-123627-2026-07-99",
            "CSM-WBS-123627-2026-07-000A",
            "CSM-WBS-999999-2026-07-0999",
            "CSM-MRS-123627-2026-07-0999",
            "2026-08-0003",
        ):
            self.store.add(onsite_record(control_number))
        record = onsite_record("CSM-WBS-123627-2026-07-0003")
        record["source_code"] = "WBS"
        self.store.add(record)

        self.assertEqual(
            self.store.next_control_number("2026-08-17", "123627", "WBS"),
            "2026-08-0004",
        )

    def test_next_control_number_requires_valid_date_mrs_school_id_and_source(self) -> None:
        for invalid in (None, "", "17-07-2026", "2026-02-30"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.store.next_control_number(invalid, "123627", "SCC")
        self.assertEqual(
            self.store.next_control_number("2026-07-17", "", "SCC"),
            "2026-07-0001",
        )
        with self.assertRaises(ValueError):
            self.store.next_control_number("2026-07-17", "", "MRS")
        with self.assertRaises(ValueError):
            self.store.next_control_number("2026-07-17", "123627", "OTHER")

    def test_update_merges_nested_values_preserves_identity_and_checks_duplicates(self) -> None:
        first = self.store.add(onsite_record("ONE"))
        second = self.store.add(onsite_record("TWO"))
        updated = self.store.update(
            first["id"],
            {
                "meta": {"service_availed": "Certification"},
                "sqd": {"sqd1": 5},
                "feedback": "Updated comment",
            },
        )
        self.assertEqual(updated["id"], first["id"])
        self.assertEqual(updated["created_at"], first["created_at"])
        self.assertNotEqual(updated["updated_at"], first["updated_at"])
        self.assertEqual(
            updated["meta"]["service_availed"],
            [
                "Issuance of Requested Documents in Certified True Copy (CTC) "
                "and Photocopy (Walk-in)"
            ],
        )
        self.assertEqual(updated["sqd"]["sqd1"], 5)
        self.assertEqual(updated["sqd"]["sqd2"], 5)
        with self.assertRaises(DuplicateControlNumberError):
            self.store.update(first["id"], {"control_number": second["control_number"]})
        with self.assertRaises(KeyError):
            self.store.update("missing", {})

    def test_scanner_operator_attribution_is_immutable_on_record_update(self) -> None:
        record = onsite_record("CSM-MRS-123627-2026-07-0001")
        record["source_code"] = "MRS"
        record["meta"].update({
            "scanner_operator_user_id": "SCOP-0001",
            "scanner_operator_username": "scanner01",
            "scanner_operator_display_name": "Juan Dela Cruz",
            "scanner_session_id": "SCNS-ORIGINAL",
            "scanner_submission_id": "SCN-ORIGINAL",
        })
        saved = self.store.add(record)
        updated = self.store.update(saved["id"], {
            "meta": {
                "scanner_operator_user_id": "SCOP-9999",
                "scanner_operator_username": "other",
                "scanner_operator_display_name": "Other Operator",
                "scanner_session_id": "SCNS-CHANGED",
                "scanner_submission_id": "SCN-CHANGED",
            }
        })
        self.assertEqual(updated["meta"]["scanner_operator_user_id"], "SCOP-0001")
        self.assertEqual(updated["meta"]["scanner_operator_display_name"], "Juan Dela Cruz")
        self.assertEqual(updated["meta"]["scanner_session_id"], "SCNS-ORIGINAL")
        self.assertEqual(updated["meta"]["scanner_submission_id"], "SCN-ORIGINAL")

    def test_delete_returns_status_and_persists(self) -> None:
        saved = self.store.add(onsite_record())
        self.assertTrue(self.store.delete(saved["id"]))
        self.assertFalse(self.store.delete(saved["id"]))
        self.assertEqual(self.store.list(), [])

    def test_defensive_read_preserves_malformed_json_and_fails_closed(self) -> None:
        self.store.path.parent.mkdir(parents=True)
        self.store.path.write_text("{broken", encoding="utf-8")
        before = self.store.path.read_bytes()
        self.assertEqual(self.store.list(), [])
        self.assertIsNotNone(self.store.last_read_error)
        with self.assertRaisesRegex(SurveyStoreError, "left unchanged"):
            self.store.add(onsite_record())
        self.assertEqual(self.store.path.read_bytes(), before)
        self.assertIsNotNone(self.store.last_recovery_copy)
        self.assertTrue(self.store.last_recovery_copy.is_file())

    def test_validation_rejects_online_na_sqd0_and_invalid_cc(self) -> None:
        with self.assertRaises(ValueError):
            self.store.add({"mode": "online", "sqd": {"sqd1": 0}})
        with self.assertRaises(ValueError):
            self.store.add({"mode": "online", "sqd": {"sqd0": 5}})
        with self.assertRaises(ValueError):
            self.store.add({"mode": "onsite", "cc": {"cc1": 99}})

    def test_service_selection_normalizes_lists_and_rejects_non_text_entries(self) -> None:
        record = onsite_record()
        record["meta"]["service_availed"] = [
            "  Enrollment (Walk-in)  ",
            "Records   Request",
            "enrollment (walk-in)",
            "",
        ]
        saved = self.store.add(record)
        self.assertEqual(
            saved["meta"]["service_availed"],
            ["Enrollment (Walk-in)", "Records Request"],
        )

        invalid = onsite_record("CTRL-INVALID")
        invalid["meta"]["service_availed"] = ["Enrollment", 7]
        with self.assertRaisesRegex(
            ValueError,
            "Every selected school transaction must be text",
        ):
            self.store.add(invalid)

        non_list = onsite_record("CTRL-OBJECT")
        non_list["meta"]["service_availed"] = {"label": "Enrollment"}
        with self.assertRaisesRegex(ValueError, "must be text or a list"):
            self.store.add(non_list)


if __name__ == "__main__":
    unittest.main()
