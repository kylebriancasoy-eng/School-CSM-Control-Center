from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_history_exposes_csv_import_action():
    source = (ROOT / "school_csm_control_center/ui/history_board.py").read_text(encoding="utf-8")
    assert "import_requested = Signal()" in source
    assert "Import CSM survey-history CSV" in source


def test_dashboard_exposes_single_month_and_custom_from_to_filters():
    source = (ROOT / "school_csm_control_center/ui/dashboard_board.py").read_text(encoding="utf-8")
    assert '("Single month", "single_month")' in source
    assert '("Custom range", "custom")' in source
    assert 'self.date_from_label = QLabel("FROM")' in source
    assert 'self.date_to_label = QLabel("TO")' in source
    assert 'def print_scope_text(self)' in source


def test_print_report_header_carries_filter_scope():
    printing = (ROOT / "school_csm_control_center/ui/dashboard_printing.py").read_text(encoding="utf-8")
    overlay = (ROOT / "school_csm_control_center/ui/dashboard_print_overlay.py").read_text(encoding="utf-8")
    assert 'filter_scope: str = "Date range: All available responses"' in printing
    assert "metadata.filter_scope" in printing
    assert "filter_scope=self._filter_scope" in overlay
