from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class MoSSLabBrandingContractTests(unittest.TestCase):
    def test_packaged_brand_assets_exist(self) -> None:
        splash = ROOT / "school_csm_control_center" / "mosslab_ui"
        self.assertTrue((splash / "__init__.py").is_file())
        self.assertTrue((splash / "splash.py").is_file())
        self.assertTrue((splash / "splash_theme.json").is_file())
        self.assertTrue((splash / "assets" / "mosslab-logo.png").is_file())
        self.assertTrue((ROOT / "assets" / "mosslab_logo_ui.png").is_file())
        self.assertTrue((ROOT / "assets" / "mosslab_seal_ui.png").is_file())

    def test_startup_uses_branded_splash_before_main_window(self) -> None:
        source = (ROOT / "school_csm_control_center" / "app.py").read_text(encoding="utf-8")
        self.assertIn("MoSSLabStartupSplash", source)
        self.assertIn("school_csm_control_center.mosslab_ui", source)
        self.assertIn("FORMAL_APPLICATION_NAME,\n        APPLICATION_SUBTITLE,", source)
        self.assertIn("QTimer.singleShot(0, reveal_window)", source)
        self.assertIn("splash.finish(window)", source)

    def test_control_center_fixed_title_bar_contains_mosslab_logo(self) -> None:
        source = (ROOT / "school_csm_control_center" / "ui" / "title_bar.py").read_text(encoding="utf-8")
        self.assertIn('title_bar_mosslab_logo', source)
        self.assertIn('mosslabLogoPath', source)
        self.assertIn('Motiong Schools Systems Laboratory', source)

    def test_survey_form_contains_sticky_mosslab_branding(self) -> None:
        html = (ROOT / "school_csm_control_center" / "web_server" / "static" / "index.html").read_text(encoding="utf-8")
        self.assertIn('class="topbar"', html)
        self.assertIn('/static/mosslab-logo-ui.png', html)
        self.assertIn('class="language-mosslab-logo"', html)

    def test_no_console_launcher_is_packaged(self) -> None:
        self.assertTrue((ROOT / "START_SCHOOL_CSM_CONTROL_CENTER.vbs").is_file())
        cmd = (ROOT / "START_SCHOOL_CSM_CONTROL_CENTER.cmd").read_text(encoding="utf-8")
        self.assertIn("wscript.exe", cmd.casefold())


if __name__ == "__main__":
    unittest.main()
