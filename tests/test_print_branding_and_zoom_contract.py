from __future__ import annotations

from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PrintBrandingAndZoomContractTests(unittest.TestCase):
    def test_deped_asset_and_resolver_are_packaged(self) -> None:
        identity = (ROOT / "school_csm_control_center/app_identity.py").read_text(encoding="utf-8")
        self.assertTrue((ROOT / "assets/deped_logo_ui.png").is_file())
        self.assertTrue((ROOT / "school_csm_control_center/web_server/static/deped-logo-ui.png").is_file())
        self.assertIn("def resolve_deped_logo", identity)

    def test_print_footer_contains_all_brand_assets(self) -> None:
        source = (ROOT / "school_csm_control_center/ui/dashboard_printing.py").read_text(encoding="utf-8")
        for token in (
            "school_logo_path",
            "deped_logo_path",
            "mosslab_logo_path",
            "mosslab_seal_path",
            "FOOTER_HEIGHT_MM = 22.0",
        ):
            self.assertIn(token, source)

    def test_preview_uses_mouse_wheel_for_zoom(self) -> None:
        source = (ROOT / "school_csm_control_center/ui/dashboard_print_overlay.py").read_text(encoding="utf-8")
        self.assertIn("class _PreviewWheelZoomFilter", source)
        self.assertIn("QEvent.Type.Wheel", source)
        self.assertIn("self.preview.zoomIn(1.15)", source)
        self.assertIn("self.preview.zoomOut(1.15)", source)
        self.assertIn("self._install_preview_wheel_zoom()", source)


if __name__ == "__main__":
    unittest.main()
