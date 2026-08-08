from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def test_clear_history_ui_and_dual_confirmation_contract():
    board = (ROOT / "school_csm_control_center/ui/history_board.py").read_text(encoding="utf-8")
    overlay = (ROOT / "school_csm_control_center/ui/history_clear_overlay.py").read_text(encoding="utf-8")
    main = (ROOT / "school_csm_control_center/ui/main_window.py").read_text(encoding="utf-8")
    assert "Clear survey history by date range" in board
    assert "Single month" in overlay
    assert "Custom From–To" in overlay
    assert "_confirm_clear_history_first" in main
    assert "_confirm_clear_history_final" in main
    assert "Dashboard print history and MRS printing records" in main
