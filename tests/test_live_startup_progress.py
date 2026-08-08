from pathlib import Path


def test_live_startup_progress_contract():
    root = Path(__file__).resolve().parents[1]
    app_source = (root / "school_csm_control_center" / "app.py").read_text(encoding="utf-8")
    splash_source = (
        root
        / "school_csm_control_center"
        / "mosslab_ui"
        / "splash.py"
    ).read_text(encoding="utf-8")
    window_source = (root / "school_csm_control_center" / "ui" / "main_window.py").read_text(encoding="utf-8")

    assert "MoSSLabStartupSplash" in app_source
    assert "class MoSSLabStartupSplash" in splash_source
    assert "QProgressBar" in splash_source
    assert "def set_progress" in splash_source
    assert "splash.set_progress" in app_source
    assert 'report_progress(100, "Ready")' in app_source
    assert "startup_progress: Callable" in window_source
    assert window_source.count("_report_startup(") >= 8
