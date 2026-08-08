from __future__ import annotations

import unittest

from school_csm_control_center.questionnaire import (
    CC_QUESTIONS,
    QUESTIONNAIRE_SPEC,
    RATING_OPTIONS,
    SQD_DIMENSIONS,
    SQD_QUESTIONS,
    SURVEY_MODES,
    apply_cc_branching,
    questionnaire_for,
)


class QuestionnaireSpecificationTests(unittest.TestCase):
    def test_modes_and_mode_specific_sqd_questions_match_annex_a(self) -> None:
        self.assertEqual(SURVEY_MODES, ("onsite", "online"))
        self.assertEqual(tuple(SQD_QUESTIONS["onsite"]), tuple(f"sqd{i}" for i in range(9)))
        self.assertEqual(tuple(SQD_QUESTIONS["online"]), tuple(f"sqd{i}" for i in range(1, 9)))
        self.assertTrue(SQD_QUESTIONS["onsite"]["sqd0"]["onsite_only"])
        self.assertNotIn("sqd0", SQD_QUESTIONS["online"])

    def test_rating_options_use_zero_for_explicit_na_and_online_omits_na(self) -> None:
        self.assertEqual([option["value"] for option in RATING_OPTIONS], [1, 2, 3, 4, 5, 0])
        self.assertEqual(
            [option["value"] for option in QUESTIONNAIRE_SPEC["online"]["rating_options"]],
            [1, 2, 3, 4, 5],
        )

    def test_dimensions_are_comparable_across_modes(self) -> None:
        self.assertEqual(
            tuple(SQD_DIMENSIONS.values()),
            (
                "Responsiveness",
                "Reliability",
                "Access and Facilities",
                "Communication",
                "Costs",
                "Integrity",
                "Assurance",
                "Outcome",
            ),
        )
        for key, dimension in SQD_DIMENSIONS.items():
            self.assertEqual(SQD_QUESTIONS["onsite"][key]["dimension"], dimension)
            self.assertEqual(SQD_QUESTIONS["online"][key]["dimension"], dimension)

    def test_cc_options_and_branching_keep_onsite_and_online_codes_distinct(self) -> None:
        self.assertEqual(
            [item["value"] for item in CC_QUESTIONS["onsite"]["cc1"]["options"]],
            [1, 2, 3, 4],
        )
        self.assertEqual(
            [item["value"] for item in CC_QUESTIONS["online"]["cc1"]["options"]],
            [1, 2, 3],
        )
        self.assertEqual(
            apply_cc_branching("onsite", {"cc1": 4, "cc2": 1, "cc3": 1}),
            {"cc1": 4, "cc2": 5, "cc3": 4},
        )
        self.assertEqual(
            apply_cc_branching("online", {"cc1": 3, "cc2": 1, "cc3": 1}),
            {"cc1": 3},
        )
        self.assertEqual(
            apply_cc_branching("online", {"cc1": 1, "cc2": 3, "cc3": 2, "cc3_reason": "x"}),
            {"cc1": 1, "cc2": 3},
        )

    def test_questionnaire_copy_is_isolated_and_onsite_has_control_number(self) -> None:
        first = questionnaire_for("onsite")
        second = questionnaire_for("ONSITE")
        self.assertIn("control_number", {field["key"] for field in first["meta_fields"]})
        first["meta_fields"][0]["label"] = "Changed"
        self.assertNotEqual(first, second)
        with self.assertRaises(ValueError):
            questionnaire_for("phone")


if __name__ == "__main__":
    unittest.main()
