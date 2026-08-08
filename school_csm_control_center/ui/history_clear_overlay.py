from __future__ import annotations

from datetime import date
from typing import Any

from PySide6.QtCore import QDate, Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from school_csm_control_center.ui import theme
from school_csm_control_center.ui.controls import NoWheelComboBox, NoWheelDateEdit
from school_csm_control_center.ui.overlays import ClickScrim
from school_csm_control_center.ui.widgets import TooltipIconButton


class HistoryClearOverlay(QWidget):
    """Select a survey-history date range before the two confirmations."""

    range_selected = Signal(object, object, int, str)

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("history_clear_overlay")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._records: list[dict[str, Any]] = []
        self.hide()

        self.scrim = ClickScrim(self)
        self.scrim.setObjectName("history_clear_scrim")
        self.scrim.clicked.connect(self.close_overlay)

        self.panel = QFrame(self)
        self.panel.setObjectName("history_clear_panel")
        layout = QVBoxLayout(self.panel)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(14)

        header = QHBoxLayout()
        copy = QVBoxLayout()
        copy.setSpacing(4)
        title = QLabel("Clear Survey History")
        title.setObjectName("history_clear_title")
        copy.addWidget(title)
        subtitle = QLabel("Choose the inclusive date range to remove. Dashboard analysis will be recalculated.")
        subtitle.setObjectName("history_clear_subtitle")
        subtitle.setWordWrap(True)
        copy.addWidget(subtitle)
        header.addLayout(copy, 1)
        close_button = TooltipIconButton("close", "Close without clearing history")
        close_button.clicked.connect(self.close_overlay)
        header.addWidget(close_button)
        layout.addLayout(header)

        form = QFrame()
        form.setObjectName("history_clear_form")
        grid = QGridLayout(form)
        grid.setContentsMargins(14, 14, 14, 14)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)

        grid.addWidget(QLabel("RANGE"), 0, 0)
        self.mode_combo = NoWheelComboBox()
        self.mode_combo.addItem("All survey history", "all")
        self.mode_combo.addItem("Single day", "day")
        self.mode_combo.addItem("Single month", "month")
        self.mode_combo.addItem("Custom From–To", "custom")
        self.mode_combo.currentIndexChanged.connect(self._mode_changed)
        grid.addWidget(self.mode_combo, 0, 1, 1, 3)

        self.day_label = QLabel("DATE")
        self.day_edit = self._date_edit(QDate.currentDate())
        grid.addWidget(self.day_label, 1, 0)
        grid.addWidget(self.day_edit, 1, 1, 1, 3)

        self.month_label = QLabel("MONTH")
        self.month_edit = self._date_edit(QDate.currentDate(), "MMMM yyyy")
        grid.addWidget(self.month_label, 2, 0)
        grid.addWidget(self.month_edit, 2, 1, 1, 3)

        self.from_label = QLabel("FROM")
        self.from_edit = self._date_edit(QDate.currentDate().addMonths(-1))
        self.to_label = QLabel("TO")
        self.to_edit = self._date_edit(QDate.currentDate())
        grid.addWidget(self.from_label, 3, 0)
        grid.addWidget(self.from_edit, 3, 1)
        grid.addWidget(self.to_label, 3, 2)
        grid.addWidget(self.to_edit, 3, 3)
        layout.addWidget(form)

        self.preview = QLabel()
        self.preview.setObjectName("history_clear_preview")
        self.preview.setWordWrap(True)
        layout.addWidget(self.preview)

        warning = QLabel("This operation affects survey-response history only. Dashboard print history and MRS printing records are not removed.")
        warning.setObjectName("history_clear_warning")
        warning.setWordWrap(True)
        layout.addWidget(warning)

        actions = QHBoxLayout()
        actions.addStretch(1)
        cancel = TooltipIconButton("cancel", "Cancel history clearing", role="subtle")
        cancel.clicked.connect(self.close_overlay)
        actions.addWidget(cancel)
        self.continue_button = TooltipIconButton(
            "delete", "Continue to the first deletion confirmation", icon_color=theme.DANGER, role="danger"
        )
        self.continue_button.clicked.connect(self._continue)
        actions.addWidget(self.continue_button)
        layout.addLayout(actions)

        for editor in (self.day_edit, self.month_edit, self.from_edit, self.to_edit):
            editor.dateChanged.connect(self._refresh_preview)
        self._mode_changed()
        self._apply_styles()

    @staticmethod
    def _date_edit(value: QDate, display: str = "MMM d, yyyy") -> NoWheelDateEdit:
        editor = NoWheelDateEdit()
        editor.setCalendarPopup(True)
        editor.setDisplayFormat(display)
        editor.setDate(value)
        editor.setMinimumDate(QDate(2000, 1, 1))
        editor.setMaximumDate(QDate.currentDate().addYears(5))
        return editor

    def open_overlay(self, records: list[dict[str, Any]]) -> None:
        self._records = list(records)
        self.setGeometry(self.parentWidget().rect())
        self._position_children()
        self._refresh_preview()
        self.show()
        self.raise_()

    def close_overlay(self) -> None:
        self.hide()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._position_children()

    def _position_children(self) -> None:
        self.scrim.setGeometry(self.rect())
        width = min(680, max(420, self.width() - 48))
        height = min(520, max(390, self.height() - 48))
        self.panel.setGeometry((self.width()-width)//2, (self.height()-height)//2, width, height)

    def _mode_changed(self) -> None:
        mode = str(self.mode_combo.currentData() or "all")
        day = mode == "day"
        month = mode == "month"
        custom = mode == "custom"
        self.day_label.setVisible(day)
        self.day_edit.setVisible(day)
        self.month_label.setVisible(month)
        self.month_edit.setVisible(month)
        self.from_label.setVisible(custom)
        self.from_edit.setVisible(custom)
        self.to_label.setVisible(custom)
        self.to_edit.setVisible(custom)
        self._refresh_preview()

    def selected_range(self) -> tuple[str | None, str | None, str]:
        mode = str(self.mode_combo.currentData() or "all")
        if mode == "all":
            return None, None, "All survey history"
        if mode == "day":
            value = self.day_edit.date().toPython().isoformat()
            return value, value, self.day_edit.date().toString("MMMM d, yyyy")
        if mode == "month":
            chosen = self.month_edit.date()
            first = QDate(chosen.year(), chosen.month(), 1)
            last = QDate(chosen.year(), chosen.month(), first.daysInMonth())
            return first.toPython().isoformat(), last.toPython().isoformat(), chosen.toString("MMMM yyyy")
        start = self.from_edit.date()
        end = self.to_edit.date()
        if start > end:
            start, end = end, start
        return (
            start.toPython().isoformat(),
            end.toPython().isoformat(),
            f"{start.toString('MMMM d, yyyy')} to {end.toString('MMMM d, yyyy')}",
        )

    def _record_date(self, record: dict[str, Any]) -> str:
        meta = record.get("meta") if isinstance(record.get("meta"), dict) else {}
        return str(record.get("survey_date") or meta.get("survey_date") or record.get("created_at") or "")[:10]

    def matching_count(self) -> int:
        date_from, date_to, _ = self.selected_range()
        if date_from is None and date_to is None:
            return len(self._records)
        count = 0
        for record in self._records:
            value = self._record_date(record)
            if len(value) != 10:
                continue
            if date_from and value < date_from:
                continue
            if date_to and value > date_to:
                continue
            count += 1
        return count

    def _refresh_preview(self) -> None:
        date_from, date_to, label = self.selected_range()
        count = self.matching_count()
        self.preview.setText(f"Selected period: {label}\nSurvey responses that will be removed: {count}")
        self.continue_button.setEnabled(count > 0)

    def _continue(self) -> None:
        date_from, date_to, label = self.selected_range()
        count = self.matching_count()
        if count <= 0:
            return
        self.close_overlay()
        self.range_selected.emit(date_from, date_to, count, label)

    def _apply_styles(self) -> None:
        self.setStyleSheet(f"""
        QWidget#history_clear_overlay {{ background: transparent; }}
        QFrame#history_clear_scrim {{ background: rgba(1, 8, 18, 0.82); border: none; }}
        QFrame#history_clear_panel {{ background: {theme.PANEL_BG}; border: 1px solid rgba(235, 82, 96, 0.72); border-radius: 18px; }}
        QLabel#history_clear_title {{ color: {theme.TEXT_PRIMARY}; font-size: 19px; font-weight: 800; }}
        QLabel#history_clear_subtitle {{ color: {theme.TEXT_SECONDARY}; font-size: 13px; }}
        QFrame#history_clear_form {{ background: {theme.CARD_BG}; border: 1px solid {theme.CARD_BORDER}; border-radius: 12px; }}
        QLabel#history_clear_preview {{ color: {theme.TEXT_PRIMARY}; background: rgba(235, 82, 96, 0.10); border: 1px solid rgba(235, 82, 96, 0.34); border-radius: 10px; padding: 12px; font-weight: 700; }}
        QLabel#history_clear_warning {{ color: {theme.TEXT_MUTED}; font-size: 12px; }}
        """)
