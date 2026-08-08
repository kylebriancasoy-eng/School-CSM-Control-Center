from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from school_csm_control_center.storage.control_center_settings import ControlCenterSettingsStore


class SchoolInformationFeatureTests(unittest.TestCase):
    def test_school_information_fields_are_persisted(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            store = ControlCenterSettingsStore(root)
            settings = store.load()
            settings.update(
                {
                    "school_name": "Test Elementary School",
                    "school_id": "123456",
                    "school_region": "Regional Office VIII",
                    "school_division": "Schools Division of Samar",
                    "school_district": "Motiong Schools District",
                    "school_address": "Test Address",
                    "school_email": "school@example.test",
                    "school_contact": "09123456789",
                    "school_head": "School Head",
                    "csm_focal_person": "CSM Operator",
                    "school_logo_path": "data/csm_survey/school_logo.png",
                }
            )
            saved = store.save(settings)
            reloaded = store.load()
            for key in (
                "school_name",
                "school_id",
                "school_region",
                "school_division",
                "school_district",
                "school_address",
                "school_email",
                "school_contact",
                "school_head",
                "csm_focal_person",
                "school_logo_path",
            ):
                self.assertEqual(saved[key], reloaded[key])

    def test_ui_and_survey_branding_contracts_exist(self) -> None:
        root = Path(__file__).parents[1]
        main_window = (root / "school_csm_control_center" / "ui" / "main_window.py").read_text(encoding="utf-8")
        school_board = (root / "school_csm_control_center" / "ui" / "school_information_board.py").read_text(encoding="utf-8")
        survey_html = (root / "school_csm_control_center" / "web_server" / "static" / "index.html").read_text(encoding="utf-8")
        server_source = (root / "school_csm_control_center" / "web_server" / "server.py").read_text(encoding="utf-8")

        self.assertIn('TooltipIconButton("school"', main_window)
        self.assertIn('setObjectName("branding_footer")', main_window)
        self.assertIn("footer_school_logo", main_window)
        self.assertIn("footer_deped_logo", main_window)
        self.assertIn("footer_mosslab_logo", main_window)
        self.assertIn("footer_mosslab_seal", main_window)
        self.assertIn("class SchoolInformationBoard", school_board)
        self.assertIn("Upload or replace the school logo", school_board)
        self.assertIn('class="survey-brand-footer"', survey_html)
        self.assertIn('id="surveySchoolLogo"', survey_html)
        self.assertIn("deped-logo-ui.png", survey_html)
        self.assertIn("mosslab-seal-ui.png", survey_html)
        self.assertIn('path == "/school-logo.png"', server_source)
        self.assertIn('"school_logo_url"', server_source)


if __name__ == "__main__":
    unittest.main()
