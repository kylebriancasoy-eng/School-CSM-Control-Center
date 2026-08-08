from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_server_board_uses_protected_field_classes() -> None:
    source = (ROOT / "school_csm_control_center/ui/server_board.py").read_text(encoding="utf-8")
    assert "NoWheelComboBox" in source
    assert "NoWheelDateEdit" in source
    assert "NoWheelSpinBox" in source
    assert "= QComboBox()" not in source
    assert "= QDateEdit()" not in source
    assert "= QSpinBox()" not in source


def test_server_helper_containers_are_transparent() -> None:
    source = (ROOT / "school_csm_control_center/ui/server_board.py").read_text(encoding="utf-8")
    assert 'setObjectName("server_content")' in source
    assert 'setObjectName("server_metric_box")' in source
    assert 'setObjectName("server_tiles")' in source
    assert "QWidget#server_content, QWidget#server_metric_box, QWidget#server_tiles" in source


def test_click_activated_wheel_guard_is_shared() -> None:
    source = (ROOT / "school_csm_control_center/ui/controls.py").read_text(encoding="utf-8")
    assert "_wheel_armed" in source
    assert "mousePressEvent" in source
    assert "focusOutEvent" in source
    assert "NoWheelDoubleSpinBox" in source
