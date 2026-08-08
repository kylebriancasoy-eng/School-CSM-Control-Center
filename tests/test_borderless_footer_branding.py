from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_print_footer_logos_are_not_framed():
    source = (ROOT / "school_csm_control_center/ui/dashboard_printing.py").read_text(encoding="utf-8")
    footer = source[source.index("def _paint_footer"):source.index("class DashboardPrintRenderer", source.index("def _paint_footer")) if "class DashboardPrintRenderer" in source[source.index("def _paint_footer"):] else len(source)]
    assert "framed=True" not in footer


def test_live_footer_logo_containers_are_borderless():
    source = (ROOT / "school_csm_control_center/ui/main_window.py").read_text(encoding="utf-8")
    assert "QLabel#footer_school_logo, QLabel#footer_mosslab_seal, QLabel#footer_deped_logo, QLabel#footer_mosslab_logo" in source
    assert "background: transparent; border: none;" in source
