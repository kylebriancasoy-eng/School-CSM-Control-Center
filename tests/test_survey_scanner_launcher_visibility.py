from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SURVEY_HTML = (
    ROOT / "school_csm_control_center" / "web_server" / "static" / "index.html"
)


class SurveyScannerLauncherVisibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = SURVEY_HTML.read_text(encoding="utf-8")

    def test_scanner_launcher_is_hidden_after_language_confirmation(self) -> None:
        handler_start = self.html.index(
            'document.getElementById("languageContinue").addEventListener("click"'
        )
        handler_end = self.html.index("\n    });", handler_start)
        handler = self.html[handler_start:handler_end]

        self.assertIn(
            'document.getElementById("languageGate").hidden = true;', handler
        )
        self.assertIn("scannerLaunch.hidden = true;", handler)

    def test_scanner_launcher_remains_available_on_language_gate_when_enabled(
        self,
    ) -> None:
        self.assertIn('id="scannerLaunch"', self.html)
        self.assertIn(
            "scannerLaunch.hidden = !serverConfig.scanner_remote_enabled;",
            self.html,
        )


if __name__ == "__main__":
    unittest.main()
