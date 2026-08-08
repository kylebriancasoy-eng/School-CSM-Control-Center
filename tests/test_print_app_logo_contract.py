from pathlib import Path


def test_print_footer_includes_application_logo_contract():
    root = Path(__file__).resolve().parents[1]
    identity = (root / "school_csm_control_center" / "app_identity.py").read_text(encoding="utf-8")
    overlay = (root / "school_csm_control_center" / "ui" / "dashboard_print_overlay.py").read_text(encoding="utf-8")
    printing = (root / "school_csm_control_center" / "ui" / "dashboard_printing.py").read_text(encoding="utf-8")

    assert "def resolve_application_logo" in identity
    assert "resolve_application_logo" in overlay
    assert "app_logo_path" in overlay
    assert "app_logo_path" in printing
    assert "app_slot" in printing
    assert "metadata.app_logo_path" in printing
