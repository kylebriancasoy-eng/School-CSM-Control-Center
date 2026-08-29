from __future__ import annotations

from datetime import date, datetime
from typing import Any

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QGridLayout,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from school_csm_control_center.services.analysis_service import AnalysisService
from school_csm_control_center.storage.print_history_store import PrintHistoryStore
from school_csm_control_center.school_services import (
    normalize_service_values,
    service_display,
    service_value_from_record,
)
from school_csm_control_center.ui import theme
from school_csm_control_center.ui.controls import NoWheelComboBox
from school_csm_control_center.ui.dashboard_printing import code39_modules
from school_csm_control_center.ui.widgets import EmptyState, TooltipIconButton


class HistoryBoard(QFrame):
    view_requested = Signal(object)
    print_view_requested = Signal(object)
    narrative_requested = Signal(object)
    reprint_requested = Signal(object)
    edit_requested = Signal(object)
    delete_requested = Signal(object)
    export_requested = Signal(object)
    import_requested = Signal()
    clear_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("history_board")
        self._records: list[dict[str, Any]] = []
        self._filtered: list[dict[str, Any]] = []
        self._print_records: list[dict[str, Any]] = []
        self._filtered_prints: list[dict[str, Any]] = []
        self._section = "surveys"
        self._filters_wide = True
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 20)
        root.setSpacing(14)

        header = QFrame()
        header.setObjectName("history_header")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(20, 15, 16, 15)
        header_layout.setSpacing(10)
        copy = QVBoxLayout()
        copy.setSpacing(3)
        title_row = QHBoxLayout()
        title = QLabel("History")
        title.setObjectName("history_title")
        title_row.addWidget(title)
        self.count_badge = QLabel("0 RECORDS")
        self.count_badge.setObjectName("history_count_badge")
        self.count_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.count_badge.setMinimumWidth(78)
        self.count_badge.setFixedHeight(22)
        title_row.addWidget(self.count_badge)
        title_row.addStretch(1)
        copy.addLayout(title_row)
        self.subtitle_label = QLabel("Every entered CSM response, searchable and ready to review")
        self.subtitle_label.setObjectName("history_subtitle")
        copy.addWidget(self.subtitle_label)
        header_layout.addLayout(copy, 1)

        self.survey_results_button = TooltipIconButton(
            "history",
            "Show entered survey results",
        )
        self.survey_results_button.setCheckable(True)
        self.survey_results_button.setChecked(True)
        self.survey_results_button.clicked.connect(
            lambda: self.show_section("surveys")
        )
        header_layout.addWidget(self.survey_results_button)
        self.prints_button = TooltipIconButton(
            "print",
            "Show successful Dashboard prints",
        )
        self.prints_button.setCheckable(True)
        self.prints_button.clicked.connect(lambda: self.show_section("prints"))
        header_layout.addWidget(self.prints_button)

        self.view_button = TooltipIconButton("view", "View selected survey result")
        self.view_button.clicked.connect(self._emit_view)
        header_layout.addWidget(self.view_button)
        self.edit_button = TooltipIconButton("edit", "Edit selected survey result")
        self.edit_button.clicked.connect(self._emit_edit)
        header_layout.addWidget(self.edit_button)
        self.delete_button = TooltipIconButton("delete", "Delete selected survey result", icon_color=theme.DANGER, role="danger")
        self.delete_button.clicked.connect(self._emit_delete)
        header_layout.addWidget(self.delete_button)
        self.import_button = TooltipIconButton("upload", "Import CSM survey-history CSV")
        self.import_button.clicked.connect(self.import_requested)
        header_layout.addWidget(self.import_button)
        self.clear_button = TooltipIconButton(
            "delete",
            "Clear survey history by date range",
            icon_color=theme.DANGER,
            role="danger",
        )
        self.clear_button.clicked.connect(self.clear_requested)
        header_layout.addWidget(self.clear_button)
        self.export_button = TooltipIconButton("export", "Export filtered history")
        self.export_button.clicked.connect(lambda: self.export_requested.emit(list(self._filtered)))
        header_layout.addWidget(self.export_button)
        root.addWidget(header)

        self.survey_filters_panel = QFrame()
        self.survey_filters_panel.setObjectName("history_filters")
        self.filter_layout = QGridLayout(self.survey_filters_panel)
        self.filter_layout.setContentsMargins(14, 10, 12, 10)
        self.filter_layout.setHorizontalSpacing(10)
        self.filter_layout.setVerticalSpacing(8)
        self.search_input = QLineEdit()
        self.search_input.setObjectName("history_search")
        self.search_input.setPlaceholderText("Search control number, source, scanner operator, service, region, client, or feedback...")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.textChanged.connect(self._apply_filters)
        self.mode_combo = NoWheelComboBox()
        self.mode_combo.addItem("All modes", "")
        self.mode_combo.addItem("Onsite", "onsite")
        self.mode_combo.addItem("Online", "online")
        self.mode_combo.currentIndexChanged.connect(self._apply_filters)
        self.source_combo = NoWheelComboBox()
        self.source_combo.addItem("All sources", "")
        self.source_combo.addItem("Browser Survey", "WBS")
        self.source_combo.addItem("Scanned Hardcopy", "MRS")
        self.source_combo.addItem("Manual Entry", "SCC")
        self.source_combo.currentIndexChanged.connect(self._apply_filters)
        self.operator_combo = NoWheelComboBox()
        self.operator_combo.setMinimumWidth(170)
        self.operator_combo.addItem("All Scanner Operators", "")
        self.operator_combo.currentIndexChanged.connect(self._apply_filters)
        self.period_combo = NoWheelComboBox()
        for label, value in (("All dates", "all"), ("This month", "month"), ("This quarter", "quarter"), ("This year", "year")):
            self.period_combo.addItem(label, value)
        self.period_combo.currentIndexChanged.connect(self._apply_filters)
        self.service_combo = NoWheelComboBox()
        self.service_combo.setMinimumWidth(190)
        self.service_combo.currentIndexChanged.connect(self._apply_filters)
        self.reset_button = TooltipIconButton("reset", "Reset History filters", button_size=36, icon_size=19)
        self.reset_button.clicked.connect(self.reset_filters)
        root.addWidget(self.survey_filters_panel)

        self.print_filters_panel = QFrame()
        self.print_filters_panel.setObjectName("history_print_filters")
        self.print_filter_layout = QGridLayout(self.print_filters_panel)
        self.print_filter_layout.setContentsMargins(14, 10, 12, 10)
        self.print_filter_layout.setHorizontalSpacing(10)
        self.print_filter_layout.setVerticalSpacing(8)
        self.print_search_input = QLineEdit()
        self.print_search_input.setObjectName("history_print_search")
        self.print_search_input.setPlaceholderText(
            "Search print control number, printer, scope, setting, or barcode..."
        )
        self.print_search_input.setClearButtonEnabled(True)
        self.print_search_input.textChanged.connect(self._apply_print_filters)
        self.print_period_combo = NoWheelComboBox()
        for label, value in (
            ("All print dates", "all"),
            ("This month", "month"),
            ("This quarter", "quarter"),
            ("This year", "year"),
        ):
            self.print_period_combo.addItem(label, value)
        self.print_period_combo.currentIndexChanged.connect(
            self._apply_print_filters
        )
        self.print_reset_button = TooltipIconButton(
            "reset",
            "Reset print-history filters",
            button_size=36,
            icon_size=19,
        )
        self.print_reset_button.clicked.connect(self.reset_print_filters)
        root.addWidget(self.print_filters_panel)

        self.table = QTableWidget(0, 13)
        self.table.setObjectName("history_table")
        self.table.setHorizontalHeaderLabels(
            ["Date", "Control No.", "Source", "Scanned By", "Mode", "Client type", "Age", "Sex", "Region", "Service", "SQD score", "Band", "Feedback"]
        )
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(42)
        self.table.horizontalHeader().setMinimumSectionSize(76)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(9, QHeaderView.ResizeMode.Stretch)
        self.table.setSortingEnabled(True)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.cellDoubleClicked.connect(lambda *_: self._emit_view())
        root.addWidget(self.table, 1)

        self.empty_state = EmptyState(
            "No matching survey history",
            "Add a survey result or change the current search and filters.",
        )
        self.empty_state.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        root.addWidget(self.empty_state, 1)

        self.prints_table = QTableWidget(0, 9)
        self.prints_table.setObjectName("history_prints_table")
        self.prints_table.setHorizontalHeaderLabels(
            [
                "Printed",
                "Print Control No.",
                "Scope",
                "Pages",
                "Printer",
                "Paper",
                "Orientation",
                "Barcode",
                "Actions",
            ]
        )
        self.prints_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.prints_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.prints_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.prints_table.setAlternatingRowColors(True)
        self.prints_table.verticalHeader().setVisible(False)
        self.prints_table.verticalHeader().setDefaultSectionSize(48)
        self.prints_table.horizontalHeader().setMinimumSectionSize(76)
        self.prints_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self.prints_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch
        )
        self.prints_table.horizontalHeader().setSectionResizeMode(
            7, QHeaderView.ResizeMode.Stretch
        )
        self.prints_table.horizontalHeader().setSectionResizeMode(
            8, QHeaderView.ResizeMode.ResizeToContents
        )
        self.prints_table.setIconSize(QSize(128, 24))
        self.prints_table.setSortingEnabled(True)
        self.prints_table.itemSelectionChanged.connect(self._selection_changed)
        self.prints_table.cellDoubleClicked.connect(lambda *_: self._emit_view())
        root.addWidget(self.prints_table, 1)

        self.prints_empty_state = EmptyState(
            "No Dashboard print activity",
            "Open Print Preview to create the first traceable print-audit record.",
        )
        self.prints_empty_state.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        root.addWidget(self.prints_empty_state, 1)
        self._set_actions_enabled(False)
        self._apply_styles()
        self._reflow_filters(force=True)
        self._sync_section_ui()

    def refresh(self, records: list[dict[str, Any]]) -> None:
        self._records = list(records)
        self._refresh_service_filter()
        self._refresh_operator_filter()
        self._apply_filters()

    @property
    def current_section(self) -> str:
        """Return ``surveys`` or ``prints`` for the visible History section."""

        return self._section

    def refresh_prints(self, records: list[dict[str, Any]]) -> None:
        """Replace Dashboard print-audit history and refresh its view."""

        self._print_records = [
            dict(record) for record in records if isinstance(record, dict)
        ]
        self._apply_print_filters()

    def show_section(self, section: str) -> None:
        wanted = str(section or "").strip().casefold()
        if wanted not in {"surveys", "prints"}:
            raise ValueError("History section must be 'surveys' or 'prints'.")
        self._section = wanted
        self._sync_section_ui()
        if wanted == "prints":
            self._apply_print_filters()
        else:
            self._apply_filters()

    def reset_print_filters(self) -> None:
        self.print_search_input.clear()
        self.print_period_combo.blockSignals(True)
        self.print_period_combo.setCurrentIndex(0)
        self.print_period_combo.blockSignals(False)
        self._apply_print_filters()

    def reset_filters(self) -> None:
        self.search_input.clear()
        for combo in (self.mode_combo, self.source_combo, self.operator_combo, self.period_combo, self.service_combo):
            combo.blockSignals(True)
            combo.setCurrentIndex(0)
            combo.blockSignals(False)
        self._apply_filters()

    def selected_record(self) -> dict[str, Any] | None:
        if self._section != "surveys":
            return None
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        record_id = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        return next((record for record in self._filtered if record.get("id") == record_id), None)

    def selected_print_record(self) -> dict[str, Any] | None:
        if self._section != "prints":
            return None
        row = self.prints_table.currentRow()
        if row < 0:
            return None
        item = self.prints_table.item(row, 0)
        record_id = (
            item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        )
        return next(
            (
                record
                for record in self._filtered_prints
                if record.get("id") == record_id
            ),
            None,
        )

    def _refresh_service_filter(self) -> None:
        current = self.service_combo.currentData()
        services = sorted(
            {
                service
                for record in self._records
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

    def _refresh_operator_filter(self) -> None:
        current = self.operator_combo.currentData()
        operators: dict[str, str] = {}
        for record in self._records:
            meta = record.get("meta") if isinstance(record.get("meta"), dict) else {}
            user_id = str(meta.get("scanner_operator_user_id") or "").strip()
            display_name = str(meta.get("scanner_operator_display_name") or "").strip()
            username = str(meta.get("scanner_operator_username") or "").strip()
            if user_id:
                operators[user_id] = display_name or username or user_id
        self.operator_combo.blockSignals(True)
        self.operator_combo.clear()
        self.operator_combo.addItem("All Scanner Operators", "")
        for user_id, label in sorted(operators.items(), key=lambda item: item[1].casefold()):
            self.operator_combo.addItem(label, user_id)
        index = self.operator_combo.findData(current)
        self.operator_combo.setCurrentIndex(index if index >= 0 else 0)
        self.operator_combo.blockSignals(False)

    def _filters(self) -> dict[str, Any]:
        filters: dict[str, Any] = {"search": self.search_input.text().strip()}
        if self.mode_combo.currentData():
            filters["mode"] = self.mode_combo.currentData()
        if self.source_combo.currentData():
            filters["source_code"] = self.source_combo.currentData()
        if self.operator_combo.currentData():
            filters["scanner_operator"] = self.operator_combo.currentData()
        if self.service_combo.currentData():
            filters["service"] = self.service_combo.currentData()
        today = date.today()
        period = str(self.period_combo.currentData() or "all")
        if period == "month":
            filters["date_from"] = today.replace(day=1).isoformat()
        elif period == "quarter":
            start_month = ((today.month - 1) // 3) * 3 + 1
            filters["date_from"] = today.replace(month=start_month, day=1).isoformat()
        elif period == "year":
            filters["date_from"] = today.replace(month=1, day=1).isoformat()
        if period != "all":
            filters["date_to"] = today.isoformat()
        return filters

    def _apply_filters(self) -> None:
        self._filtered = AnalysisService.filter_records(self._records, self._filters())
        self._populate_table()
        has_rows = bool(self._filtered)
        if self._section == "surveys":
            self.count_badge.setText(
                f"{len(self._filtered)} RECORD{'S' if len(self._filtered) != 1 else ''}"
            )
            self.table.setVisible(has_rows)
            self.empty_state.setVisible(not has_rows)
            self._set_actions_enabled(False)

    def _apply_print_filters(self) -> None:
        search = " ".join(self.print_search_input.text().split()).casefold()
        date_from, date_to = _period_bounds(
            str(self.print_period_combo.currentData() or "all")
        )
        filtered: list[dict[str, Any]] = []
        for record in self._print_records:
            generated_date = str(
                record.get("printed_at") or record.get("generated_at") or ""
            )[:10]
            if date_from and generated_date < date_from:
                continue
            if date_to and generated_date > date_to:
                continue
            if search and search not in _print_search_text(record):
                continue
            filtered.append(record)
        filtered.sort(
            key=lambda record: (
                str(record.get("printed_at") or record.get("generated_at") or ""),
                str(record.get("id") or ""),
            ),
            reverse=True,
        )
        self._filtered_prints = filtered
        self._populate_prints_table()
        if self._section == "prints":
            self.count_badge.setText(
                f"{len(filtered)} PRINT{'S' if len(filtered) != 1 else ''}"
            )
            has_rows = bool(filtered)
            self.prints_table.setVisible(has_rows)
            self.prints_empty_state.setVisible(not has_rows)
            self._set_actions_enabled(False)

    def _populate_table(self) -> None:
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(self._filtered))
        for row_index, record in enumerate(self._filtered):
            meta = record.get("meta") if isinstance(record.get("meta"), dict) else {}
            feedback = record.get("feedback") if isinstance(record.get("feedback"), dict) else {}
            analysis = AnalysisService.analyze([record])
            overview = analysis.get("overview", {})
            date_value = record.get("survey_date") or meta.get("survey_date") or str(record.get("created_at") or "")[:10] or "--"
            comments = feedback.get("comments") or feedback.get("text") or feedback.get("remarks") or ""
            source_code = str(record.get("source_code") or "").upper()
            source_key = str(meta.get("submission_source") or "manual_entry").casefold()
            source_label = {
                "WBS": "Browser Survey",
                "MRS": "Scanned Hardcopy",
                "SCC": "Manual Entry",
            }.get(
                source_code,
                {
                    "local_web_form": "Browser Survey",
                    "scanned_hardcopy": "Scanned Hardcopy",
                    "manual_entry": "Manual Entry",
                    "imported_online": "Imported Online",
                }.get(source_key, source_key.replace("_", " ").title() or "Manual Entry"),
            )
            scanned_by = str(meta.get("scanner_operator_display_name") or "--") if source_label == "Scanned Hardcopy" else "--"
            values = [
                date_value,
                record.get("control_number") or "--",
                source_label,
                scanned_by,
                str(record.get("mode") or "onsite").title(),
                meta.get("client_type") or "--",
                "--" if meta.get("age") in (None, "") else meta.get("age"),
                meta.get("sex") or "--",
                meta.get("region") or "--",
                service_display(service_value_from_record(record)),
                "--" if overview.get("positive_rate") is None else f"{overview['positive_rate']:.2f}%",
                overview.get("rating") or "No Data",
                comments or "--",
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, record.get("id"))
                if column in (2, 3, 4, 6, 10):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                item.setToolTip(str(value))
                self.table.setItem(row_index, column, item)
        self.table.setSortingEnabled(True)
        self.table.clearSelection()
        self.table.setCurrentCell(-1, -1)

    def _populate_prints_table(self) -> None:
        self.prints_table.setSortingEnabled(False)
        self.prints_table.setRowCount(len(self._filtered_prints))
        for row_index, record in enumerate(self._filtered_prints):
            settings = (
                record.get("settings")
                if isinstance(record.get("settings"), dict)
                else {}
            )
            timestamp = str(
                record.get("printed_at") or record.get("generated_at") or ""
            )
            status = str(record.get("status") or "confirmed").title()
            barcode_value = str(record.get("barcode_value") or "")
            values = [
                f"{_display_timestamp(timestamp)} · {status}",
                record.get("control_number") or "--",
                record.get("scope") or "Complete current Dashboard",
                record.get("page_count") or "--",
                record.get("printer_name")
                or settings.get("printer_name")
                or "--",
                settings.get("paper_size")
                or settings.get("paper")
                or "--",
                str(settings.get("orientation") or "--").title(),
                barcode_value or "--",
            ]
            details = _print_details_text(record)
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, record.get("id"))
                if column in (3, 5, 6):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if column == 7 and barcode_value:
                    item.setIcon(_barcode_icon(barcode_value))
                item.setToolTip(details if column == 7 else str(value))
                self.prints_table.setItem(row_index, column, item)
            eligible, reason = PrintHistoryStore.narrative_eligibility(record)
            actions = QWidget()
            actions.setObjectName("history_print_actions")
            action_layout = QHBoxLayout(actions)
            action_layout.setContentsMargins(3, 2, 3, 2)
            action_layout.setSpacing(4)
            narrative_button = TooltipIconButton(
                "edit",
                (
                    "Write or edit Narrative Report"
                    if eligible
                    else f"Narrative Report unavailable: {reason}"
                ),
                role="primary",
            )
            narrative_button.setText("Narrative")
            narrative_button.setFixedWidth(118)
            narrative_button.setEnabled(eligible)
            narrative_button.clicked.connect(
                lambda _checked=False, source=dict(record): self.narrative_requested.emit(source)
            )
            action_layout.addWidget(narrative_button)
            reprint_button = TooltipIconButton(
                "print",
                "Reprint Dashboard" if eligible else f"Reprint Dashboard unavailable: {reason}",
            )
            reprint_button.setEnabled(eligible)
            reprint_button.clicked.connect(
                lambda _checked=False, source=dict(record): self.reprint_requested.emit(source)
            )
            action_layout.addWidget(reprint_button)
            self.prints_table.setCellWidget(row_index, 8, actions)
        self.prints_table.setSortingEnabled(True)
        self.prints_table.clearSelection()
        self.prints_table.setCurrentCell(-1, -1)

    def _selection_changed(self) -> None:
        selected = (
            self.selected_print_record()
            if self._section == "prints"
            else self.selected_record()
        )
        self._set_actions_enabled(selected is not None)

    def _set_actions_enabled(self, enabled: bool) -> None:
        self.view_button.setEnabled(enabled)
        survey_enabled = enabled and self._section == "surveys"
        self.edit_button.setEnabled(survey_enabled)
        self.delete_button.setEnabled(survey_enabled)

    def _emit_view(self) -> None:
        if self._section == "prints":
            record = self.selected_print_record()
            if record is not None:
                self.print_view_requested.emit(record)
            return
        record = self.selected_record()
        if record is not None:
            self.view_requested.emit(record)

    def _emit_edit(self) -> None:
        if self._section != "surveys":
            return
        record = self.selected_record()
        if record is not None:
            self.edit_requested.emit(record)

    def _emit_delete(self) -> None:
        if self._section != "surveys":
            return
        record = self.selected_record()
        if record is not None:
            self.delete_requested.emit(record)

    def _sync_section_ui(self) -> None:
        surveys = self._section == "surveys"
        self.survey_results_button.setChecked(surveys)
        self.prints_button.setChecked(not surveys)
        self.survey_filters_panel.setVisible(surveys)
        self.print_filters_panel.setVisible(not surveys)
        self.table.setVisible(surveys and bool(self._filtered))
        self.empty_state.setVisible(surveys and not self._filtered)
        self.prints_table.setVisible(not surveys and bool(self._filtered_prints))
        self.prints_empty_state.setVisible(
            not surveys and not self._filtered_prints
        )
        self.edit_button.setVisible(surveys)
        self.delete_button.setVisible(surveys)
        self.export_button.setVisible(surveys)
        self.import_button.setVisible(surveys)
        self.clear_button.setVisible(surveys)
        self.view_button.set_action(
            "view",
            "View selected survey result"
            if surveys
            else "View selected Dashboard print details",
        )
        self.subtitle_label.setText(
            "Every entered CSM response, searchable and ready to review"
            if surveys
            else "Dashboard print attempts and completed jobs with traceable settings and barcode identity"
        )
        count = len(self._filtered) if surveys else len(self._filtered_prints)
        noun = "RECORD" if surveys else "PRINT"
        self.count_badge.setText(
            f"{count} {noun}{'S' if count != 1 else ''}"
        )
        self._set_actions_enabled(False)
        self._reflow_filters(force=True)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self._reflow_filters()

    def _reflow_filters(self, force: bool = False) -> None:
        wide = self.width() >= 1000
        if not force and wide == self._filters_wide:
            return
        self._filters_wide = wide
        while self.filter_layout.count():
            self.filter_layout.takeAt(0)
        if wide:
            self.filter_layout.addWidget(self.search_input, 0, 0, 1, 3)
            self.filter_layout.addWidget(self.mode_combo, 0, 3)
            self.filter_layout.addWidget(self.source_combo, 0, 4)
            self.filter_layout.addWidget(self.operator_combo, 0, 5)
            self.filter_layout.addWidget(self.period_combo, 0, 6)
            self.filter_layout.addWidget(self.service_combo, 0, 7)
            self.filter_layout.addWidget(self.reset_button, 0, 8)
            self.filter_layout.setColumnStretch(0, 1)
            self.filter_layout.setColumnStretch(1, 1)
            self.filter_layout.setColumnStretch(2, 1)
        else:
            self.filter_layout.addWidget(self.search_input, 0, 0, 1, 4)
            self.filter_layout.addWidget(self.mode_combo, 1, 0)
            self.filter_layout.addWidget(self.source_combo, 1, 1)
            self.filter_layout.addWidget(self.operator_combo, 1, 2)
            self.filter_layout.addWidget(self.period_combo, 2, 0)
            self.filter_layout.addWidget(self.service_combo, 2, 1, 1, 2)
            self.filter_layout.addWidget(self.reset_button, 2, 3)
            self.filter_layout.setColumnStretch(2, 1)
        while self.print_filter_layout.count():
            self.print_filter_layout.takeAt(0)
        if wide:
            self.print_filter_layout.addWidget(
                self.print_search_input, 0, 0, 1, 4
            )
            self.print_filter_layout.addWidget(self.print_period_combo, 0, 4)
            self.print_filter_layout.addWidget(self.print_reset_button, 0, 5)
            self.print_filter_layout.setColumnStretch(0, 1)
            self.print_filter_layout.setColumnStretch(1, 1)
            self.print_filter_layout.setColumnStretch(2, 1)
            self.print_filter_layout.setColumnStretch(3, 1)
        else:
            self.print_filter_layout.addWidget(
                self.print_search_input, 0, 0, 1, 2
            )
            self.print_filter_layout.addWidget(self.print_period_combo, 1, 0)
            self.print_filter_layout.addWidget(self.print_reset_button, 1, 1)
            self.print_filter_layout.setColumnStretch(0, 1)

    def _apply_styles(self) -> None:
        self.setStyleSheet(
            f"""
            QFrame#history_board {{ background: {theme.WINDOW_BG}; }}
            QFrame#history_header {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {theme.PANEL_BG}, stop:1 {theme.WINDOW_BG_ALT}); border: 1px solid {theme.CARD_BORDER}; border-radius: 14px; }}
            QLabel#history_title {{ color: {theme.TEXT_PRIMARY}; font-size: 22px; font-weight: 800; }}
            QLabel#history_subtitle {{ color: {theme.TEXT_MUTED}; font-size: 13px; }}
            QLabel#history_count_badge {{ color: {theme.ACCENT_CYAN}; background: rgba(98,219,255,0.10); border: 1px solid rgba(98,219,255,0.42); border-radius: 11px; padding: 0 9px; font-size: 12px; font-weight: 900; }}
            QFrame#history_filters, QFrame#history_print_filters {{ background: rgba(10,29,48,0.88); border: 1px solid {theme.DIVIDER}; border-radius: 11px; }}
            QTableWidget#history_table, QTableWidget#history_prints_table {{ background: rgba(5,19,33,0.72); alternate-background-color: rgba(13,41,64,0.56); border: 1px solid {theme.CARD_BORDER}; border-radius: 10px; gridline-color: {theme.DIVIDER}; selection-background-color: rgba(22,142,136,0.72); }}
            QTableWidget#history_table::item, QTableWidget#history_prints_table::item {{ padding: 7px; color: {theme.TEXT_SECONDARY}; }}
            QHeaderView::section {{ background: {theme.CARD_BG}; color: {theme.TEXT_PRIMARY}; border: none; border-right: 1px solid {theme.DIVIDER}; padding: 8px; font-size: 12px; font-weight: 800; }}
            """
        )


def _period_bounds(period: str) -> tuple[str, str]:
    today = date.today()
    if period == "month":
        return today.replace(day=1).isoformat(), today.isoformat()
    if period == "quarter":
        start_month = ((today.month - 1) // 3) * 3 + 1
        return (
            today.replace(month=start_month, day=1).isoformat(),
            today.isoformat(),
        )
    if period == "year":
        return today.replace(month=1, day=1).isoformat(), today.isoformat()
    return "", ""


def _print_search_text(record: dict[str, Any]) -> str:
    settings = record.get("settings")
    values = [
        record.get("control_number"),
        record.get("generated_at"),
        record.get("printed_at"),
        record.get("scope"),
        record.get("page_count"),
        record.get("printer_name"),
        record.get("barcode_value"),
        settings,
    ]
    return " ".join(str(value or "") for value in values).casefold()


def _display_timestamp(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return "--"
    try:
        timestamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    if timestamp.tzinfo is not None:
        timestamp = timestamp.astimezone()
    return timestamp.strftime("%Y-%m-%d %I:%M %p")


def _print_details_text(record: dict[str, Any]) -> str:
    settings = (
        record.get("settings")
        if isinstance(record.get("settings"), dict)
        else {}
    )
    selected_pages = (
        record.get("selected_pages")
        or settings.get("selected_pages")
        or settings.get("page_range")
        or "All pages"
    )
    parts = [
        f"Print control number: {record.get('control_number') or '--'}",
        f"Audit status: {str(record.get('status') or 'confirmed').title()}",
        f"Status detail: {record.get('status_message') or '--'}",
        f"Printed: {_display_timestamp(str(record.get('printed_at') or record.get('generated_at') or ''))}",
        f"Scope: {record.get('scope') or 'Complete current Dashboard'}",
        f"Pages: {record.get('page_count') or '--'}; selected {selected_pages}",
        f"Printer: {record.get('printer_name') or settings.get('printer_name') or '--'}",
        f"Paper: {settings.get('paper_size') or settings.get('paper') or '--'}",
        f"Orientation: {str(settings.get('orientation') or '--').title()}",
        f"Copies: {record.get('copies') or settings.get('copies') or 1}",
        f"Color: {settings.get('color_mode') or settings.get('color') or '--'}",
        f"Duplex: {settings.get('duplex') or '--'}",
        f"Resolution: {settings.get('resolution_dpi') or settings.get('resolution') or '--'} dpi",
        f"Scaling: {settings.get('scale_mode') or settings.get('scaling') or '--'}",
        f"Survey responses: {record.get('survey_count') if record.get('survey_count') is not None else '--'}",
        f"Barcode value: {record.get('barcode_value') or '--'}",
    ]
    return "\n".join(parts)


def _barcode_icon(value: str) -> QIcon:
    try:
        modules = code39_modules(value)
    except ValueError:
        return QIcon()
    width = 142
    height = 26
    quiet = 8
    pixmap = QPixmap(width, height)
    pixmap.fill(QColor("#F3F8FB"))
    painter = QPainter(pixmap)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#07131F"))
    module_width = width / max(1, len(modules) + quiet * 2)
    left = quiet * module_width
    run_start = 0
    while run_start < len(modules):
        run_value = modules[run_start]
        run_end = run_start + 1
        while run_end < len(modules) and modules[run_end] == run_value:
            run_end += 1
        if run_value:
            painter.drawRect(
                int(left + run_start * module_width),
                2,
                max(1, int((run_end - run_start) * module_width + 0.5)),
                height - 4,
            )
        run_start = run_end
    painter.end()
    return QIcon(pixmap)
