from pathlib import Path


def test_mobile_language_and_service_overlays_scroll_as_complete_sections():
    html = (Path(__file__).parents[1] / "school_csm_control_center" / "web_server" / "static" / "index.html").read_text(encoding="utf-8")
    assert ".language-gate {\n        display: block;\n        overflow-y: auto;" in html
    assert ".language-footer {\n        position: sticky;\n        bottom: 0;" in html
    assert ".picker-overlay {\n        display: block;\n        overflow-y: auto;" in html
    assert ".picker-dialog {\n        width: 100%;\n        min-height: 100dvh;" in html
    assert ".picker-scroll { overflow: visible; }" in html
    assert ".picker-foot {\n        position: sticky;\n        bottom: 0;" in html


def test_final_mobile_overrides_follow_base_overlay_styles():
    html = (Path(__file__).parents[1] / "school_csm_control_center" / "web_server" / "static" / "index.html").read_text(encoding="utf-8")
    final_marker = "/* Final mobile overlay overrides"
    assert html.rfind(final_marker) > html.find(".picker-overlay {\n      position: fixed")
    assert html.rfind(final_marker) > html.find(".language-gate {\n      position: fixed")
