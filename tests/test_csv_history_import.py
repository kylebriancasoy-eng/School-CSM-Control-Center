from __future__ import annotations

import csv
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from school_csm_control_center.storage.csv_import import parse_survey_history_csv
from school_csm_control_center.storage.survey_store import SurveyStore


class CsvHistoryImportTests(unittest.TestCase):
    def test_legacy_dep_ed_history_is_imported_and_recomputed(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            csv_path = root / "history.csv"
            fields = [
                "id", "control_number", "mode", "survey_date", "created_at",
                "client_type", "sex", "age", "region", "agency_visited",
                "service_availed", "cc1", "cc2", "cc3", "cc3_reason",
                *[f"sqd{number}" for number in range(9)],
                "overall_positive_rate", "performance_band", "feedback", "email",
            ]
            with csv_path.open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=fields)
                writer.writeheader()
                writer.writerow({
                    "id": "5d09864d-bb1e-4b74-af39-b0da2d8a9686",
                    "control_number": "2026-05-0003",
                    "mode": "onsite",
                    "survey_date": "2026-05-27",
                    "created_at": "2026-07-23T14:11:19.366865Z",
                    "client_type": "Citizen",
                    "sex": "Female",
                    "age": "28.0",
                    "region": "Eastern Visayas",
                    "agency_visited": "Calapi Elementary School",
                    "service_availed": "Enrollment (Walk-in); Transfer",
                    "cc1": "1.0", "cc2": "1.0", "cc3": "1.0",
                    "sqd0": "5.0", "sqd1": "4", "sqd2": "4",
                    "sqd3": "4.0", "sqd4": "4.0", "sqd5": "N/A",
                    "sqd6": "5.0", "sqd7": "5", "sqd8": "5",
                    "overall_positive_rate": "100.0",
                    "performance_band": "Outstanding",
                })

            parsed = parse_survey_history_csv(csv_path)
            self.assertEqual(parsed.valid_count, 1)
            self.assertEqual(parsed.error_count, 0)
            store = SurveyStore(root)
            result = store.import_records(parsed.records)
            self.assertEqual(result["imported_count"], 1)
            record = store.list()[0]
            self.assertEqual(record["id"], "5d09864d-bb1e-4b74-af39-b0da2d8a9686")
            self.assertEqual(record["control_number"], "2026-05-0003")
            self.assertEqual(record["survey_date"], "2026-05-27")
            self.assertEqual(record["source_code"], "SCC")
            self.assertEqual(record["meta"]["age"], 28)
            self.assertEqual(record["meta"]["service_availed"], ["Enrollment (Walk-in)", "Transfer"])
            self.assertEqual(record["sqd"]["sqd5"], 0)
            self.assertNotIn("overall_positive_rate", record)
            self.assertNotIn("performance_band", record)

            repeated = store.import_records(parsed.records)
            self.assertEqual(repeated["imported_count"], 0)
            self.assertEqual(repeated["skipped_count"], 1)


if __name__ == "__main__":
    unittest.main()
