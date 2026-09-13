from pathlib import Path
import unittest


ROOT = Path(__file__).parents[1]
SURVEY_HTML = (
    ROOT / "school_csm_control_center" / "web_server" / "static" / "index.html"
)


class SurveySchoolIdentityUIContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = SURVEY_HTML.read_text(encoding="utf-8")

    def test_verified_school_identity_is_visible_and_read_only(self) -> None:
        self.assertIn('id="verifiedSchoolId" type="text" readonly', self.html)
        self.assertIn('id="verifiedSchoolName" type="text" readonly', self.html)
        self.assertIn('id="languageSchoolName"', self.html)
        self.assertIn('id="languageSchoolId"', self.html)

    def test_identity_is_loaded_only_from_server_configuration(self) -> None:
        self.assertIn(
            'const schoolId = String(serverConfig.school_id || "").trim();',
            self.html,
        )
        self.assertIn(
            'const schoolName = String(serverConfig.school_name || "School").trim()',
            self.html,
        )
        self.assertNotIn('name="school_id"', self.html.casefold())
        self.assertNotIn('name="school_name"', self.html.casefold())

    def test_language_changes_rerender_without_changing_identity_source(self) -> None:
        apply_language = self.html[
            self.html.index("function applyLanguage()") : self.html.index(
                "function renderRatingLegend()"
            )
        ]
        self.assertIn("renderVerifiedSchoolIdentity();", apply_language)
        self.assertIn('scannerLaunch.hidden = true;', self.html)

    def test_current_browser_questionnaire_omits_sqd5(self) -> None:
        self.assertNotIn('["SQD5",', self.html)
        self.assertNotIn("Nine quality statements", self.html)


if __name__ == "__main__":
    unittest.main()
