from __future__ import annotations

"""Controlled MRS printing, physical-copy accounting, and registry dashboard."""

from datetime import datetime
import os
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from school_csm_control_center.mrs_diagnostics import (
    format_mrs_self_check_report,
    run_mrs_software_self_check,
    write_mrs_self_check_report,
)
from school_csm_control_center.mrs_printing import (
    MRSPrintingError,
    available_mrs_templates,
    discover_physical_printers,
    print_official_mrs_pages,
    template_spec,
    verify_physical_printer,
)
from school_csm_control_center.storage.mrs_registry import (
    MRSRegistryError,
    MRSRegistryStore,
)
from school_csm_control_center.storage.mrs_field_tests import MRSFieldTestStore
from school_csm_control_center.ui import theme
from school_csm_control_center.ui.controls import NoWheelComboBox, NoWheelSpinBox
from school_csm_control_center.ui.overlays import OverlayPrompt
from school_csm_control_center.ui.widgets import MetricCard, SectionCard, TooltipIconButton


class MRSFailedPrintOverlay(QWidget):
    """In-window, scrollable post-print accounting overlay."""

    confirmed = Signal(object)
    cancelled = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("mrs_failed_print_overlay")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.hide()
        self._rows: list[tuple[str, QCheckBox]] = []

        self.scrim = QFrame(self)
        self.scrim.setObjectName("mrs_failed_print_scrim")
        self.panel = QFrame(self)
        self.panel.setObjectName("mrs_failed_print_panel")
        root = QVBoxLayout(self.panel)
        root.setContentsMargins(22, 20, 22, 18)
        root.setSpacing(11)

        title_row = QHBoxLayout()
        title = QLabel("CONFIRM MRS PRINTING RESULTS")
        title.setObjectName("mrs_failed_print_title")
        title.setWordWrap(True)
        title_row.addWidget(title, 1)
        self.batch_badge = QLabel()
        self.batch_badge.setObjectName("mrs_failed_print_badge")
        self.batch_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_row.addWidget(self.batch_badge)
        root.addLayout(title_row)

        instruction = QLabel(
            "Select every control number that failed to print correctly. This includes missing pages, "
            "blank pages, incomplete pages, paper jams, severe misalignment, and other unusable output."
        )
        instruction.setObjectName("mrs_failed_print_instruction")
        instruction.setWordWrap(True)
        root.addWidget(instruction)

        self.scroll = QScrollArea()
        self.scroll.setObjectName("mrs_failed_print_scroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setMinimumHeight(250)
        self.list_widget = QWidget()
        self.list_widget.setObjectName("mrs_failed_print_list")
        self.list_layout = QVBoxLayout(self.list_widget)
        self.list_layout.setContentsMargins(8, 8, 8, 8)
        self.list_layout.setSpacing(7)
        self.list_layout.addStretch(1)
        self.scroll.setWidget(self.list_widget)
        root.addWidget(self.scroll, 1)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.none_button = TooltipIconButton("check", "None failed — every page printed successfully", role="success")
        self.none_button.clicked.connect(self._select_none)
        actions.addWidget(self.none_button)
        self.all_button = TooltipIconButton("add", "Select all control numbers as failed")
        self.all_button.clicked.connect(self._select_all)
        actions.addWidget(self.all_button)
        self.clear_button = TooltipIconButton("reset", "Clear failed-page selection")
        self.clear_button.clicked.connect(self._select_none)
        actions.addWidget(self.clear_button)
        actions.addStretch(1)
        self.cancel_button = TooltipIconButton("cancel", "Cancel confirmation and leave this batch pending", role="subtle")
        self.cancel_button.clicked.connect(self._cancel)
        actions.addWidget(self.cancel_button)
        self.confirm_button = TooltipIconButton("save", "Confirm selected failed forms", role="primary")
        self.confirm_button.clicked.connect(self._confirm)
        actions.addWidget(self.confirm_button)
        root.addLayout(actions)

        self.setStyleSheet(
            f"""
            QWidget#mrs_failed_print_overlay {{ background: transparent; }}
            QFrame#mrs_failed_print_scrim {{ background: rgba(1, 8, 18, 0.82); border: none; }}
            QFrame#mrs_failed_print_panel {{
                background: {theme.PANEL_BG}; border: 1px solid {theme.ACCENT_TEAL}; border-radius: 16px;
            }}
            QLabel#mrs_failed_print_title {{ color: {theme.TEXT_PRIMARY}; font-size: 18px; font-weight: 800; }}
            QLabel#mrs_failed_print_badge {{
                color: {theme.ACCENT_CYAN}; background: rgba(43,217,197,0.10);
                border: 1px solid rgba(43,217,197,0.45); border-radius: 8px; padding: 4px 8px; font-weight: 800;
            }}
            QLabel#mrs_failed_print_instruction {{ color: {theme.TEXT_SECONDARY}; font-size: 12px; }}
            QScrollArea#mrs_failed_print_scroll {{
                background: rgba(5,19,33,0.76); border: 1px solid {theme.CARD_BORDER}; border-radius: 10px;
            }}
            QWidget#mrs_failed_print_list {{ background: transparent; }}
            QCheckBox {{
                color: {theme.TEXT_PRIMARY}; background: rgba(13,41,64,0.68);
                border: 1px solid rgba(43,217,197,0.20); border-radius: 8px; padding: 9px 10px;
                font-weight: 700;
            }}
            """
        )

    def show_batch(self, batch: Mapping[str, Any], selected_controls: Sequence[str] = ()) -> None:
        while self.list_layout.count() > 1:
            item = self.list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._rows.clear()
        pages = list(batch.get("pages") or [])
        selected = {str(value) for value in selected_controls}
        for page in pages:
            control = str(page.get("control_number") or "")
            attempt = int(page.get("attempt_number") or 1)
            language = str(page.get("language") or "English")
            row = QCheckBox(f"{control}   ·   {language}   ·   Print attempt {attempt}")
            row.setProperty("controlNumber", control)
            row.setChecked(control in selected)
            row.setToolTip(f"Mark {control} only when its current printed page is unusable.")
            self.list_layout.insertWidget(self.list_layout.count() - 1, row)
            self._rows.append((control, row))
        self.batch_badge.setText(f"{len(pages)} PAGE{'S' if len(pages) != 1 else ''}")
        self.setGeometry(self.parentWidget().rect())
        self._position_children()
        self.show()
        self.raise_()
        self.confirm_button.setFocus(Qt.FocusReason.ActiveWindowFocusReason)

    def selected_controls(self) -> list[str]:
        return [control for control, box in self._rows if box.isChecked()]

    def _select_none(self) -> None:
        for _control, box in self._rows:
            box.setChecked(False)

    def _select_all(self) -> None:
        for _control, box in self._rows:
            box.setChecked(True)

    def _confirm(self) -> None:
        selected = self.selected_controls()
        self.hide()
        self.confirmed.emit(selected)

    def _cancel(self) -> None:
        self.hide()
        self.cancelled.emit()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._position_children()

    def _position_children(self) -> None:
        self.scrim.setGeometry(self.rect())
        width = min(820, max(420, self.width() - 48))
        height = min(690, max(390, self.height() - 48))
        self.panel.setGeometry((self.width() - width) // 2, (self.height() - height) // 2, width, height)


class MRSPrintingBoard(QFrame):
    """Direct-print MRS production and physical-form tracking board."""

    notice_requested = Signal(str, str)
    registry_changed = Signal()

    TEMPLATE_ID = MRSRegistryStore.TEMPLATE_ID

    def __init__(
        self,
        project_root: str | Path,
        *,
        settings_provider: Callable[[], Mapping[str, Any]],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.project_root = Path(project_root)
        self.settings_provider = settings_provider
        self.registry = MRSRegistryStore(self.project_root)
        self.field_tests = MRSFieldTestStore(self.project_root)
        self._printers: list[dict[str, Any]] = []
        self._active_batch: dict[str, Any] | None = None
        self._pending_failed_controls: list[str] = []
        self.setObjectName("mrs_printing_board")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.board_scroll = QScrollArea(self)
        self.board_scroll.setObjectName("mrs_board_scroll")
        self.board_scroll.setWidgetResizable(True)
        self.board_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.board_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.board_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        self.board_content = QWidget()
        self.board_content.setObjectName("mrs_board_content")
        self.board_content.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        root = QVBoxLayout(self.board_content)
        root.setContentsMargins(22, 20, 22, 20)
        root.setSpacing(13)
        self.board_scroll.setWidget(self.board_content)
        outer.addWidget(self.board_scroll, 1)

        header = QFrame()
        header.setObjectName("mrs_header")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(19, 14, 14, 14)
        header_layout.setSpacing(10)
        copy = QVBoxLayout()
        copy.setSpacing(2)
        title = QLabel("MRS Printing & Tracking")
        title.setObjectName("mrs_title")
        copy.addWidget(title)
        subtitle = QLabel("Generate, print, account for, reprint, and match every official machine-readable survey form")
        subtitle.setObjectName("mrs_subtitle")
        subtitle.setWordWrap(True)
        copy.addWidget(subtitle)
        header_layout.addLayout(copy, 1)
        self.resume_button = TooltipIconButton("view", "Resume the latest pending post-print confirmation")
        self.resume_button.clicked.connect(self._resume_pending_confirmation)
        header_layout.addWidget(self.resume_button)
        self.self_check_button = TooltipIconButton("check", "Run MRS software and template self-check")
        self.self_check_button.clicked.connect(self._run_mrs_self_check)
        header_layout.addWidget(self.self_check_button)
        self.field_test_button = TooltipIconButton("view", "View scanner field-test results")
        self.field_test_button.clicked.connect(self._show_field_test_results)
        header_layout.addWidget(self.field_test_button)
        self.refresh_button = TooltipIconButton("reset", "Refresh printers and MRS registry")
        self.refresh_button.clicked.connect(self.refresh)
        header_layout.addWidget(self.refresh_button)
        root.addWidget(header)

        metric_row = QHBoxLayout()
        metric_row.setSpacing(9)
        self.generated_metric = MetricCard("Generated", 0, "Official control numbers")
        self.awaiting_metric = MetricCard("Awaiting Scan", 0, "Printed copies shown in red", accent=theme.DANGER)
        self.reprint_metric = MetricCard("Pending Reprint", 0, "Failed or spoiled copies", accent=theme.WARNING)
        self.valid_metric = MetricCard("Valid Scans", 0, "Included in analysis", accent=theme.SUCCESS)
        self.invalid_metric = MetricCard("Invalid / Duplicate", 0, "Excluded but retained", accent=theme.PURPLE)
        for metric in (self.generated_metric, self.awaiting_metric, self.reprint_metric, self.valid_metric, self.invalid_metric):
            metric_row.addWidget(metric, 1)
        root.addLayout(metric_row)

        content_row = QHBoxLayout()
        content_row.setSpacing(12)

        print_card = SectionCard(
            "Controlled MRS Printing",
            "Official forms are rendered in memory and sent directly to a connected physical printer. No print-ready PDF is created.",
        )
        print_card.setMinimumHeight(325)
        config = QGridLayout()
        config.setContentsMargins(0, 0, 0, 0)
        config.setHorizontalSpacing(10)
        config.setVerticalSpacing(5)

        language_caption = QLabel("Language")
        language_caption.setObjectName("mrs_form_label")
        config.addWidget(language_caption, 0, 0)
        self.language_combo = NoWheelComboBox()
        self.language_combo.setObjectName("mrs_language_combo")
        self.language_combo.setMinimumWidth(150)
        self.language_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._template_specs = available_mrs_templates(self.project_root)
        for spec in self._template_specs:
            self.language_combo.addItem(spec["language"], spec["code"])
        self.language_combo.setToolTip("Select an installed official v0.4 MRS language template.")
        self.language_combo.currentIndexChanged.connect(self._language_selection_changed)
        config.addWidget(self.language_combo, 1, 0)

        count_caption = QLabel("New forms")
        count_caption.setObjectName("mrs_form_label")
        config.addWidget(count_caption, 0, 1)
        self.count_spin = NoWheelSpinBox()
        self.count_spin.setObjectName("mrs_count_spin")
        self.count_spin.setRange(0, 500)
        self.count_spin.setValue(10)
        self.count_spin.setMinimumWidth(90)
        self.count_spin.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.count_spin.setToolTip("Number of newly generated forms. Pending reprints are added first.")
        self.count_spin.valueChanged.connect(self._update_batch_preview)
        config.addWidget(self.count_spin, 1, 1)

        template_caption = QLabel("Template")
        template_caption.setObjectName("mrs_form_label")
        config.addWidget(template_caption, 0, 2)
        initial_template = self._selected_template_spec()
        self.template_label = QLabel(initial_template.get("template_id") or self.TEMPLATE_ID)
        self.template_label.setObjectName("mrs_value_label")
        self.template_label.setMinimumHeight(36)
        self.template_label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        self.template_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        config.addWidget(self.template_label, 1, 2)

        printer_caption = QLabel("Physical printer")
        printer_caption.setObjectName("mrs_form_label")
        config.addWidget(printer_caption, 2, 0, 1, 3)
        self.printer_combo = NoWheelComboBox()
        self.printer_combo.setObjectName("mrs_printer_combo")
        self.printer_combo.setMinimumWidth(0)
        self.printer_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.printer_combo.currentIndexChanged.connect(self._printer_selection_changed)
        config.addWidget(self.printer_combo, 3, 0, 1, 3)

        config.setColumnStretch(0, 3)
        config.setColumnStretch(1, 2)
        config.setColumnStretch(2, 5)
        config.setRowMinimumHeight(1, 38)
        config.setRowMinimumHeight(3, 38)
        print_card.content_layout.addLayout(config)

        self.printer_status = QLabel("Checking connected physical printers…")
        self.printer_status.setObjectName("mrs_status_label")
        self.printer_status.setWordWrap(True)
        print_card.add_widget(self.printer_status)
        self.batch_preview = QLabel()
        self.batch_preview.setObjectName("mrs_batch_preview")
        self.batch_preview.setWordWrap(True)
        self.batch_preview.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        print_card.add_widget(self.batch_preview)

        print_actions = QHBoxLayout()
        print_actions.setSpacing(8)
        self.refresh_printers_button = TooltipIconButton("reset", "Refresh connected physical printers")
        self.refresh_printers_button.clicked.connect(self.refresh_printers)
        print_actions.addWidget(self.refresh_printers_button)
        self.details_button = TooltipIconButton("info", "View selected printer details")
        self.details_button.clicked.connect(self._show_printer_details)
        print_actions.addWidget(self.details_button)
        self.settings_button = TooltipIconButton("settings", "Open Windows Printer Settings")
        self.settings_button.clicked.connect(self._open_printer_settings)
        print_actions.addWidget(self.settings_button)
        self.test_button = TooltipIconButton("play", "Test selected printer availability")
        self.test_button.clicked.connect(self._test_printer)
        print_actions.addWidget(self.test_button)
        print_actions.addStretch(1)
        self.print_button = TooltipIconButton("print", "Review and start direct MRS printing", role="primary")
        self.print_button.clicked.connect(self._request_print)
        print_actions.addWidget(self.print_button)
        print_card.content_layout.addLayout(print_actions)
        content_row.addWidget(print_card, 2)

        reprint_card = SectionCard(
            "Pending MRS Reprints",
            "Failed prints and confirmed crossed-out forms retain their original control numbers.",
        )
        reprint_card.setMinimumHeight(325)
        self.pending_label = QLabel("No pending replacement forms.")
        self.pending_label.setObjectName("mrs_pending_summary")
        self.pending_label.setWordWrap(True)
        reprint_card.add_widget(self.pending_label)
        self.reprint_table = QTableWidget(0, 5)
        self.reprint_table.setObjectName("mrs_reprint_table")
        self.reprint_table.setHorizontalHeaderLabels(["Control No.", "Reason", "Queued", "Attempts", "Language"])
        self._configure_table(self.reprint_table, stretch_column=1, row_height=34)
        self.reprint_table.setMaximumHeight(205)
        reprint_card.add_widget(self.reprint_table, 1)
        content_row.addWidget(reprint_card, 3)
        root.addLayout(content_row)

        registry_card = SectionCard(
            "Printed MRS Form Registry",
            "Red control numbers represent successfully printed physical copies that have not yet been matched with a scan.",
        )
        registry_actions = QHBoxLayout()
        registry_actions.addStretch(1)
        self.form_details_button = TooltipIconButton("info", "View selected MRS form lifecycle details", button_size=36, icon_size=19)
        self.form_details_button.clicked.connect(self._show_selected_form_details)
        registry_actions.addWidget(self.form_details_button)
        registry_card.content_layout.addLayout(registry_actions)
        self.registry_table = QTableWidget(0, 11)
        self.registry_table.setObjectName("mrs_registry_table")
        self.registry_table.setHorizontalHeaderLabels(
            ["Control No.", "Generated", "Language", "Print Status", "Scan Status", "Print Attempts", "Invalid Scans", "Reprint", "Analysis", "Last Printed", "Last Scanned"]
        )
        self._configure_table(self.registry_table, stretch_column=0, row_height=37)
        self.registry_table.itemSelectionChanged.connect(self._registry_selection_changed)
        self.registry_table.cellDoubleClicked.connect(lambda *_: self._show_selected_form_details())
        registry_card.add_widget(self.registry_table, 1)
        root.addWidget(registry_card, 1)

        self.prompt = OverlayPrompt(self)
        self.post_print_overlay = MRSFailedPrintOverlay(self)
        self.post_print_overlay.confirmed.connect(self._confirm_print_results)
        self.post_print_overlay.cancelled.connect(self._post_print_cancelled)

        self.printer_timer = QTimer(self)
        self.printer_timer.setInterval(12000)
        self.printer_timer.timeout.connect(self.refresh_printers)
        self.printer_timer.start()

        self._apply_styles()
        self.refresh()

    @staticmethod
    def _configure_table(table: QTableWidget, *, stretch_column: int, row_height: int) -> None:
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(row_height)
        table.horizontalHeader().setMinimumSectionSize(75)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(stretch_column, QHeaderView.ResizeMode.Stretch)
        table.setSortingEnabled(True)

    def _selected_template_spec(self) -> dict[str, str]:
        code = str(self.language_combo.currentData() or "en") if hasattr(self, "language_combo") else "en"
        try:
            return template_spec(code)
        except MRSPrintingError:
            return template_spec("en")

    def _language_selection_changed(self, *_args: object) -> None:
        spec = self._selected_template_spec()
        self.template_label.setText(spec["template_id"])
        self._update_batch_preview()

    def refresh(self) -> None:
        self.refresh_printers()
        stats = self.registry.stats()
        self.generated_metric.set_value(stats["generated"])
        self.awaiting_metric.set_value(stats["awaiting_scan"])
        self.reprint_metric.set_value(stats["pending_reprint"])
        self.valid_metric.set_value(stats["scanned_valid"])
        self.invalid_metric.set_metric(stats["scanned_invalid"] + stats["duplicates"], f"{stats['scanned_invalid']} invalid · {stats['duplicates']} duplicate")
        self._populate_registry()
        self._populate_reprints()
        pending_batches = self.registry.pending_print_confirmations()
        self.resume_button.setEnabled(bool(pending_batches))
        self.resume_button.setToolTip(
            f"Resume post-print confirmation for {pending_batches[0]['print_batch_id']}" if pending_batches
            else "No post-print confirmation is pending"
        )
        self._update_batch_preview()

    def refresh_printers(self) -> None:
        selected_name = str(self.printer_combo.currentData() or "")
        self._printers = discover_physical_printers()
        self.printer_combo.blockSignals(True)
        self.printer_combo.clear()
        if not self._printers:
            self.printer_combo.addItem("NO CONNECTED PHYSICAL PRINTER DETECTED", "")
            self.printer_status.setText(
                "NO CONNECTED PHYSICAL PRINTER DETECTED. Connect and enable a physical printer, then refresh the list."
            )
        else:
            for printer in self._printers:
                self.printer_combo.addItem(f"{printer['name']}  ·  {printer['status']}", printer["name"])
            index = next((i for i, row in enumerate(self._printers) if row.get("name") == selected_name), 0)
            self.printer_combo.setCurrentIndex(index)
        self.printer_combo.blockSignals(False)
        self._printer_selection_changed()

    def _selected_printer(self) -> dict[str, Any] | None:
        name = str(self.printer_combo.currentData() or "")
        return next((dict(row) for row in self._printers if str(row.get("name") or "") == name), None)

    def _printer_selection_changed(self, *_args: object) -> None:
        printer = self._selected_printer()
        available = printer is not None
        self.print_button.setEnabled(available)
        self.details_button.setEnabled(available)
        self.test_button.setEnabled(available)
        if printer is not None:
            self.printer_status.setText(
                f"{printer['name']} · {printer['connection_type']} · {printer['status']} · A4 supported"
            )
        self._update_batch_preview()

    def _settings(self) -> dict[str, Any]:
        return dict(self.settings_provider() or {})

    def _operator_name(self) -> str:
        settings = self._settings()
        return str(settings.get("csm_focal_person") or settings.get("school_head") or "Control Center Operator").strip()

    def _update_batch_preview(self, *_args: object) -> None:
        settings = self._settings()
        school_id = str(settings.get("school_id") or "").strip()
        pending = self.registry.pending_reprints()
        count = int(self.count_spin.value())
        try:
            controls = self.registry.preview_new_control_numbers(count, school_id) if count else []
        except Exception as exc:
            self.batch_preview.setText(str(exc))
            self.print_button.setEnabled(False)
            return
        printer_available = self._selected_printer() is not None
        self.print_button.setEnabled(printer_available and bool(pending or controls))
        range_text = "No new control numbers"
        if controls:
            range_text = controls[0] if len(controls) == 1 else f"{controls[0]} to {controls[-1]}"
        self.batch_preview.setText(
            f"Pending reprints: {len(pending)}  ·  New forms: {len(controls)}  ·  Total pages: {len(pending) + len(controls)}\n"
            f"New control-number range: {range_text}"
        )

    def _request_print(self) -> None:
        pending_confirmations = self.registry.pending_print_confirmations()
        if pending_confirmations:
            self.notice_requested.emit(
                "Complete the pending post-print confirmation before starting another official MRS print batch.",
                "warning",
            )
            self._resume_pending_confirmation()
            return
        printer = self._selected_printer()
        if printer is None:
            self.notice_requested.emit("No connected physical printer is available.", "error")
            return
        settings = self._settings()
        school_id = str(settings.get("school_id") or "").strip()
        pending = self.registry.pending_reprints()
        new_count = int(self.count_spin.value())
        try:
            new_controls = self.registry.preview_new_control_numbers(new_count, school_id) if new_count else []
            verified = verify_physical_printer(str(printer.get("name") or ""))
        except Exception as exc:
            self.notice_requested.emit(str(exc), "error")
            self.refresh_printers()
            return
        if not pending and not new_controls:
            self.notice_requested.emit("There are no new forms or pending reprints to print.", "warning")
            return
        total = len(pending) + len(new_controls)
        range_text = "None"
        if new_controls:
            range_text = new_controls[0] if len(new_controls) == 1 else f"{new_controls[0]} to {new_controls[-1]}"
        detail = (
            f"Printer: {verified['name']}\n"
            f"Connection: {verified['connection_type']}\n"
            f"Status: {verified['status']}\n"
            f"Paper: A4 · Actual Size / 100%\n"
            f"New-form language: {self._selected_template_spec()['language']}\n"
            f"Template: {self._selected_template_spec()['template_id']}\n"
            f"Pending reprints: {len(pending)}\n"
            f"New forms: {len(new_controls)}\n"
            f"Total pages: {total}\n"
            f"New control-number range: {range_text}"
        )
        self.prompt.show_prompt(
            "Confirm Official MRS Print Batch",
            "The pages will be rendered temporarily in memory and sent directly to the selected physical printer.",
            detail=detail,
            accept_tooltip="Start direct MRS printing",
            cancel_tooltip="Cancel printing",
            marker="P",
            on_accept=lambda: self._execute_print_batch(verified, new_count),
        )

    def _execute_print_batch(self, printer: Mapping[str, Any], new_count: int) -> None:
        settings = self._settings()
        try:
            batch = self.registry.create_print_batch(
                school_id=settings.get("school_id"),
                language=self._selected_template_spec()["language"],
                template_version=self._selected_template_spec()["template_id"],
                new_form_count=new_count,
                printer=printer,
                printed_by=self._operator_name(),
                include_pending_reprints=True,
            )
            self._active_batch = batch
            print_official_mrs_pages(
                self.project_root,
                str(printer.get("name") or ""),
                list(batch.get("pages") or []),
                school_name=str(settings.get("school_name") or "School"),
            )
            self.registry.mark_batch_submitted(str(batch["print_batch_id"]), accepted=True)
        except (MRSRegistryError, MRSPrintingError, ValueError, OSError) as exc:
            if self._active_batch:
                batch = self._active_batch
                try:
                    self.registry.mark_batch_submitted(str(batch["print_batch_id"]), accepted=False, error=str(exc))
                    controls = [str(page.get("control_number") or "") for page in batch.get("pages") or []]
                    self.registry.confirm_print_results(
                        str(batch["print_batch_id"]),
                        controls,
                        confirmed_by=self._operator_name(),
                        failure_reason=f"Print submission failed: {exc}",
                    )
                except Exception:
                    pass
            self.refresh()
            self.registry_changed.emit()
            self.notice_requested.emit(f"MRS printing failed: {exc}", "error")
            if self.registry.pending_reprints():
                self._ask_reprint_decision()
            return
        self.notice_requested.emit(
            f"Windows accepted {len(batch.get('pages') or [])} MRS page(s). Confirm the physical printing results.",
            "info",
        )
        self.post_print_overlay.show_batch(batch)

    def _confirm_print_results(self, failed_controls: object) -> None:
        batch = self._active_batch
        if batch is None:
            self.notice_requested.emit("The active print batch could not be identified.", "error")
            return
        failed = list(failed_controls) if isinstance(failed_controls, Sequence) and not isinstance(failed_controls, (str, bytes)) else []
        self._pending_failed_controls = [str(value) for value in failed]
        if failed:
            self.prompt.show_prompt(
                "REPRINT FAILED MRS FORMS?",
                f"{len(failed)} selected control number(s) will be marked Print Failed and queued for replacement.",
                detail=(
                    "Reprint now prints the same control numbers immediately. Reprint later retains them in the Pending MRS Reprints queue. "
                    "Use the return action to revise the failed-page selection before it is committed."
                ),
                accept_tooltip="Confirm failed forms and reprint now",
                cancel_tooltip="Confirm failed forms and reprint later",
                alternate_tooltip="Return to failed-form selection",
                marker="R",
                on_accept=lambda: self._finalize_print_results(reprint_now=True),
                on_reject=lambda: self._finalize_print_results(reprint_now=False),
                on_alternate=self._return_to_failed_selection,
            )
            return
        self._finalize_print_results(reprint_now=False)

    def _finalize_print_results(self, *, reprint_now: bool) -> None:
        batch = self._active_batch
        if batch is None:
            self.notice_requested.emit("The active print batch could not be identified.", "error")
            return
        failed = list(self._pending_failed_controls)
        try:
            self.registry.confirm_print_results(
                str(batch["print_batch_id"]),
                failed,
                confirmed_by=self._operator_name(),
            )
        except Exception as exc:
            self.notice_requested.emit(str(exc), "error")
            self.post_print_overlay.show_batch(batch, failed)
            return
        self._active_batch = None
        self._pending_failed_controls = []
        self.refresh()
        self.registry_changed.emit()
        if failed:
            self.notice_requested.emit(
                f"{len(failed)} failed form(s) were retained and queued for replacement.", "warning"
            )
            if reprint_now:
                self._reprint_now()
            else:
                self.notice_requested.emit("Pending MRS replacements will be added before new forms in the next print batch.", "info")
        else:
            self.notice_requested.emit("All MRS pages were confirmed as successfully printed.", "success")

    def _return_to_failed_selection(self) -> None:
        if self._active_batch is None:
            return
        self.post_print_overlay.show_batch(self._active_batch, self._pending_failed_controls)

    def _post_print_cancelled(self) -> None:
        self.notice_requested.emit(
            "Post-print confirmation remains pending. Resume it before starting another official print batch.",
            "warning",
        )
        self.refresh()

    def _ask_reprint_decision(self) -> None:
        pending = self.registry.pending_reprints()
        if not pending:
            return
        self.prompt.show_prompt(
            "Reprint Failed or Spoiled MRS Forms?",
            f"{len(pending)} control number(s) are waiting for replacement. Reprints retain their original control numbers.",
            detail="Reprint now sends all pending replacements first. Reprint later keeps them in the Pending MRS Reprints queue and adds them automatically to the next printing operation.",
            accept_tooltip="Reprint pending MRS forms now",
            cancel_tooltip="Reprint later",
            marker="R",
            on_accept=self._reprint_now,
            on_reject=lambda: self.notice_requested.emit("Pending MRS replacements were retained for the next print batch.", "info"),
        )

    def _reprint_now(self) -> None:
        self.count_spin.setValue(0)
        self._request_print()

    def _resume_pending_confirmation(self) -> None:
        batches = self.registry.pending_print_confirmations()
        if not batches:
            self.notice_requested.emit("No post-print confirmation is pending.", "info")
            return
        batch = dict(batches[0])
        attempts = self.registry.list_print_attempts()
        pages: list[dict[str, Any]] = []
        for control in batch.get("page_control_numbers") or []:
            attempt = next(
                (row for row in attempts if row.get("print_batch_id") == batch.get("print_batch_id") and row.get("control_number") == control),
                {},
            )
            form = self.registry.get_form(control) or {}
            pages.append({
                "control_number": control,
                "language": form.get("language") or batch.get("language") or "English",
                "attempt_number": attempt.get("attempt_number") or 1,
                "print_attempt_id": attempt.get("print_attempt_id") or "",
            })
        batch["pages"] = pages
        self._active_batch = batch
        self.post_print_overlay.show_batch(batch)

    def _run_mrs_self_check(self) -> None:
        self.self_check_button.setEnabled(False)
        self.notice_requested.emit("Running MRS template, barcode, recognition, and printer checks…", "info")
        try:
            result = run_mrs_software_self_check(self.project_root)
            report_path = write_mrs_self_check_report(self.project_root, result)
            report = format_mrs_self_check_report(result)
            summary = f"{result.get('passed', 0)} checks passed; {result.get('failed', 0)} require attention."
            self.prompt.show_information(
                "MRS Software Self-Check",
                summary,
                detail=report + f"\nSaved report: {report_path}",
            )
            level = "success" if int(result.get("failed") or 0) == 0 else "warning"
            self.notice_requested.emit(summary, level)
        except Exception as exc:
            self.notice_requested.emit(f"MRS self-check failed: {exc}", "error")
        finally:
            self.self_check_button.setEnabled(True)

    def _show_field_test_results(self) -> None:
        rows = self.field_tests.list()
        summary = self.field_tests.summary()
        average = summary.get("average_confidence")
        average_text = "—" if average is None else f"{float(average) * 100:.1f}%"
        language_text = ", ".join(
            f"{name}: {count}" for name, count in sorted((summary.get("languages") or {}).items())
        ) or "No language data"
        outcome_text = ", ".join(
            f"{name.replace('_', ' ').title()}: {count}"
            for name, count in sorted((summary.get("outcomes") or {}).items())
        ) or "No outcomes recorded"
        recent_lines = []
        for row in rows[:12]:
            confidence = row.get("overall_confidence")
            confidence_text = "—" if confidence is None else f"{float(confidence) * 100:.0f}%"
            recent_lines.append(
                f"{row.get('test_result_id')} · {row.get('language') or 'Unknown'} · {confidence_text} · "
                f"{str(row.get('outcome') or 'needs_review').replace('_', ' ').title()} · "
                f"{row.get('capture_condition') or 'Condition not recorded'} · "
                f"{row.get('operator_display_name') or row.get('operator_username') or 'Unknown operator'} · "
                f"{self._display_date(row.get('recorded_at'))}"
            )
        detail = (
            f"Total field-test scans: {summary.get('total', 0)}\n"
            f"Average recognition confidence: {average_text}\n"
            f"Languages: {language_text}\n"
            f"Outcomes: {outcome_text}\n\n"
            "RECENT FIELD TESTS\n"
            + ("\n".join(recent_lines) if recent_lines else "No field-test scans have been finalized yet.")
            + "\n\nField-test records are excluded from the Printed MRS Registry, official survey responses, control-number sequences, and CSM analysis."
        )
        self.prompt.show_information(
            "MRS Scanner Field Tests",
            "Use Field-test mode in the Scanner Remote during printer, camera, lighting, and recognition calibration.",
            detail=detail,
        )

    def _show_printer_details(self) -> None:
        printer = self._selected_printer()
        if printer is None:
            return
        detail = "\n".join(
            [
                f"Name: {printer.get('name', '')}",
                f"Connection: {printer.get('connection_type', 'Unknown')}",
                f"Status: {printer.get('status', 'Unknown')}",
                f"Driver: {printer.get('driver') or 'Not reported'}",
                f"Port: {printer.get('port') or 'Not reported'}",
                f"A4 capable: {'Yes' if printer.get('a4_capable') else 'No'}",
                f"Computer: {printer.get('computer_name', '')}",
            ]
        )
        self.prompt.show_information("Physical Printer Details", "The selected queue passed the current physical-printer filter.", detail=detail)

    def _test_printer(self) -> None:
        printer = self._selected_printer()
        if printer is None:
            return
        try:
            verified = verify_physical_printer(str(printer.get("name") or ""))
        except Exception as exc:
            self.notice_requested.emit(str(exc), "error")
            self.refresh_printers()
            return
        self.notice_requested.emit(
            f"{verified['name']} is currently available and reports A4 support.", "success"
        )

    def _open_printer_settings(self) -> None:
        try:
            if os.name != "nt":
                raise OSError("Windows Printer Settings are available only on the Windows deployment laptop.")
            os.startfile("ms-settings:printers")  # type: ignore[attr-defined]
        except OSError as exc:
            self.notice_requested.emit(str(exc), "warning")

    def _populate_registry(self) -> None:
        rows = self.registry.list_forms()
        self.registry_table.setSortingEnabled(False)
        self.registry_table.setRowCount(len(rows))
        for row_index, record in enumerate(rows):
            values = [
                record.get("control_number") or "",
                self._display_date(record.get("original_date_generated")),
                record.get("language") or "",
                record.get("current_print_status") or "",
                record.get("current_scan_status") or "",
                record.get("number_of_print_attempts") or 0,
                record.get("number_of_invalid_scans") or 0,
                "Pending" if record.get("pending_reprint") else "—",
                record.get("analysis_status") or "",
                self._display_date(record.get("last_date_printed")),
                self._display_date(record.get("last_date_scanned")),
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setData(Qt.ItemDataRole.UserRole, dict(record))
                if column == 0 and str(record.get("current_font_state") or "") == "red":
                    item.setForeground(QColor(theme.DANGER))
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                    item.setToolTip("Printed physical copy is still awaiting a matching scan.")
                self.registry_table.setItem(row_index, column, item)
        self.registry_table.setSortingEnabled(True)
        self._registry_selection_changed()

    def _populate_reprints(self) -> None:
        rows = self.registry.pending_reprints()
        self.pending_label.setText(
            "No pending replacement forms." if not rows
            else f"{len(rows)} replacement form(s) will be printed before newly generated forms."
        )
        self.reprint_table.setSortingEnabled(False)
        self.reprint_table.setRowCount(len(rows))
        for row_index, record in enumerate(rows):
            values = [
                record.get("control_number") or "",
                record.get("reprint_reason") or "Replacement required",
                self._display_date(record.get("reprint_queued_at")),
                record.get("number_of_print_attempts") or 0,
                record.get("language") or "",
            ]
            for column, value in enumerate(values):
                self.reprint_table.setItem(row_index, column, QTableWidgetItem(str(value)))
        self.reprint_table.setSortingEnabled(True)

    def _selected_form(self) -> dict[str, Any] | None:
        row = self.registry_table.currentRow()
        item = self.registry_table.item(row, 0) if row >= 0 else None
        data = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        return dict(data) if isinstance(data, Mapping) else None

    def _registry_selection_changed(self) -> None:
        self.form_details_button.setEnabled(self._selected_form() is not None)

    def _show_selected_form_details(self) -> None:
        record = self._selected_form()
        if record is None:
            return
        control = str(record.get("control_number") or "")
        prints = self.registry.list_print_attempts(control)
        scans = self.registry.list_scan_attempts(control)
        print_lines = [
            (
                f"{row.get('print_attempt_id')} · attempt {row.get('attempt_number')} · {row.get('outcome')} · "
                f"{row.get('printer_name') or 'Unknown printer'} · {row.get('printer_port') or 'port not reported'} · "
                f"{self._display_date(row.get('date_time'))}"
            )
            for row in prints[:8]
        ] or ["No print attempts recorded."]
        scan_lines = [
            (
                f"{row.get('scan_attempt_id')} · {row.get('response_validity')} · {row.get('barcode_status')} · "
                f"{row.get('scanner_operator_display_name') or row.get('scanner_operator_username') or 'Unknown operator'} · "
                f"{self._display_date(row.get('scanned_at'))}"
            )
            for row in scans[:8]
        ] or ["No scan attempts recorded."]
        detail = (
            f"Barcode value: {record.get('barcode_value') or control}\n"
            f"School ID: {record.get('school_id') or '—'}\n"
            f"Template: {record.get('template_version') or '—'}\n"
            f"Sequence: {record.get('sequence_year')}-{int(record.get('sequence_number') or 0):04d}\n"
            f"Print status: {record.get('current_print_status')}\n"
            f"Scan status: {record.get('current_scan_status')}\n"
            f"Physical copy: {record.get('printed_copy_status')}\n"
            f"Analysis: {record.get('analysis_status')}\n"
            f"Pending reprint: {'Yes' if record.get('pending_reprint') else 'No'}\n"
            f"Reprint reason: {record.get('reprint_reason') or '—'}\n\n"
            "PRINT ATTEMPTS\n" + "\n".join(print_lines) + "\n\nSCAN ATTEMPTS\n" + "\n".join(scan_lines)
        )
        self.prompt.show_information(control, "Complete official-form lifecycle and accounting record.", detail=detail)

    @staticmethod
    def _display_date(value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return "—"
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            return parsed.strftime("%d %b %Y, %I:%M %p")
        except ValueError:
            return text

    def _apply_styles(self) -> None:
        self.setStyleSheet(
            f"""
            QFrame#mrs_printing_board {{ background: {theme.WINDOW_BG}; }}
            QScrollArea#mrs_board_scroll {{ background: {theme.WINDOW_BG}; border: none; }}
            QScrollArea#mrs_board_scroll > QWidget > QWidget {{ background: {theme.WINDOW_BG}; }}
            QWidget#mrs_board_content {{ background: {theme.WINDOW_BG}; }}
            QFrame#mrs_header {{
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 rgba(13,41,64,0.98), stop:1 rgba(9,25,43,0.98));
                border: 1px solid {theme.CARD_BORDER}; border-radius: 13px;
            }}
            QLabel#mrs_title {{ color: {theme.TEXT_PRIMARY}; font-size: 21px; font-weight: 850; }}
            QLabel#mrs_subtitle {{ color: {theme.TEXT_MUTED}; font-size: 12px; }}
            QLabel#mrs_form_label {{
                color: {theme.TEXT_SECONDARY}; font-size: 11px; font-weight: 750;
                padding: 0 2px; min-height: 16px;
            }}
            QComboBox#mrs_language_combo, QComboBox#mrs_printer_combo, QSpinBox#mrs_count_spin {{
                min-height: 36px; max-height: 36px;
            }}
            QLabel#mrs_value_label {{
                color: {theme.ACCENT_CYAN}; background: rgba(5,19,33,0.82); border: 1px solid rgba(43,217,197,0.34);
                border-radius: 8px; padding: 8px 10px; font-weight: 700;
            }}
            QLabel#mrs_status_label {{ color: {theme.TEXT_SECONDARY}; font-weight: 700; }}
            QLabel#mrs_batch_preview, QLabel#mrs_pending_summary {{
                color: {theme.TEXT_SECONDARY}; background: rgba(5,19,33,0.68); border: 1px solid {theme.DIVIDER};
                border-radius: 8px; padding: 9px;
            }}
            QTableWidget#mrs_registry_table, QTableWidget#mrs_reprint_table {{
                background: rgba(5,19,33,0.72); alternate-background-color: rgba(13,41,64,0.56);
                border: 1px solid {theme.CARD_BORDER}; border-radius: 9px; gridline-color: {theme.DIVIDER};
                selection-background-color: rgba(22,142,136,0.72);
            }}
            QTableWidget#mrs_registry_table::item, QTableWidget#mrs_reprint_table::item {{
                padding: 6px; color: {theme.TEXT_SECONDARY};
            }}
            QHeaderView::section {{
                background: {theme.PANEL_BG_ALT}; color: {theme.TEXT_PRIMARY}; border: none;
                border-right: 1px solid {theme.DIVIDER}; border-bottom: 1px solid {theme.CARD_BORDER};
                padding: 7px; font-weight: 800;
            }}
            """
        )
