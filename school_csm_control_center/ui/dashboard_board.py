from __future__ import annotations

import calendar
from datetime import date
from typing import Any

from PySide6.QtCore import QDate, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFrame,
    QGridLayout,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from school_csm_control_center.services.analysis_service import AnalysisService
from school_csm_control_center.school_services import (
    normalize_service_values,
    service_value_from_record,
)
from school_csm_control_center.ui import theme
from school_csm_control_center.ui.charts import DimensionBarChart, ResponseDonutChart, TrendChart
from school_csm_control_center.ui.controls import NoWheelComboBox, NoWheelDateEdit
from school_csm_control_center.ui.widgets import EmptyState, MetricCard, SectionCard, TooltipIconButton


def _score_text(value: float | int | None) -> str:
    return "--" if value is None else f"{float(value):.2f}%"


def _average_text(value: float | int | None) -> str:
    return "--/5" if value is None else f"{float(value):.2f}/5"


def _date_range_text(period: dict[str, Any]) -> str:
    start_text = str(period.get("from") or "")
    end_text = str(period.get("to") or "")
    try:
        start = date.fromisoformat(start_text)
        end = date.fromisoformat(end_text)
    except ValueError:
        return "No dated responses"
    if start == end:
        return f"{start.strftime('%b')} {start.day}, {start.year}"
    if start.year == end.year and start.month == end.month:
        return f"{start.strftime('%b')} {start.day}–{end.day}, {start.year}"
    if start.year == end.year:
        return f"{start.strftime('%b')} {start.day}–{end.strftime('%b')} {end.day}, {start.year}"
    return f"{start.strftime('%b')} {start.day}, {start.year}–{end.strftime('%b')} {end.day}, {end.year}"


def _band_detail(next_band: Any) -> str:
    if not isinstance(next_band, dict):
        return "Add valid SQD ratings to calculate the ARTA band"
    points = float(next_band.get("points_needed") or 0)
    threshold = float(next_band.get("threshold") or 0)
    label = str(next_band.get("label") or "next band")
    if points <= 0:
        return f"Benchmark met · {threshold:.0f}%+ {label}"
    return f"{points:.2f} points to {label} · target {threshold:.0f}%"


def _compact_text(text: str, limit: int) -> str:
    clean = " ".join(str(text or "").split())
    if len(clean) <= limit:
        return clean
    return clean[: max(1, limit - 1)].rstrip() + "…"


class RateRow(QFrame):
    def __init__(self, label: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("dashboard_rate_row")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(5)
        heading = QHBoxLayout()
        self.label = QLabel(label)
        self.label.setObjectName("dashboard_rate_label")
        heading.addWidget(self.label, 1)
        self.value = QLabel("--")
        self.value.setObjectName("dashboard_rate_value")
        heading.addWidget(self.value)
        layout.addLayout(heading)
        self.bar = QProgressBar()
        self.bar.setObjectName("dashboard_rate_bar")
        self.bar.setRange(0, 1000)
        self.bar.setValue(0)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(9)
        layout.addWidget(self.bar)

    def set_rate(
        self,
        rate: float | None,
        valid: int = 0,
        positive: int | None = None,
    ) -> None:
        if positive is None:
            positive = round(float(rate) * valid / 100) if rate is not None and valid else 0
        detail = f" · {positive}/{valid}" if valid else " · no answers"
        self.value.setText(f"{_score_text(rate)}{detail}")
        self.bar.setValue(0 if rate is None else round(rate * 10))
        self.setToolTip(
            f"{self.label.text()}: {_score_text(rate)}; {positive} positive out of {valid} valid answer(s)"
        )
        self.setAccessibleName(self.label.text())
        self.setAccessibleDescription(self.toolTip())


class ProfileBreakdownRow(QFrame):
    """Compact leading-group bar with a visible runner-up and full tooltip."""

    def __init__(self, title: str, values: list[dict[str, Any]], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("profile_breakdown_row")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(9, 6, 9, 6)
        layout.setSpacing(3)

        heading = QHBoxLayout()
        heading.setSpacing(8)
        label = QLabel(title)
        label.setObjectName("profile_breakdown_title")
        heading.addWidget(label)
        self.primary = QLabel("--")
        self.primary.setObjectName("profile_breakdown_primary")
        self.primary.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        heading.addWidget(self.primary, 1)
        layout.addLayout(heading)

        self.bar = QProgressBar()
        self.bar.setObjectName("profile_breakdown_bar")
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(7)
        layout.addWidget(self.bar)

        self.secondary = QLabel()
        self.secondary.setObjectName("profile_breakdown_secondary")
        self.secondary.setWordWrap(True)
        layout.addWidget(self.secondary)
        self.set_values(title, values)

    def set_values(self, title: str, values: list[dict[str, Any]]) -> None:
        rows = [row for row in values if isinstance(row, dict)]
        if not rows:
            self.primary.setText("No data")
            self.secondary.clear()
            self.bar.setValue(0)
            return
        top = rows[0]
        top_label = str(top.get("label") or "Not Specified")
        top_count = int(top.get("count") or 0)
        top_percent = float(top.get("percent") or 0)
        self.primary.setText(f"{top_label} · {top_count} ({top_percent:.1f}%)")
        self.bar.setValue(round(top_percent * 10))
        if len(rows) > 1:
            runner = rows[1]
            self.secondary.setText(
                f"Next: {runner.get('label') or 'Not Specified'} · "
                f"{int(runner.get('count') or 0)} ({float(runner.get('percent') or 0):.1f}%)"
            )
        else:
            self.secondary.setText("No second group in the current view")
        evidence = "; ".join(
            f"{row.get('label') or 'Not Specified'}: {int(row.get('count') or 0)} "
            f"({float(row.get('percent') or 0):.1f}%), SQD positive {_score_text(row.get('positive_rate'))}, "
            f"average {_average_text(row.get('average_rating'))}"
            for row in rows
        )
        self.setToolTip(f"{title}. {evidence}")
        self.setAccessibleName(f"{title} profile")
        self.setAccessibleDescription(evidence)


class DashboardBoard(QFrame):
    add_requested = Signal()
    export_requested = Signal()
    print_requested = Signal()
    methodology_requested = Signal()
    filters_changed = Signal(dict)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("dashboard_board")
        self._records: list[dict[str, Any]] = []
        self._analysis: dict[str, Any] = {}
        self._layout_mode = ""
        self._filters_wide = True
        self._filters_connected = False
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(9)

        header = QFrame()
        header.setObjectName("dashboard_header")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(15, 9, 12, 9)
        header_layout.setSpacing(8)
        heading = QVBoxLayout()
        heading.setSpacing(3)
        title_row = QHBoxLayout()
        title = QLabel("Dashboard")
        title.setObjectName("board_title")
        title_row.addWidget(title)
        live = QLabel("LIVE")
        live.setObjectName("live_badge")
        live.setAlignment(Qt.AlignmentFlag.AlignCenter)
        live.setFixedSize(48, 22)
        title_row.addWidget(live)
        title_row.addStretch(1)
        heading.addLayout(title_row)
        self.header_subtitle = QLabel("Complete Client Satisfaction Measurement analysis")
        self.header_subtitle.setObjectName("board_subtitle")
        self.header_subtitle.setWordWrap(True)
        self.header_subtitle.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        heading.addWidget(self.header_subtitle)
        header_layout.addLayout(heading, 1)
        self.info_button = TooltipIconButton("info", "View scoring methodology")
        self.info_button.clicked.connect(self.methodology_requested)
        header_layout.addWidget(self.info_button)
        self.print_button = TooltipIconButton("print", "Print complete Dashboard")
        self.print_button.setShortcut("Ctrl+P")
        self.print_button.clicked.connect(self.print_requested)
        header_layout.addWidget(self.print_button)
        self.export_button = TooltipIconButton("export", "Export filtered survey history")
        self.export_button.clicked.connect(self.export_requested)
        header_layout.addWidget(self.export_button)
        self.add_button = TooltipIconButton(
            "add", "Add Survey Result", icon_color=theme.SUCCESS, button_size=46, icon_size=24, role="success"
        )
        self.add_button.setShortcut("Ctrl+N")
        self.add_button.clicked.connect(self.add_requested)
        header_layout.addWidget(self.add_button)
        root.addWidget(header)

        self.filter_strip = QFrame()
        self.filter_strip.setObjectName("dashboard_filter_strip")
        self.filter_layout = QGridLayout(self.filter_strip)
        self.filter_layout.setContentsMargins(10, 7, 9, 7)
        self.filter_layout.setHorizontalSpacing(8)
        self.filter_layout.setVerticalSpacing(6)
        self.filter_copy = QWidget()
        self.filter_copy.setObjectName("dashboard_filter_copy")
        filter_copy_layout = QVBoxLayout(self.filter_copy)
        filter_copy_layout.setContentsMargins(0, 0, 0, 0)
        filter_copy_layout.setSpacing(1)
        self.filter_label = QLabel("FILTERS")
        self.filter_label.setObjectName("dashboard_filter_label")
        filter_copy_layout.addWidget(self.filter_label)
        self.filter_summary_label = QLabel("All responses")
        self.filter_summary_label.setObjectName("dashboard_filter_summary")
        self.filter_summary_label.setMinimumWidth(150)
        filter_copy_layout.addWidget(self.filter_summary_label)
        self.period_combo = self._filter_combo(
            (
                ("All dates", "all"),
                ("Today", "today"),
                ("This month", "month"),
                ("This quarter", "quarter"),
                ("This year", "year"),
                ("Single month", "single_month"),
                ("Custom range", "custom"),
            )
        )
        self.period_combo.setToolTip("Filter by preset period, a single month, or a custom From–To range")
        self.month_combo = self._filter_combo((("Select month", ""),))
        self.month_combo.setObjectName("dashboard_month_combo")
        self.month_combo.setToolTip("Show responses from one selected calendar month")
        self.month_combo.setMinimumWidth(138)
        self.date_from_edit = NoWheelDateEdit()
        self.date_from_edit.setObjectName("dashboard_date_from")
        self.date_from_edit.setCalendarPopup(True)
        self.date_from_edit.setDisplayFormat("MMM d, yyyy")
        self.date_from_edit.setDate(QDate.currentDate().addMonths(-1))
        self.date_from_edit.setToolTip("First survey date included in the Dashboard")
        self.date_to_edit = NoWheelDateEdit()
        self.date_to_edit.setObjectName("dashboard_date_to")
        self.date_to_edit.setCalendarPopup(True)
        self.date_to_edit.setDisplayFormat("MMM d, yyyy")
        self.date_to_edit.setDate(QDate.currentDate())
        self.date_to_edit.setToolTip("Last survey date included in the Dashboard")
        self.date_range_widget = QWidget()
        self.date_range_widget.setObjectName("dashboard_date_range")
        date_range_layout = QHBoxLayout(self.date_range_widget)
        date_range_layout.setContentsMargins(0, 0, 0, 0)
        date_range_layout.setSpacing(5)
        self.date_from_label = QLabel("FROM")
        self.date_from_label.setObjectName("dashboard_range_label")
        date_range_layout.addWidget(self.date_from_label)
        date_range_layout.addWidget(self.date_from_edit)
        self.date_to_label = QLabel("TO")
        self.date_to_label.setObjectName("dashboard_range_label")
        date_range_layout.addWidget(self.date_to_label)
        date_range_layout.addWidget(self.date_to_edit)
        self.month_combo.hide()
        self.date_range_widget.hide()
        self.mode_combo = self._filter_combo((("All modes", ""), ("Onsite", "onsite"), ("Online", "online")))
        self.mode_combo.setToolTip("Filter by questionnaire mode")
        self.client_combo = self._filter_combo(
            (("All client types", ""), ("Citizen", "Citizen"), ("Business", "Business"), ("Government", "Government"))
        )
        self.client_combo.setToolTip("Filter by client type")
        self.service_combo = self._filter_combo((("All services", ""),))
        self.service_combo.setToolTip("Filter by service availed")
        self.service_combo.setMinimumWidth(180)
        self.reset_filters_button = TooltipIconButton("reset", "Reset Dashboard filters", button_size=36, icon_size=19)
        self.reset_filters_button.clicked.connect(self.reset_filters)
        root.addWidget(self.filter_strip)

        self.scroll = QScrollArea()
        self.scroll.setObjectName("dashboard_scroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.body = QWidget()
        self.body.setObjectName("dashboard_body")
        body_layout = QVBoxLayout(self.body)
        body_layout.setContentsMargins(0, 0, 0, 4)
        body_layout.setSpacing(9)

        self.kpi_grid = QGridLayout()
        self.kpi_grid.setSpacing(8)
        self.total_metric = MetricCard("SURVEY RESPONSES", "0", "No filters applied", accent=theme.ACCENT_CYAN)
        self.overall_metric = MetricCard("OVERALL SQD1-SQD8", "--", "No valid ratings", accent=theme.ACCENT_TEAL)
        self.band_metric = MetricCard("PERFORMANCE BAND", "No Data", "ARTA interpretation", accent=theme.PURPLE)
        self.sqd0_metric = MetricCard("SQD0 SATISFACTION", "--", "Onsite responses only", accent=theme.WARNING)
        self.metric_cards = [self.total_metric, self.overall_metric, self.band_metric, self.sqd0_metric]
        body_layout.addLayout(self.kpi_grid)

        self.analysis_grid = QGridLayout()
        self.analysis_grid.setHorizontalSpacing(10)
        self.analysis_grid.setVerticalSpacing(10)
        self.trend_chart = TrendChart("Satisfaction Trend")
        self.donut_chart = ResponseDonutChart("SQD Response Mix")
        self.dimension_chart = DimensionBarChart("Service Quality Dimensions")
        self.cc_card = self._build_cc_card()
        self.demographics_card = self._build_demographics_card()
        self.services_card = self._build_services_card()
        self.insights_card = self._build_insights_card()
        self.feedback_card = self._build_feedback_card()
        self.recent_card = self._build_recent_card()
        self.analysis_widgets = [
            self.trend_chart,
            self.donut_chart,
            self.dimension_chart,
            self.cc_card,
            self.demographics_card,
            self.services_card,
            self.insights_card,
            self.feedback_card,
            self.recent_card,
        ]
        body_layout.addLayout(self.analysis_grid)
        body_layout.addStretch(1)
        self.scroll.setWidget(self.body)
        root.addWidget(self.scroll, 1)
        self._apply_styles()
        self._reflow(force=True)

    @staticmethod
    def _filter_combo(items: tuple[tuple[str, str], ...]) -> NoWheelComboBox:
        combo = NoWheelComboBox()
        combo.setObjectName("dashboard_filter_combo")
        combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        combo.setMinimumContentsLength(5)
        combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        for label, value in items:
            combo.addItem(label, value)
        return combo

    def _connect_filters(self) -> None:
        if self._filters_connected:
            return
        for combo in (self.period_combo, self.month_combo, self.mode_combo, self.client_combo, self.service_combo):
            combo.currentIndexChanged.connect(self._on_filters_changed)
        self.date_from_edit.dateChanged.connect(self._on_filters_changed)
        self.date_to_edit.dateChanged.connect(self._on_filters_changed)
        self._filters_connected = True

    def _build_cc_card(self) -> SectionCard:
        card = SectionCard("Citizen's Charter", "Mode-aware awareness, visibility, and usefulness")
        self.cc_summary = QLabel("Rates use answered, applicable Charter items.")
        self.cc_summary.setObjectName("dashboard_section_summary")
        self.cc_summary.setWordWrap(True)
        card.add_widget(self.cc_summary)
        self.cc_awareness = RateRow("Awareness")
        self.cc_saw = RateRow("Saw the Charter")
        self.cc_easy = RateRow("Easy to see / find")
        self.cc_helpful = RateRow("Helpful / used")
        for row in (self.cc_awareness, self.cc_saw, self.cc_easy, self.cc_helpful):
            card.add_widget(row)
        return card

    def _build_demographics_card(self) -> SectionCard:
        card = SectionCard("Client profile", "Top response groups in the current filter")
        self.demographics_box = QVBoxLayout()
        self.demographics_box.setSpacing(8)
        card.content_layout.addLayout(self.demographics_box)
        return card

    def _make_table(self, columns: list[str]) -> QTableWidget:
        table = QTableWidget(0, len(columns))
        table.setHorizontalHeaderLabels(columns)
        table.setObjectName("dashboard_table")
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        table.setWordWrap(False)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setStretchLastSection(True)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setMinimumSectionSize(52)
        table.horizontalHeader().setFixedHeight(34)
        table.verticalHeader().setDefaultSectionSize(32)
        table.setAlternatingRowColors(True)
        table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        table.setMinimumHeight(124)
        return table

    def _build_services_card(self) -> SectionCard:
        card = SectionCard("Services", "Volume, response share, score, and ARTA band")
        self.services_summary = QLabel("No service results in the current view.")
        self.services_summary.setObjectName("dashboard_section_summary")
        self.services_summary.setWordWrap(True)
        card.add_widget(self.services_summary)
        self.services_table = self._make_table(["Service", "Volume / share", "Positive", "Band"])
        self.services_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        card.add_widget(self.services_table)
        return card

    def _build_insights_card(self) -> SectionCard:
        card = SectionCard("Live insights", "Automatically refreshed priorities and strengths")
        self.insights_summary = QLabel("Evidence-based observations will appear with survey results.")
        self.insights_summary.setObjectName("dashboard_section_summary")
        self.insights_summary.setWordWrap(True)
        card.add_widget(self.insights_summary)
        self.insights_scroll = QScrollArea()
        self.insights_scroll.setObjectName("dashboard_inline_scroll")
        self.insights_scroll.setWidgetResizable(True)
        self.insights_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.insights_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.insights_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.insights_content = QWidget()
        self.insights_content.setObjectName("dashboard_inline_scroll_content")
        self.insights_box = QVBoxLayout(self.insights_content)
        self.insights_box.setContentsMargins(0, 0, 3, 0)
        self.insights_box.setSpacing(5)
        self.insights_box.addStretch(1)
        self.insights_scroll.setWidget(self.insights_content)
        self.insights_scroll.setFixedHeight(120)
        card.add_widget(self.insights_scroll)
        return card

    def _build_feedback_card(self) -> SectionCard:
        card = SectionCard("Written feedback", "Latest suggestions and remarks")
        self.feedback_summary = QLabel("No filtered response includes written feedback.")
        self.feedback_summary.setObjectName("dashboard_section_summary")
        self.feedback_summary.setWordWrap(True)
        card.add_widget(self.feedback_summary)
        self.feedback_box = QVBoxLayout()
        self.feedback_box.setSpacing(5)
        card.content_layout.addLayout(self.feedback_box)
        return card

    def _build_recent_card(self) -> SectionCard:
        card = SectionCard("Recent entries", "The latest saved survey results")
        self.recent_summary = QLabel("No recent entries in the current view.")
        self.recent_summary.setObjectName("dashboard_section_summary")
        self.recent_summary.setWordWrap(True)
        card.add_widget(self.recent_summary)
        self.recent_table = self._make_table(["Date", "Mode", "Service", "Score"])
        self.recent_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.recent_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.recent_table.setColumnWidth(3, 108)
        self.recent_table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        card.add_widget(self.recent_table)
        return card

    def refresh(self, records: list[dict[str, Any]]) -> None:
        self._records = list(records)
        self._refresh_service_filter()
        self._refresh_month_filter()
        self._sync_date_filter_visibility()
        filters = self.current_filters()
        self._analysis = AnalysisService.analyze(self._records, filters)
        self.filters_changed.emit(filters)
        self._render_analysis()

    def current_filtered_records(self) -> list[dict[str, Any]]:
        return AnalysisService.filter_records(self._records, self.current_filters())

    def current_filters(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        mode = self.mode_combo.currentData()
        service = self.service_combo.currentData()
        client_type = self.client_combo.currentData()
        if mode:
            result["mode"] = mode
        if service:
            result["service"] = service
        if client_type:
            result["client_type"] = client_type
        period = str(self.period_combo.currentData() or "all")
        today = date.today()
        if period == "today":
            result["date_from"] = today.isoformat()
            result["date_to"] = today.isoformat()
        elif period == "month":
            result["date_from"] = today.replace(day=1).isoformat()
            result["date_to"] = today.isoformat()
        elif period == "quarter":
            month = ((today.month - 1) // 3) * 3 + 1
            result["date_from"] = today.replace(month=month, day=1).isoformat()
            result["date_to"] = today.isoformat()
        elif period == "year":
            result["date_from"] = today.replace(month=1, day=1).isoformat()
            result["date_to"] = today.isoformat()
        elif period == "single_month":
            month_value = str(self.month_combo.currentData() or "")
            try:
                year, month = (int(part) for part in month_value.split("-", 1))
                last_day = calendar.monthrange(year, month)[1]
                result["date_from"] = date(year, month, 1).isoformat()
                result["date_to"] = date(year, month, last_day).isoformat()
            except (TypeError, ValueError):
                pass
        elif period == "custom":
            start = self.date_from_edit.date().toPython()
            end = self.date_to_edit.date().toPython()
            if start > end:
                start, end = end, start
            result["date_from"] = start.isoformat()
            result["date_to"] = end.isoformat()
        return result

    def print_scope_text(self) -> str:
        filters = self.current_filters()
        start = str(filters.get("date_from") or "")
        end = str(filters.get("date_to") or "")
        if start and end:
            date_scope = f"Date range: {_date_range_text({'from': start, 'to': end})}"
        else:
            date_scope = "Date range: All available responses"
        categories: list[str] = []
        for label, value in (
            ("Mode", self.mode_combo.currentText() if self.mode_combo.currentData() else ""),
            ("Client", self.client_combo.currentText() if self.client_combo.currentData() else ""),
            ("Service", self.service_combo.currentText() if self.service_combo.currentData() else ""),
        ):
            if value:
                categories.append(f"{label}: {value}")
        return " · ".join([date_scope, *categories])

    def reset_filters(self) -> None:
        for combo in (self.period_combo, self.month_combo, self.mode_combo, self.client_combo, self.service_combo):
            combo.blockSignals(True)
            combo.setCurrentIndex(0)
            combo.blockSignals(False)
        self.date_from_edit.blockSignals(True)
        self.date_to_edit.blockSignals(True)
        self.date_from_edit.setDate(QDate.currentDate().addMonths(-1))
        self.date_to_edit.setDate(QDate.currentDate())
        self.date_from_edit.blockSignals(False)
        self.date_to_edit.blockSignals(False)
        self.refresh(self._records)

    def _refresh_service_filter(self) -> None:
        current = self.service_combo.currentData()
        services = sorted(
            {
                service
                for record in self._records
                if isinstance(record, dict)
                for service in (
                    normalize_service_values(service_value_from_record(record))
                    or ["Unspecified service"]
                )
            },
            key=str.casefold,
        )
        self.service_combo.blockSignals(True)
        self.service_combo.clear()
        self.service_combo.addItem("All services", "")
        for service in services:
            self.service_combo.addItem(service, service)
        index = self.service_combo.findData(current)
        self.service_combo.setCurrentIndex(index if index >= 0 else 0)
        self.service_combo.blockSignals(False)
        self._connect_filters()

    def _refresh_month_filter(self) -> None:
        current = str(self.month_combo.currentData() or "")
        months = sorted(
            {
                str(record.get("survey_date") or "")[:7]
                for record in self._records
                if len(str(record.get("survey_date") or "")) >= 7
            },
            reverse=True,
        )
        self.month_combo.blockSignals(True)
        self.month_combo.clear()
        self.month_combo.addItem("Select month", "")
        for value in months:
            try:
                parsed = date.fromisoformat(f"{value}-01")
            except ValueError:
                continue
            self.month_combo.addItem(parsed.strftime("%B %Y"), value)
        index = self.month_combo.findData(current)
        if index < 0 and self.month_combo.count() > 1:
            index = 1
        self.month_combo.setCurrentIndex(max(0, index))
        self.month_combo.blockSignals(False)

    def _sync_date_filter_visibility(self) -> None:
        period = str(self.period_combo.currentData() or "all")
        self.month_combo.setVisible(period == "single_month")
        self.date_range_widget.setVisible(period == "custom")

    def _on_filters_changed(self) -> None:
        self._sync_date_filter_visibility()
        self.refresh(self._records)

    def _active_filter_count(self) -> int:
        categorical = sum(
            bool(combo.currentData())
            for combo in (self.mode_combo, self.client_combo, self.service_combo)
        )
        period_active = str(self.period_combo.currentData() or "all") != "all"
        return categorical + int(period_active)

    def _render_analysis(self) -> None:
        analysis = self._analysis
        overview = analysis.get("overview", {})
        mix = analysis.get("response_mix", {})
        total = int(overview.get("total_responses") or 0)
        all_total = len(self._records)
        selected_filters = self.current_filters()
        selected_from = str(selected_filters.get("date_from") or "")
        selected_to = str(selected_filters.get("date_to") or "")
        period_text = _date_range_text(
            {"from": selected_from, "to": selected_to}
            if selected_from and selected_to
            else (overview.get("period") or {})
        )
        active_filters = self._active_filter_count()
        filter_note = f"{total} of {all_total} responses · {period_text}"
        if active_filters:
            filter_note += f" · {active_filters} active filter{'s' if active_filters != 1 else ''}"
        self.filter_summary_label.setText(filter_note)
        self.filter_summary_label.setToolTip(filter_note)
        self.reset_filters_button.setEnabled(bool(active_filters))
        self.header_subtitle.setText(
            f"Live CSM report · {total} filtered response{'s' if total != 1 else ''} · {period_text}"
        )

        record_share = (total / all_total * 100) if all_total else 0.0
        response_detail = (
            f"{overview.get('onsite_responses', 0)} onsite · "
            f"{overview.get('online_responses', 0)} online · {record_share:.1f}% of all records"
            if all_total
            else "No survey records yet"
        )
        self.total_metric.set_metric(total, response_detail)

        valid = int(overview.get("valid_sqd_answers") or 0)
        positive = int(overview.get("positive_sqd_answers") or 0)
        average = overview.get("average_rating")
        coverage = overview.get("answer_coverage_rate")
        overall_detail = (
            f"{positive}/{valid} positive · Avg {_average_text(average)} · "
            f"{_score_text(coverage)} answered"
            if valid
            else f"0 valid ratings · {_score_text(coverage)} answered"
        )
        self.overall_metric.set_metric(_score_text(overview.get("positive_rate")), overall_detail)

        self.band_metric.set_metric(
            overview.get("rating") or "No Data",
            _band_detail(overview.get("next_band")),
        )
        sqd0 = overview.get("sqd0") or {}
        sqd0_valid = int(sqd0.get("valid_responses") or 0)
        sqd0_positive = int(sqd0.get("positive_responses") or 0)
        self.sqd0_metric.set_metric(
            _score_text(sqd0.get("positive_rate")),
            f"{sqd0_positive}/{sqd0_valid} positive · Avg {_average_text(sqd0.get('average_rating'))} · "
            f"{int(sqd0.get('not_applicable') or 0)} N/A",
        )

        dimensions = analysis.get("dimensions", [])
        dimension_data = [
            (f"{row['code']}  {row['dimension']}", row["positive_rate"])
            for row in dimensions
            if row.get("positive_rate") is not None
        ]
        self.dimension_chart.set_data(dimension_data)
        scored_dimensions = [row for row in dimensions if row.get("positive_rate") is not None]
        if scored_dimensions:
            best = max(scored_dimensions, key=lambda row: (float(row["positive_rate"]), row["code"]))
            focus = min(scored_dimensions, key=lambda row: (float(row["positive_rate"]), row["code"]))
            context = (
                f"All scored at {_score_text(best.get('positive_rate'))}"
                if best.get("positive_rate") == focus.get("positive_rate")
                else f"Best {best['code']} {_score_text(best.get('positive_rate'))} · Focus {focus['code']} {_score_text(focus.get('positive_rate'))}"
            )
            self.dimension_chart.set_context(context)
            evidence = "; ".join(
                f"{row['code']} {row['dimension']}: {_score_text(row.get('positive_rate'))}, "
                f"{int(row.get('positive_responses') or 0)}/{int(row.get('valid_responses') or 0)} positive, "
                f"average {_average_text(row.get('average_rating'))}, {row.get('rating') or 'No Data'}"
                for row in scored_dimensions
            )
            self.dimension_chart.setToolTip(evidence)
        else:
            self.dimension_chart.set_context("")

        donut_data = [
            ("Positive", mix.get("positive_count", 0), theme.SUCCESS),
            ("Neutral", mix.get("neutral_count", 0), theme.WARNING),
            ("Negative", mix.get("negative_count", 0), theme.DANGER),
            ("N/A", mix.get("not_applicable", 0), theme.TEXT_MUTED),
            ("Unanswered", mix.get("unanswered", 0), theme.PURPLE),
        ]
        self.donut_chart.set_data(donut_data, center_label="SQD ANSWERS")
        self.donut_chart.set_context(
            f"{int(mix.get('answered_count') or 0)}/{int(mix.get('total_possible_answers') or 0)} answered · "
            f"{int(mix.get('unanswered') or 0)} missing"
        )
        trend_rows = analysis.get("trend", [])
        scored_trend_rows = [row for row in trend_rows if row.get("positive_rate") is not None]
        trend_data = [
            (row.get("label") or row.get("period"), row.get("positive_rate"))
            for row in scored_trend_rows
        ]
        self.trend_chart.set_data(trend_data)
        if scored_trend_rows:
            latest = scored_trend_rows[-1]
            change = latest.get("change")
            baseline = len(scored_trend_rows) == 1
            lead = "Baseline" if baseline else "Latest"
            change_text = (
                "This is the only scored period; a trend line requires at least two scored periods"
                if baseline
                else f"{float(change):+0.2f} pts vs previous reported period"
            )
            self.trend_chart.setToolTip(
                f"{lead} {latest.get('label') or latest.get('period')}: {_score_text(latest.get('positive_rate'))}; "
                f"{int(latest.get('total_responses') or 0)} response(s); "
                f"{int(latest.get('positive_responses') or 0)}/{int(latest.get('valid_responses') or 0)} positive; "
                f"average {_average_text(latest.get('average_rating'))}; {change_text}."
            )

        cc = analysis.get("citizen_charter", {})
        by_mode = cc.get("by_mode") or {}
        self.cc_summary.setText(
            f"{int(cc.get('responses') or 0)} responses · "
            f"{int((by_mode.get('onsite') or {}).get('responses') or 0)} onsite · "
            f"{int((by_mode.get('online') or {}).get('responses') or 0)} online · denominators vary by branching"
        )
        self.cc_awareness.set_rate(
            cc.get("awareness_rate"), cc.get("awareness_valid", 0), cc.get("awareness_positive", 0)
        )
        self.cc_saw.set_rate(
            cc.get("saw_charter_rate"), cc.get("saw_charter_valid", 0), cc.get("saw_charter_positive", 0)
        )
        self.cc_easy.set_rate(
            cc.get("easy_to_see_rate"), cc.get("easy_to_see_valid", 0), cc.get("easy_to_see_positive", 0)
        )
        self.cc_helpful.set_rate(
            cc.get("helpfulness_rate"), cc.get("helpfulness_valid", 0), cc.get("helpfulness_positive", 0)
        )
        self._render_demographics(analysis.get("demographics", {}))
        self._render_services(analysis.get("services", []))
        self._render_insights(analysis.get("insights", []))
        self._render_feedback(
            analysis.get("feedback", []),
            total=total,
            feedback_count=int(overview.get("feedback_count") or 0),
        )
        self._render_recent(analysis.get("recent", []))

    def _render_demographics(self, demographics: dict[str, Any]) -> None:
        self._clear_box(self.demographics_box)
        rows: list[tuple[str, list[dict[str, Any]]]] = []
        for title, key in (("Client type", "client_type"), ("Sex", "sex"), ("Age", "age_group"), ("Region", "region")):
            values = demographics.get(key) or []
            if values:
                rows.append((title, values))
        if not rows:
            self.demographics_box.addWidget(EmptyState("No client profile yet", "Optional demographic answers will appear here."))
            return
        for title, values in rows:
            self.demographics_box.addWidget(ProfileBreakdownRow(title, values))

    def _render_services(self, services: list[dict[str, Any]]) -> None:
        self.services_table.setRowCount(len(services))
        for row_index, row in enumerate(services):
            count = int(row.get("count") or 0)
            share = float(row.get("percent") or 0)
            values = [
                row.get("service"),
                f"{count} · {share:.1f}%",
                _score_text(row.get("positive_rate")),
                row.get("rating"),
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value if value is not None else "--"))
                if column in (1, 2):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                item.setToolTip(
                    f"{row.get('service') or 'Unspecified service'} · {count} response(s), {share:.1f}% of filtered responses · "
                    f"{int(row.get('positive_responses') or 0)}/{int(row.get('valid_responses') or 0)} positive · "
                    f"average {_average_text(row.get('average_rating'))} · {row.get('rating') or 'No Data'} · "
                    f"{int(row.get('not_applicable') or 0)} N/A · {int(row.get('unanswered') or 0)} unanswered"
                )
                self.services_table.setItem(row_index, column, item)
        self.services_table.setVisible(bool(services))
        self._fit_table(self.services_table, len(services), visible_rows=6)
        if not services:
            self.services_summary.setText("No service results in the current view.")
            return
        top = services[0]
        top_service = str(top.get("service") or "Unspecified service")
        scored = [row for row in services if row.get("positive_rate") is not None]
        best = max(scored, key=lambda row: float(row["positive_rate"])) if scored else None
        best_service = str(best.get("service") or "Unspecified service") if best is not None else ""
        best_text = (
            f" · Highest score: {_compact_text(best_service, 38)} ({_score_text(best.get('positive_rate'))})"
            if best is not None
            else ""
        )
        summary = (
            f"{len(services)} represented · Most selected: {_compact_text(top_service, 38)} "
            f"({int(top.get('count') or 0)}, {float(top.get('percent') or 0):.1f}%){best_text}. "
            "A response may include multiple services, so shares can total over 100%."
        )
        full_summary = (
            f"{len(services)} represented. Most selected: {top_service} "
            f"({int(top.get('count') or 0)}, {float(top.get('percent') or 0):.1f}%). "
            + (
                f"Highest score: {best_service} ({_score_text(best.get('positive_rate'))}). "
                if best is not None
                else ""
            )
            + "A response may include multiple services, so shares can total over 100%."
        )
        self.services_summary.setText(summary)
        self.services_summary.setToolTip(full_summary)

    def _render_insights(self, insights: list[dict[str, Any]]) -> None:
        self._clear_box(self.insights_box)
        self.insights_summary.setText(
            f"{len(insights)} live observation{'s' if len(insights) != 1 else ''} · scroll to review every evidence-backed item"
            if insights
            else "No observations are available for the current view."
        )
        self.insights_scroll.setFixedHeight(max(120, min(260, len(insights) * 80)))
        colors = {"positive": theme.SUCCESS, "warning": theme.WARNING, "info": theme.ACCENT_CYAN}
        for insight in insights:
            row = QFrame()
            row.setObjectName("insight_row")
            layout = QHBoxLayout(row)
            layout.setContentsMargins(8, 6, 8, 6)
            layout.setSpacing(7)
            dot = QLabel("●")
            dot.setStyleSheet(f"color: {colors.get(insight.get('severity'), theme.ACCENT_CYAN)};")
            layout.addWidget(dot, 0, Qt.AlignmentFlag.AlignTop)
            copy = QLabel(f"<b>{insight.get('title', '')}</b><br>{insight.get('message', '')}")
            copy.setObjectName("insight_copy")
            copy.setWordWrap(True)
            copy.setToolTip(f"{insight.get('title', '')}. {insight.get('message', '')}")
            layout.addWidget(copy, 1)
            self.insights_box.addWidget(row)
        self.insights_box.addStretch(1)

    def _render_feedback(
        self,
        feedback: list[dict[str, Any]],
        *,
        total: int,
        feedback_count: int,
    ) -> None:
        self._clear_box(self.feedback_box)
        feedback_rate = (feedback_count / total * 100) if total else 0.0
        self.feedback_summary.setText(
            f"{feedback_count} of {total} responses include comments ({feedback_rate:.1f}%) · "
            f"showing the latest {min(3, len(feedback))}"
            if total
            else "No filtered response includes written feedback."
        )
        if not feedback:
            self.feedback_box.addWidget(EmptyState("No written feedback", "Suggestions and remarks in filtered responses will appear here."))
            return
        for item in feedback[:3]:
            row = QFrame()
            row.setObjectName("feedback_row")
            layout = QVBoxLayout(row)
            layout.setContentsMargins(9, 6, 9, 6)
            layout.setSpacing(2)
            full_context = (
                f"{str(item.get('mode') or '').title()} · {item.get('client_type') or 'Not Specified'} · "
                f"{item.get('service', 'Unspecified service')} · {item.get('date') or 'No date'} · "
                f"{item.get('control_number') or 'No control no.'}"
            )
            context = QLabel(_compact_text(full_context, 118))
            context.setObjectName("feedback_context")
            context.setWordWrap(True)
            context.setToolTip(full_context)
            layout.addWidget(context)
            full_text = str(item.get("text") or "")
            text = QLabel(_compact_text(full_text, 150))
            text.setObjectName("feedback_text")
            text.setWordWrap(True)
            text.setToolTip(full_text)
            text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(text)
            self.feedback_box.addWidget(row)

    def _render_recent(self, recent: list[dict[str, Any]]) -> None:
        self.recent_table.setRowCount(len(recent))
        for row_index, row in enumerate(recent):
            values = [
                row.get("date") or "--",
                str(row.get("mode") or "").title(),
                row.get("service"),
                f"{_score_text(row.get('positive_rate'))} · {row.get('rating') or 'No Data'}",
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value or "--"))
                item.setToolTip(
                    f"{row.get('date') or 'No date'} · {row.get('control_number') or 'No control no.'} · "
                    f"{str(row.get('mode') or '').title()} · {row.get('client_type') or 'Not Specified'} · "
                    f"{row.get('service') or 'Unspecified service'} · {_score_text(row.get('positive_rate'))} "
                    f"{row.get('rating') or 'No Data'} · average {_average_text(row.get('average_rating'))}"
                )
                self.recent_table.setItem(row_index, column, item)
        self.recent_table.setVisible(bool(recent))
        self._fit_table(self.recent_table, len(recent), visible_rows=5)
        self.recent_summary.setText(
            f"{len(recent)} latest filtered entr{'y' if len(recent) == 1 else 'ies'} · newest {recent[0].get('date') or 'undated'}"
            if recent
            else "No recent entries in the current view."
        )

    @staticmethod
    def _fit_table(table: QTableWidget, row_count: int, *, visible_rows: int) -> None:
        rows = max(1, min(max(0, row_count), max(1, visible_rows)))
        height = table.horizontalHeader().height() + rows * table.verticalHeader().defaultSectionSize() + 5
        table.setFixedHeight(max(88, height))

    @staticmethod
    def _key_value_row(label: str, value: str) -> QFrame:
        row = QFrame()
        row.setObjectName("key_value_row")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(10, 8, 10, 8)
        left = QLabel(label)
        left.setObjectName("key_value_label")
        layout.addWidget(left)
        right = QLabel(value)
        right.setObjectName("key_value_value")
        right.setWordWrap(True)
        right.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(right, 1)
        return row

    @staticmethod
    def _clear_box(layout: QVBoxLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self._reflow()

    def _reflow(self, force: bool = False) -> None:
        filter_wide = self.width() >= 1160
        if force or filter_wide != self._filters_wide:
            self._filters_wide = filter_wide
            while self.filter_layout.count():
                self.filter_layout.takeAt(0)
            if filter_wide:
                self.filter_layout.addWidget(self.filter_copy, 0, 0)
                self.filter_layout.addWidget(self.period_combo, 0, 1)
                self.filter_layout.addWidget(self.month_combo, 0, 2)
                self.filter_layout.addWidget(self.date_range_widget, 0, 2, 1, 2)
                self.filter_layout.addWidget(self.mode_combo, 0, 4)
                self.filter_layout.addWidget(self.client_combo, 0, 5)
                self.filter_layout.addWidget(self.service_combo, 0, 6)
                self.filter_layout.addWidget(self.reset_filters_button, 0, 7)
                self.filter_layout.setColumnStretch(6, 1)
            else:
                self.filter_layout.addWidget(self.filter_copy, 0, 0)
                self.filter_layout.addWidget(self.period_combo, 0, 1)
                self.filter_layout.addWidget(self.month_combo, 0, 2)
                self.filter_layout.addWidget(self.reset_filters_button, 0, 3)
                self.filter_layout.addWidget(self.date_range_widget, 1, 0, 1, 4)
                self.filter_layout.addWidget(self.mode_combo, 2, 0)
                self.filter_layout.addWidget(self.client_combo, 2, 1)
                self.filter_layout.addWidget(self.service_combo, 2, 2, 1, 2)
            self._sync_date_filter_visibility()
        width = self.width()
        layout_mode = "wide" if width >= 1120 else "medium" if width >= 900 else "narrow"
        if not force and layout_mode == self._layout_mode:
            return
        self._layout_mode = layout_mode
        while self.kpi_grid.count():
            self.kpi_grid.takeAt(0)
        columns = 4 if layout_mode in {"wide", "medium"} else 2
        for index, card in enumerate(self.metric_cards):
            self.kpi_grid.addWidget(card, index // columns, index % columns)
        while self.analysis_grid.count():
            self.analysis_grid.takeAt(0)
        if layout_mode == "wide":
            self.analysis_grid.setColumnStretch(0, 1)
            self.analysis_grid.setColumnStretch(1, 1)
            self.analysis_grid.setColumnStretch(2, 1)
            placements = [
                (self.trend_chart, 0, 0, 1, 2),
                (self.donut_chart, 0, 2, 1, 1),
                (self.dimension_chart, 1, 0, 1, 2),
                (self.cc_card, 1, 2, 1, 1),
                (self.services_card, 2, 0, 1, 2),
                (self.demographics_card, 2, 2, 1, 1),
                (self.insights_card, 3, 0, 1, 1),
                (self.feedback_card, 3, 1, 1, 1),
                (self.recent_card, 3, 2, 1, 1),
            ]
        elif layout_mode == "medium":
            self.analysis_grid.setColumnStretch(0, 1)
            self.analysis_grid.setColumnStretch(1, 1)
            self.analysis_grid.setColumnStretch(2, 0)
            placements = [
                (self.trend_chart, 0, 0, 1, 1),
                (self.donut_chart, 0, 1, 1, 1),
                (self.dimension_chart, 1, 0, 1, 2),
                (self.cc_card, 2, 0, 1, 1),
                (self.demographics_card, 2, 1, 1, 1),
                (self.services_card, 3, 0, 1, 2),
                (self.insights_card, 4, 0, 1, 1),
                (self.recent_card, 4, 1, 1, 1),
                (self.feedback_card, 5, 0, 1, 2),
            ]
        else:
            self.analysis_grid.setColumnStretch(0, 1)
            self.analysis_grid.setColumnStretch(1, 0)
            self.analysis_grid.setColumnStretch(2, 0)
            placements = [(widget, row, 0, 1, 1) for row, widget in enumerate(self.analysis_widgets)]
        for widget, row, column, row_span, column_span in placements:
            self.analysis_grid.addWidget(widget, row, column, row_span, column_span)

    def _apply_styles(self) -> None:
        self.setStyleSheet(
            f"""
            QFrame#dashboard_board, QWidget#dashboard_body {{ background: {theme.WINDOW_BG}; }}
            QFrame#dashboard_header {{
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {theme.PANEL_BG}, stop:1 {theme.WINDOW_BG_ALT});
                border: 1px solid {theme.CARD_BORDER}; border-radius: 11px;
            }}
            QLabel#board_title {{ color: {theme.TEXT_PRIMARY}; font-size: 22px; font-weight: 800; }}
            QLabel#board_subtitle {{ color: {theme.TEXT_SECONDARY}; font-size: 13px; }}
            QLabel#live_badge {{ color: {theme.SUCCESS}; background: rgba(57,219,145,0.10); border: 1px solid rgba(57,219,145,0.50); border-radius: 11px; font-size: 13px; font-weight: 900; }}
            QFrame#dashboard_filter_strip {{ background: rgba(10,29,48,0.88); border: 1px solid {theme.DIVIDER}; border-radius: 9px; }}
            QLabel#dashboard_filter_label {{ color: {theme.ACCENT_TEAL}; font-size: 13px; font-weight: 900; }}
            QLabel#dashboard_filter_summary {{ color: {theme.TEXT_SECONDARY}; font-size: 12px; font-weight: 700; }}
            QLabel#dashboard_range_label {{ color: {theme.TEXT_MUTED}; font-size: 12px; font-weight: 800; }}
            QDateEdit#dashboard_date_from, QDateEdit#dashboard_date_to {{ min-width: 104px; }}
            QComboBox#dashboard_filter_combo {{ min-height: 36px; font-size: 13px; }}
            QFrame#dashboard_rate_row {{ background: transparent; }}
            QLabel#dashboard_rate_label {{ color: {theme.TEXT_SECONDARY}; font-size: 13px; font-weight: 700; }}
            QLabel#dashboard_rate_value {{ color: {theme.TEXT_PRIMARY}; font-size: 13px; font-weight: 800; }}
            QProgressBar#dashboard_rate_bar {{ background: rgba(5,19,33,0.9); border: none; border-radius: 4px; }}
            QProgressBar#dashboard_rate_bar::chunk {{ background: {theme.ACCENT_TEAL}; border-radius: 4px; }}
            QLabel#dashboard_section_summary {{ color: {theme.ACCENT_CYAN}; font-size: 12px; font-weight: 700; }}
            QFrame#profile_breakdown_row {{ background: rgba(7,25,42,0.62); border: 1px solid rgba(27,85,114,0.38); border-radius: 7px; }}
            QLabel#profile_breakdown_title {{ color: {theme.TEXT_SECONDARY}; font-size: 12px; font-weight: 800; }}
            QLabel#profile_breakdown_primary {{ color: {theme.TEXT_PRIMARY}; font-size: 13px; font-weight: 800; }}
            QLabel#profile_breakdown_secondary {{ color: {theme.TEXT_SECONDARY}; font-size: 12px; }}
            QProgressBar#profile_breakdown_bar {{ background: rgba(5,19,33,0.9); border: none; border-radius: 3px; }}
            QProgressBar#profile_breakdown_bar::chunk {{ background: {theme.ACCENT_CYAN}; border-radius: 3px; }}
            QTableWidget#dashboard_table {{ background: rgba(5,19,33,0.62); alternate-background-color: rgba(13,41,64,0.5); border: 1px solid {theme.DIVIDER}; border-radius: 7px; gridline-color: {theme.DIVIDER}; font-size: 13px; }}
            QHeaderView::section {{ background: {theme.CARD_BG}; color: {theme.TEXT_SECONDARY}; border: none; border-right: 1px solid {theme.DIVIDER}; padding: 7px; font-size: 13px; font-weight: 800; }}
            QFrame#insight_row, QFrame#feedback_row, QFrame#key_value_row {{ background: rgba(7,25,42,0.62); border: 1px solid rgba(27,85,114,0.38); border-radius: 8px; }}
            QLabel#insight_copy, QLabel#feedback_text {{ color: {theme.TEXT_SECONDARY}; font-size: 13px; }}
            QLabel#feedback_context, QLabel#key_value_label {{ color: {theme.TEXT_SECONDARY}; font-size: 12px; font-weight: 700; }}
            QLabel#key_value_value {{ color: {theme.TEXT_PRIMARY}; font-size: 13px; font-weight: 700; }}
            QFrame#dashboard_board QFrame#csm_empty_state {{ min-height: 88px; }}
            QScrollArea#dashboard_scroll, QScrollArea#dashboard_inline_scroll {{ border: none; background: transparent; }}
            QWidget#dashboard_inline_scroll_content {{ background: transparent; }}
            """
        )
