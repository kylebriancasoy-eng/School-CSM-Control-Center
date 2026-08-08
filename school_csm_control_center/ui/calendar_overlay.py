from __future__ import annotations

from PySide6.QtCore import QDate, Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCalendarWidget,
    QFrame,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from school_csm_control_center.ui import theme
from school_csm_control_center.ui.overlays import ClickScrim
from school_csm_control_center.ui.widgets import TooltipIconButton


class CalendarPickerOverlay(QWidget):
    """An embedded survey-date calendar that never creates a dialog window."""

    date_selected = Signal(QDate)

    def __init__(self, owner, parent: QWidget) -> None:
        super().__init__(parent)
        self.owner = owner
        self.setObjectName("calendar_picker_overlay")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.hide()

        self.scrim = ClickScrim(self)
        self.scrim.setObjectName("calendar_picker_scrim")
        self.scrim.clicked.connect(self.close_overlay)

        self.panel = QFrame(self)
        self.panel.setObjectName("calendar_picker_panel")
        panel_layout = QVBoxLayout(self.panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.setSpacing(0)

        header = QFrame()
        header.setObjectName("calendar_picker_header")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(18, 14, 12, 14)
        header_layout.setSpacing(9)
        title = QLabel("Select survey date")
        title.setObjectName("calendar_picker_title")
        header_layout.addWidget(title, 1)
        self.close_button = TooltipIconButton("close", "Close calendar")
        self.close_button.clicked.connect(self.close_overlay)
        header_layout.addWidget(self.close_button)
        panel_layout.addWidget(header)

        navigation = QFrame()
        navigation.setObjectName("calendar_picker_navigation")
        navigation_layout = QHBoxLayout(navigation)
        navigation_layout.setContentsMargins(16, 11, 16, 9)
        navigation_layout.setSpacing(9)
        self.previous_button = TooltipIconButton(
            "previous", "Show previous month", role="subtle"
        )
        self.previous_button.clicked.connect(self._previous_month)
        navigation_layout.addWidget(self.previous_button)
        self.month_label = QLabel()
        self.month_label.setObjectName("calendar_picker_month")
        self.month_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        navigation_layout.addWidget(self.month_label, 1)
        self.today_button = TooltipIconButton(
            "calendar", "Use today's date", role="subtle"
        )
        self.today_button.clicked.connect(self._use_today)
        navigation_layout.addWidget(self.today_button)
        self.next_button = TooltipIconButton(
            "next", "Show next month", role="subtle"
        )
        self.next_button.clicked.connect(self._next_month)
        navigation_layout.addWidget(self.next_button)
        panel_layout.addWidget(navigation)

        self.calendar = QCalendarWidget()
        self.calendar.setObjectName("survey_date_calendar")
        self.calendar.setNavigationBarVisible(False)
        self.calendar.setGridVisible(True)
        self.calendar.setFirstDayOfWeek(Qt.DayOfWeek.Monday)
        self.calendar.setHorizontalHeaderFormat(
            QCalendarWidget.HorizontalHeaderFormat.ShortDayNames
        )
        self.calendar.setVerticalHeaderFormat(
            QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader
        )
        self.calendar.currentPageChanged.connect(self._page_changed)
        self.calendar.clicked.connect(self._apply_date)
        panel_layout.addWidget(self.calendar, 1)

        footer = QFrame()
        footer.setObjectName("calendar_picker_footer")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(16, 12, 16, 12)
        footer_layout.setSpacing(9)
        footer_layout.addStretch(1)
        self.cancel_button = TooltipIconButton(
            "cancel", "Cancel date selection", role="subtle"
        )
        self.cancel_button.clicked.connect(self.close_overlay)
        footer_layout.addWidget(self.cancel_button)
        self.apply_button = TooltipIconButton(
            "check",
            "Use selected survey date",
            icon_color=theme.SUCCESS,
            role="success",
            button_size=44,
        )
        self.apply_button.clicked.connect(
            lambda: self._apply_date(self.calendar.selectedDate())
        )
        footer_layout.addWidget(self.apply_button)
        panel_layout.addWidget(footer)

        self.escape_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        self.escape_shortcut.setContext(
            Qt.ShortcutContext.WidgetWithChildrenShortcut
        )
        self.escape_shortcut.activated.connect(self.close_overlay)
        self._apply_styles()

    def open_for(self, selected_date: QDate) -> None:
        date = selected_date if selected_date.isValid() else QDate.currentDate()
        self.owner.escape_shortcut.setEnabled(False)
        self.calendar.setSelectedDate(date)
        self.calendar.setCurrentPage(date.year(), date.month())
        self._update_month_label(date.year(), date.month())
        self.setGeometry(self.parentWidget().rect())
        self._position_children()
        self.show()
        self.raise_()
        self.calendar.setFocus(Qt.FocusReason.ActiveWindowFocusReason)

    def close_overlay(self) -> None:
        self.hide()
        self.owner.escape_shortcut.setEnabled(True)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self._position_children()

    def _position_children(self) -> None:
        self.scrim.setGeometry(self.rect())
        width = min(520, max(390, self.width() - 48))
        height = min(610, max(470, self.height() - 48))
        self.panel.setGeometry(
            (self.width() - width) // 2,
            (self.height() - height) // 2,
            width,
            height,
        )

    def _previous_month(self) -> None:
        self.calendar.showPreviousMonth()

    def _next_month(self) -> None:
        self.calendar.showNextMonth()

    def _use_today(self) -> None:
        today = QDate.currentDate()
        self.calendar.setSelectedDate(today)
        self.calendar.showSelectedDate()
        self._apply_date(today)

    def _page_changed(self, year: int, month: int) -> None:
        self._update_month_label(year, month)

    def _update_month_label(self, year: int, month: int) -> None:
        self.month_label.setText(QDate(year, month, 1).toString("MMMM yyyy"))

    def _apply_date(self, date: QDate) -> None:
        if not date.isValid():
            return
        self.date_selected.emit(date)
        self.close_overlay()

    def _apply_styles(self) -> None:
        self.setStyleSheet(
            f"""
            QWidget#calendar_picker_overlay {{ background: transparent; }}
            QFrame#calendar_picker_scrim {{ background: rgba(1, 8, 18, 0.82); border: none; }}
            QFrame#calendar_picker_panel {{
                background: {theme.WINDOW_BG_ALT};
                border: 1px solid rgba(43, 217, 197, 0.72);
                border-radius: 16px;
            }}
            QFrame#calendar_picker_header, QFrame#calendar_picker_footer {{
                background: {theme.TITLE_BG};
                border: none;
            }}
            QFrame#calendar_picker_header {{
                border-bottom: 1px solid {theme.DIVIDER};
                border-top-left-radius: 16px;
                border-top-right-radius: 16px;
            }}
            QFrame#calendar_picker_footer {{
                border-top: 1px solid {theme.DIVIDER};
                border-bottom-left-radius: 16px;
                border-bottom-right-radius: 16px;
            }}
            QFrame#calendar_picker_navigation {{ background: {theme.PANEL_BG}; border: none; }}
            QLabel#calendar_picker_title {{ color: {theme.TEXT_PRIMARY}; font-size: 18px; font-weight: 800; }}
            QLabel#calendar_picker_month {{ color: {theme.ACCENT_TEAL}; font-size: 15px; font-weight: 800; }}
            QCalendarWidget#survey_date_calendar {{
                background: {theme.WINDOW_BG_ALT};
                color: {theme.TEXT_PRIMARY};
                border: none;
            }}
            QCalendarWidget#survey_date_calendar QTableView {{
                background: {theme.WINDOW_BG_ALT};
                alternate-background-color: {theme.WINDOW_BG_ALT};
                color: {theme.TEXT_PRIMARY};
                selection-background-color: {theme.ACCENT_TEAL_DARK};
                selection-color: {theme.TEXT_PRIMARY};
                gridline-color: {theme.CARD_BORDER};
                outline: none;
            }}
            QCalendarWidget#survey_date_calendar QHeaderView::section {{
                background: {theme.PANEL_BG};
                color: {theme.TEXT_SECONDARY};
                border: none;
                padding: 7px 3px;
                font-weight: 700;
            }}
            """
        )

