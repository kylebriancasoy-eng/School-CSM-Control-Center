from __future__ import annotations

import unittest

from school_csm_control_center.services.analysis_service import AnalysisService


class SourceAndOperatorFilterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.records = [
            {
                "id": "1",
                "control_number": "CSM-WBS-123627-2026-07-0001",
                "source_code": "WBS",
                "mode": "onsite",
                "meta": {"submission_source": "local_web_form", "service_availed": ["Enrollment (Walk-in)"]},
                "cc": {},
                "sqd": {},
                "feedback": {},
            },
            {
                "id": "2",
                "control_number": "CSM-MRS-123627-2026-07-0001",
                "source_code": "MRS",
                "mode": "onsite",
                "meta": {
                    "submission_source": "scanned_hardcopy",
                    "scanner_operator_user_id": "SCOP-0007",
                    "scanner_operator_display_name": "Juan Dela Cruz",
                    "service_availed": ["Enrollment (Walk-in)"],
                },
                "cc": {},
                "sqd": {},
                "feedback": {},
            },
        ]

    def test_source_filter(self) -> None:
        result = AnalysisService.filter_records(self.records, {"source_code": "MRS"})
        self.assertEqual([item["id"] for item in result], ["2"])

    def test_scanner_operator_filter(self) -> None:
        result = AnalysisService.filter_records(self.records, {"scanner_operator": "SCOP-0007"})
        self.assertEqual([item["id"] for item in result], ["2"])


if __name__ == "__main__":
    unittest.main()
