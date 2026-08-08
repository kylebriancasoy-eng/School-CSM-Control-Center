from __future__ import annotations

from copy import deepcopy
import unittest

from school_csm_control_center.services.analysis_service import AnalysisService, rating_label


def sample_records() -> list[dict]:
    return [
        {
            "id": "onsite-1",
            "control_number": "A-001",
            "mode": "onsite",
            "survey_date": "2026-06-15",
            "meta": {
                "client_type": "Citizen",
                "sex": "Female",
                "age": 31,
                "region": "Region VIII",
                "service_availed": "Enrollment",
            },
            "cc": {"cc1": 1, "cc2": 1, "cc3": 2},
            "sqd": {
                "sqd0": 5,
                "sqd1": 4,
                "sqd2": 5,
                "sqd3": 3,
                "sqd4": 0,
                "sqd5": None,
                "sqd6": 1,
                "sqd7": 2,
                "sqd8": 4,
            },
            "feedback": {"suggestions": "Keep the clear queue signs."},
            "created_at": "2026-06-15T08:00:00Z",
            "updated_at": "2026-06-15T08:00:00Z",
        },
        {
            "id": "online-1",
            "control_number": "",
            "mode": "online",
            "meta": {
                "client_type": "Business",
                "sex": "Male",
                "age": 47,
                "region": "Region VIII",
                "agency_visited": "Calapi Elementary School",
                "service_availed": "Records Request",
            },
            "cc": {"cc1": 3},
            "sqd": {
                "sqd1": 5,
                "sqd2": 4,
                "sqd3": 4,
                "sqd4": 3,
                "sqd5": 2,
                "sqd6": 5,
                "sqd7": 1,
                "sqd8": 5,
            },
            "feedback": {"comments": "Online support was prompt."},
            "created_at": "2026-07-02T09:00:00Z",
            "updated_at": "2026-07-02T09:00:00Z",
        },
    ]


class AnalysisServiceTests(unittest.TestCase):
    def test_required_performance_bands_have_exact_boundaries(self) -> None:
        cases = {
            None: "No Data",
            0: "Poor",
            59.99: "Poor",
            60: "Fair",
            79.99: "Fair",
            80: "Satisfactory",
            89.99: "Satisfactory",
            90: "Very Satisfactory",
            94.99: "Very Satisfactory",
            95: "Outstanding",
            100: "Outstanding",
        }
        for score, expected in cases.items():
            with self.subTest(score=score):
                self.assertEqual(rating_label(score), expected)
                self.assertEqual(AnalysisService.rating_label(score), expected)

    def test_overall_and_dimension_scoring_excludes_na_and_unanswered(self) -> None:
        analysis = AnalysisService.analyze(sample_records())
        overview = analysis["overview"]
        self.assertEqual(overview["total_responses"], 2)
        self.assertEqual(overview["valid_sqd_answers"], 14)
        self.assertEqual(overview["positive_sqd_answers"], 8)
        self.assertEqual(overview["positive_rate"], 57.14)
        self.assertEqual(overview["rating"], "Poor")
        self.assertEqual(overview["sqd0"]["positive_rate"], 100.0)

        sqd1 = next(row for row in analysis["dimensions"] if row["key"] == "sqd1")
        self.assertEqual(sqd1["valid_responses"], 2)
        self.assertEqual(sqd1["positive_rate"], 100.0)
        self.assertEqual(sqd1["dimension"], "Responsiveness")

        mix = analysis["response_mix"]
        self.assertEqual(mix["not_applicable"], 1)
        self.assertEqual(mix["unanswered"], 1)
        self.assertEqual(mix["answered_count"], 15)
        self.assertEqual(mix["answer_coverage_rate"], 93.75)
        self.assertEqual(mix["scorable_rate"], 87.5)
        self.assertEqual(mix["completion_rate"], 93.75)
        self.assertEqual(mix["unanswered_rate"], 6.25)

        sqd2 = next(row for row in analysis["dimensions"] if row["key"] == "sqd2")
        self.assertEqual(sqd1["rank"], sqd2["rank"])
        self.assertEqual(sqd1["rank"], 1)

    def test_output_has_predictable_dashboard_sections_and_live_breakdowns(self) -> None:
        analysis = AnalysisService.analyze(sample_records())
        self.assertTrue(
            {
                "overview",
                "dimensions",
                "response_mix",
                "trend",
                "citizen_charter",
                "demographics",
                "services",
                "insights",
                "feedback",
                "recent",
            }.issubset(analysis)
        )
        self.assertEqual([row["period"] for row in analysis["trend"]], ["2026-06", "2026-07"])
        self.assertEqual(analysis["citizen_charter"]["awareness_rate"], 50.0)
        self.assertEqual(analysis["citizen_charter"]["awareness_positive"], 1)
        self.assertEqual(analysis["citizen_charter"]["awareness_valid"], 2)
        self.assertEqual(len(analysis["services"]), 2)
        self.assertEqual({row["label"] for row in analysis["demographics"]["sex"]}, {"Female", "Male"})
        self.assertEqual(len(analysis["feedback"]), 2)
        self.assertEqual(analysis["feedback"][0]["text"], "Online support was prompt.")
        self.assertTrue(analysis["insights"])
        self.assertEqual(len(analysis["recent"]), 2)
        self.assertEqual(analysis["overview"]["feedback_rate"], 100.0)
        self.assertEqual(analysis["overview"]["answer_coverage_rate"], 93.75)
        self.assertEqual(
            analysis["overview"]["next_band"],
            {"label": "Fair", "threshold": 60.0, "points_needed": 2.86},
        )
        latest_trend = analysis["trend"][-1]
        self.assertEqual(latest_trend["valid_responses"], 8)
        self.assertEqual(latest_trend["positive_responses"], 5)

    def test_filter_records_supports_mode_service_date_and_demographics(self) -> None:
        records = sample_records()
        self.assertEqual(
            len(AnalysisService.filter_records(records, {"mode": "online"})),
            1,
        )
        self.assertEqual(
            len(AnalysisService.filter_records(records, {"service": "Enrollment"})),
            1,
        )
        self.assertEqual(
            len(AnalysisService.filter_records(records, {"date_from": "2026-07-01"})),
            1,
        )
        self.assertEqual(
            len(AnalysisService.filter_records(records, {"age_group": "20_34", "sex": "female"})),
            1,
        )
        filtered_analysis = AnalysisService.analyze(records, {"mode": "online"})
        self.assertEqual(filtered_analysis["overview"]["total_responses"], 1)
        self.assertEqual(filtered_analysis["overview"]["online_responses"], 1)

    def test_service_analysis_supports_legacy_scalar_and_multi_select_lists(self) -> None:
        records = sample_records()
        # The first record remains a legacy scalar while the second uses the
        # current multi-select representation.
        records[1]["meta"]["service_availed"] = [
            "Records Request",
            "Enrollment",
            "records request",
        ]

        analysis = AnalysisService.analyze(records)
        services = {row["service"]: row for row in analysis["services"]}
        self.assertEqual(services["Enrollment"]["count"], 2)
        self.assertEqual(services["Enrollment"]["percent"], 100.0)
        self.assertEqual(services["Records Request"]["count"], 1)
        self.assertEqual(services["Records Request"]["percent"], 50.0)

        enrollment = AnalysisService.filter_records(records, {"service": "Enrollment"})
        records_request = AnalysisService.filter_records(
            records,
            {"service_availed": "records request"},
        )
        self.assertEqual({record["id"] for record in enrollment}, {"onsite-1", "online-1"})
        self.assertEqual([record["id"] for record in records_request], ["online-1"])
        self.assertEqual(
            analysis["recent"][0]["service"],
            "Records Request; Enrollment",
        )

    def test_analysis_does_not_mutate_input_and_handles_empty_data(self) -> None:
        records = sample_records()
        original = deepcopy(records)
        AnalysisService.analyze(records, {"search": "queue"})
        self.assertEqual(records, original)
        empty = AnalysisService.analyze([])
        self.assertEqual(empty["overview"]["total_responses"], 0)
        self.assertIsNone(empty["overview"]["positive_rate"])
        self.assertEqual(empty["overview"]["rating"], "No Data")
        self.assertEqual(empty["insights"][0]["title"], "No survey data yet")

    def test_tied_high_scores_do_not_create_false_focus_or_review_warnings(self) -> None:
        records = sample_records()
        for record in records:
            record["sqd"].update({f"sqd{index}": 5 for index in range(1, 9)})

        titles = [item["title"] for item in AnalysisService.analyze(records)["insights"]]
        self.assertIn("Dimensions are consistent", titles)
        self.assertIn("Service scores are consistent", titles)
        self.assertFalse(any(title.startswith("Focus area:") for title in titles))
        self.assertFalse(any(title.startswith("Service to review:") for title in titles))


if __name__ == "__main__":
    unittest.main()
