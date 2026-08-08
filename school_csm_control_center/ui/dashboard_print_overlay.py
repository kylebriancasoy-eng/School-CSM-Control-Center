from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import os
from pathlib import Path
import subprocess
from typing import Any

from PySide6.QtCore import QEvent, QMarginsF, QObject, QTimer, Qt, Signal
from PySide6.QtGui import QKeySequence, QPageLayout, QPageSize, QShortcut
from PySide6.QtPrintSupport import QPrinter, QPrinterInfo, QPrintPreviewWidget
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from school_csm_control_center.app_identity import (
    resolve_application_logo,
    resolve_deped_logo,
    resolve_mosslab_logo,
    resolve_mosslab_seal,
)
from school_csm_control_center.runtime_paths import storage_root_for
from school_csm_control_center.storage.control_center_settings import ControlCenterSettingsStore
from school_csm_control_center.storage.print_history_store import PrintHistoryStore
from school_csm_control_center.ui import theme
from school_csm_control_center.ui.controls import NoWheelComboBox, NoWheelDoubleSpinBox, NoWheelSpinBox
from school_csm_control_center.ui.dashboard_printing import (
    DashboardPrintRenderer,
    DashboardSnapshot,
    PrintReportMetadata,
)
from school_csm_control_center.ui.overlays import ClickScrim
from school_csm_control_center.ui.widgets import TooltipIconButton


PrinterProvider = Callable[[], list[QPrinterInfo]]
PrintExecutor = Callable[..., int]
ControlNumberProvider = Callable[[datetime], str]
Clock = Callable[[], datetime]
PrinterPreferencesOpener = Callable[[str], None]


MINIMUM_PRINT_MARGIN_MM = 5.0

class _PreviewWheelZoomFilter(QObject):
    """Convert wheel movement anywhere over the preview into zoom steps."""

    def __init__(self, preview: QPrintPreviewWidget) -> None:
        super().__init__(preview)
        self.preview = preview

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt API
        if event.type() == QEvent.Type.Wheel:
            delta = event.angleDelta().y()
            if delta:
                try:
                    self.preview.setZoomMode(QPrintPreviewWidget.ZoomMode.CustomZoom)
                except (AttributeError, TypeError):
                    pass
                if delta > 0:
                    self.preview.zoomIn(1.15)
                else:
                    self.preview.zoomOut(1.15)
                event.accept()
                return True
        return super().eventFilter(watched, event)


class DashboardPrintOverlay(QWidget):
    """Full-Dashboard preview and printer preferences without a dialog window."""

    print_completed = Signal(str)
    print_job_completed = Signal(object, str)
    print_failed = Signal(str)
    audit_history_changed = Signal()

    def __init__(
        self,
        dashboard,
        parent: QWidget,
        *,
        printer_provider: PrinterProvider | None = None,
        print_executor: PrintExecutor | None = None,
        control_number_provider: ControlNumberProvider | None = None,
        clock: Clock | None = None,
        printer_preferences_opener: PrinterPreferencesOpener | None = None,
        project_root: str | Path | None = None,
        print_store: PrintHistoryStore | None = None,
    ) -> None:
        super().__init__(parent)
        self.dashboard = dashboard
        self.project_root = Path(project_root).resolve() if project_root is not None else Path.cwd().resolve()
        self.data_root = storage_root_for(self.project_root)
        self.settings_store = ControlCenterSettingsStore(self.project_root)
        self.print_store = print_store
        self._printer_provider = printer_provider or self._system_printers
        self._print_executor = print_executor or self._execute_native_print
        self._control_number_provider = control_number_provider or self._fallback_control_number
        self._clock = clock or (lambda: datetime.now().astimezone())
        self._printer_preferences_opener = printer_preferences_opener or self._open_windows_printer_preferences
        self._uses_native_print_executor = print_executor is None
        self._printer_infos: dict[str, QPrinterInfo] = {}
        self._page_sizes: dict[str, QPageSize] = {}
        self._source_snapshot: DashboardSnapshot | None = None
        self._snapshot: DashboardSnapshot | None = None
        self.report_metadata: PrintReportMetadata | None = None
        self._filter_scope = "All responses"
        self._response_scope_text = "0 of 0 responses"
        self._syncing = False
        self._audit_record_id = ""
        self._audit_status = ""

        self.setObjectName("dashboard_print_overlay")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.hide()

        self.printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        self.printer.setResolution(300)
        self.printer.setFullPage(False)
        self.printer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
        initial_layout = self.printer.pageLayout()
        initial_layout.setUnits(QPageLayout.Unit.Millimeter)
        initial_layout.setMargins(
            QMarginsF(
                MINIMUM_PRINT_MARGIN_MM,
                MINIMUM_PRINT_MARGIN_MM,
                MINIMUM_PRINT_MARGIN_MM,
                MINIMUM_PRINT_MARGIN_MM,
            ),
            QPageLayout.OutOfBoundsPolicy.Clamp,
        )
        self.printer.setPageLayout(initial_layout)
        if hasattr(self.printer, "setDocName"):
            self.printer.setDocName("School CSM Control Center Dashboard")

        self.scrim = ClickScrim(self)
        self.scrim.setObjectName("dashboard_print_scrim")
        self.scrim.clicked.connect(self.close_overlay)

        self.panel = QFrame(self)
        self.panel.setObjectName("dashboard_print_panel")
        panel_layout = QVBoxLayout(self.panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.setSpacing(0)

        header = QFrame()
        header.setObjectName("dashboard_print_header")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(20, 14, 14, 14)
        header_layout.setSpacing(12)
        heading = QVBoxLayout()
        heading.setSpacing(3)
        title = QLabel("Print complete Dashboard")
        title.setObjectName("dashboard_print_title")
        heading.addWidget(title)
        self.scope_label = QLabel("Previewing the complete current Dashboard")
        self.scope_label.setObjectName("dashboard_print_scope")
        self.scope_label.setWordWrap(True)
        heading.addWidget(self.scope_label)
        header_layout.addLayout(heading, 1)
        self.close_button = TooltipIconButton("close", "Close print preview")
        self.close_button.clicked.connect(self.close_overlay)
        header_layout.addWidget(self.close_button)
        panel_layout.addWidget(header)

        content = QWidget()
        content.setObjectName("dashboard_print_content")
        content_layout = QHBoxLayout(content)
        content_layout.setContentsMargins(12, 12, 12, 12)
        content_layout.setSpacing(12)

        self.settings_scroll = QScrollArea()
        self.settings_scroll.setObjectName("dashboard_print_settings_scroll")
        self.settings_scroll.setWidgetResizable(True)
        self.settings_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.settings_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        settings_body = QWidget()
        settings_body.setObjectName("dashboard_print_settings_body")
        settings_layout = QVBoxLayout(settings_body)
        settings_layout.setContentsMargins(10, 10, 10, 12)
        settings_layout.setSpacing(10)

        self.printer_status = QLabel()
        self.printer_status.setObjectName("dashboard_print_printer_status")
        self.printer_status.setWordWrap(True)
        settings_layout.addWidget(self.printer_status)

        basic = QFrame()
        basic.setObjectName("dashboard_print_settings_section")
        basic_layout = QVBoxLayout(basic)
        basic_layout.setContentsMargins(11, 10, 11, 11)
        basic_layout.setSpacing(8)
        basic_title = QLabel("Print settings")
        basic_title.setObjectName("dashboard_print_section_title")
        basic_layout.addWidget(basic_title)
        basic_form = self._form_layout()

        self.printer_combo = self._combo("Select a printer")
        printer_row = QWidget()
        printer_row_layout = QHBoxLayout(printer_row)
        printer_row_layout.setContentsMargins(0, 0, 0, 0)
        printer_row_layout.setSpacing(6)
        printer_row_layout.addWidget(self.printer_combo, 1)
        self.system_preferences_button = TooltipIconButton(
            "settings",
            "Open selected printer's Windows preferences",
            button_size=34,
            icon_size=18,
            role="subtle",
        )
        self.system_preferences_button.setEnabled(False)
        self.system_preferences_button.clicked.connect(self._open_selected_printer_preferences)
        printer_row_layout.addWidget(self.system_preferences_button)
        basic_form.addRow("Printer", printer_row)
        self.copies_spin = NoWheelSpinBox()
        self.copies_spin.setObjectName("dashboard_print_spin")
        self.copies_spin.setRange(1, 999)
        self.copies_spin.setValue(1)
        self.copies_spin.setToolTip("Number of printed copies")
        basic_form.addRow("Copies", self.copies_spin)

        self.page_range_combo = self._combo("Choose which preview pages to print")
        self.page_range_combo.addItem("All pages", "all")
        self.page_range_combo.addItem("Current preview page", "current")
        self.page_range_combo.addItem("Custom range", "custom")
        basic_form.addRow("Pages", self.page_range_combo)
        self.range_widget = QWidget()
        range_layout = QHBoxLayout(self.range_widget)
        range_layout.setContentsMargins(0, 0, 0, 0)
        range_layout.setSpacing(6)
        self.from_page_spin = NoWheelSpinBox()
        self.from_page_spin.setObjectName("dashboard_print_spin")
        self.from_page_spin.setRange(1, 1)
        self.to_page_spin = NoWheelSpinBox()
        self.to_page_spin.setObjectName("dashboard_print_spin")
        self.to_page_spin.setRange(1, 1)
        range_layout.addWidget(self.from_page_spin)
        range_layout.addWidget(QLabel("to"))
        range_layout.addWidget(self.to_page_spin)
        self.range_widget.hide()
        basic_form.addRow("Range", self.range_widget)

        self.appearance_combo = self._combo("Choose standard or low-ink Dashboard rendering")
        self.appearance_combo.addItem("Standard", "standard")
        self.appearance_combo.addItem("Low-ink light", "low_ink")
        basic_form.addRow("Appearance", self.appearance_combo)

        self.scale_mode_combo = self._combo("Choose how the complete Dashboard fits on paper")
        self.scale_mode_combo.addItem("Readable page width", "fit_width")
        self.scale_mode_combo.addItem("Custom readable width", "custom")
        basic_form.addRow("Scaling", self.scale_mode_combo)
        self.width_percent_spin = NoWheelSpinBox()
        self.width_percent_spin.setObjectName("dashboard_print_spin")
        self.width_percent_spin.setRange(70, 100)
        self.width_percent_spin.setValue(100)
        self.width_percent_spin.setSuffix("%")
        self.width_percent_spin.setToolTip("Report width as a percentage of the printable page width")
        self.width_percent_spin.hide()
        basic_form.addRow("Width", self.width_percent_spin)
        basic_layout.addLayout(basic_form)
        settings_layout.addWidget(basic)

        advanced_header = QFrame()
        advanced_header.setObjectName("dashboard_print_advanced_header")
        advanced_header_layout = QHBoxLayout(advanced_header)
        advanced_header_layout.setContentsMargins(11, 9, 9, 9)
        advanced_copy = QVBoxLayout()
        advanced_copy.setSpacing(2)
        advanced_title = QLabel("Advanced")
        advanced_title.setObjectName("dashboard_print_section_title")
        advanced_copy.addWidget(advanced_title)
        advanced_subtitle = QLabel("Printer preferences")
        advanced_subtitle.setObjectName("dashboard_print_section_subtitle")
        advanced_copy.addWidget(advanced_subtitle)
        advanced_header_layout.addLayout(advanced_copy, 1)
        self.advanced_button = TooltipIconButton(
            "expand", "Show advanced printer preferences", button_size=36, icon_size=19, role="subtle"
        )
        self.advanced_button.clicked.connect(self._toggle_advanced)
        advanced_header_layout.addWidget(self.advanced_button)
        settings_layout.addWidget(advanced_header)

        self.advanced_body = QFrame()
        self.advanced_body.setObjectName("dashboard_print_settings_section")
        advanced_layout = QVBoxLayout(self.advanced_body)
        advanced_layout.setContentsMargins(11, 10, 11, 11)
        advanced_layout.setSpacing(8)
        advanced_form = self._form_layout()
        self.paper_combo = self._combo("Select printer paper size")
        advanced_form.addRow("Paper", self.paper_combo)
        self.orientation_combo = self._combo("Select page orientation")
        self.orientation_combo.addItem("Portrait", "portrait")
        self.orientation_combo.addItem("Landscape", "landscape")
        advanced_form.addRow("Orientation", self.orientation_combo)
        self.color_combo = self._combo("Select color or grayscale printing")
        advanced_form.addRow("Color", self.color_combo)
        self.duplex_combo = self._combo("Select one-sided or duplex printing")
        advanced_form.addRow("Duplex", self.duplex_combo)
        self.resolution_combo = self._combo("Select printer resolution")
        advanced_form.addRow("Quality", self.resolution_combo)
        self.use_driver_preferences_check = QCheckBox("Use Windows driver preferences")
        self.use_driver_preferences_check.setObjectName("dashboard_print_checkbox")
        self.use_driver_preferences_check.setChecked(False)
        self.use_driver_preferences_check.setToolTip(
            "Use the selected printer driver's saved quality, including Draft or Draft Vivid when supported"
        )
        advanced_form.addRow("Driver", self.use_driver_preferences_check)
        self.collate_check = QCheckBox("Collate copies")
        self.collate_check.setObjectName("dashboard_print_checkbox")
        self.collate_check.setChecked(True)
        self.collate_check.setToolTip("Keep multi-page copies in page order")
        advanced_form.addRow("Copies", self.collate_check)

        margins_widget = QWidget()
        margins_layout = QGridLayout(margins_widget)
        margins_layout.setContentsMargins(0, 0, 0, 0)
        margins_layout.setHorizontalSpacing(5)
        margins_layout.setVerticalSpacing(4)
        self.margin_spins: dict[str, NoWheelDoubleSpinBox] = {}
        for index, key in enumerate(("Left", "Top", "Right", "Bottom")):
            label = QLabel(key)
            label.setObjectName("dashboard_print_mini_label")
            spin = NoWheelDoubleSpinBox()
            spin.setObjectName("dashboard_print_margin_spin")
            spin.setRange(MINIMUM_PRINT_MARGIN_MM, 50.0)
            spin.setDecimals(1)
            spin.setSingleStep(0.5)
            spin.setValue(MINIMUM_PRINT_MARGIN_MM)
            spin.setSuffix(" mm")
            spin.setToolTip(f"{key} page margin in millimeters; minimum 5 mm (0.5 cm)")
            row, column = divmod(index, 2)
            margins_layout.addWidget(label, row * 2, column)
            margins_layout.addWidget(spin, row * 2 + 1, column)
            self.margin_spins[key.casefold()] = spin
        advanced_form.addRow("Margins", margins_widget)
        advanced_layout.addLayout(advanced_form)
        self.advanced_body.hide()
        settings_layout.addWidget(self.advanced_body)

        self.capability_label = QLabel()
        self.capability_label.setObjectName("dashboard_print_capabilities")
        self.capability_label.setWordWrap(True)
        settings_layout.addWidget(self.capability_label)
        settings_layout.addStretch(1)
        self.settings_scroll.setWidget(settings_body)
        content_layout.addWidget(self.settings_scroll)

        preview_surface = QFrame()
        preview_surface.setObjectName("dashboard_print_preview_surface")
        preview_layout = QVBoxLayout(preview_surface)
        preview_layout.setContentsMargins(8, 8, 8, 8)
        preview_layout.setSpacing(7)
        toolbar = QFrame()
        toolbar.setObjectName("dashboard_print_preview_toolbar")
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(6, 5, 6, 5)
        toolbar_layout.setSpacing(6)
        self.previous_button = TooltipIconButton("previous", "Previous preview page", button_size=34, icon_size=18)
        self.previous_button.clicked.connect(self._previous_page)
        toolbar_layout.addWidget(self.previous_button)
        self.next_button = TooltipIconButton("next", "Next preview page", button_size=34, icon_size=18)
        self.next_button.clicked.connect(self._next_page)
        toolbar_layout.addWidget(self.next_button)
        self.page_label = QLabel("Page 1 of 1")
        self.page_label.setObjectName("dashboard_print_page_label")
        self.page_label.setToolTip("Use the mouse wheel anywhere over the preview to zoom in or out")
        toolbar_layout.addWidget(self.page_label)
        toolbar_layout.addStretch(1)
        self.fit_page_button = TooltipIconButton("fit_page", "Fit preview page", button_size=34, icon_size=18)
        self.fit_page_button.clicked.connect(self._fit_preview_page)
        toolbar_layout.addWidget(self.fit_page_button)
        self.fit_width_button = TooltipIconButton("fit_width", "Fit preview width", button_size=34, icon_size=18)
        self.fit_width_button.clicked.connect(self._fit_preview_width)
        toolbar_layout.addWidget(self.fit_width_button)
        self.zoom_out_button = TooltipIconButton("zoom_out", "Zoom out preview", button_size=34, icon_size=18)
        self.zoom_out_button.clicked.connect(self._zoom_out)
        toolbar_layout.addWidget(self.zoom_out_button)
        self.zoom_in_button = TooltipIconButton("zoom_in", "Zoom in preview", button_size=34, icon_size=18)
        self.zoom_in_button.clicked.connect(self._zoom_in)
        toolbar_layout.addWidget(self.zoom_in_button)
        self.refresh_button = TooltipIconButton("reset", "Refresh complete Dashboard preview", button_size=34, icon_size=18)
        self.refresh_button.clicked.connect(self._recapture)
        toolbar_layout.addWidget(self.refresh_button)
        preview_layout.addWidget(toolbar)

        self.preview = QPrintPreviewWidget(self.printer)
        self.preview.setObjectName("dashboard_print_preview_widget")
        self.preview.setMinimumSize(280, 280)
        self.preview.setAllPagesViewMode()
        self.preview.paintRequested.connect(self._paint_preview)
        self.preview.previewChanged.connect(self._preview_changed)
        self._preview_wheel_filter = _PreviewWheelZoomFilter(self.preview)
        self._install_preview_wheel_zoom()
        QTimer.singleShot(0, self._install_preview_wheel_zoom)
        preview_layout.addWidget(self.preview, 1)
        content_layout.addWidget(preview_surface, 1)
        panel_layout.addWidget(content, 1)

        footer = QFrame()
        footer.setObjectName("dashboard_print_footer")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(16, 10, 14, 10)
        footer_layout.setSpacing(10)
        self.status_label = QLabel("Preparing complete Dashboard preview…")
        self.status_label.setObjectName("dashboard_print_status")
        self.status_label.setWordWrap(True)
        footer_layout.addWidget(self.status_label, 1)
        self.print_button = TooltipIconButton(
            "print", "Print complete Dashboard", icon_color=theme.SUCCESS, role="success", button_size=44, icon_size=23
        )
        self.print_button.setEnabled(False)
        self.print_button.clicked.connect(self._print)
        footer_layout.addWidget(self.print_button)
        panel_layout.addWidget(footer)

        self.preview_timer = QTimer(self)
        self.preview_timer.setSingleShot(True)
        self.preview_timer.setInterval(120)
        self.preview_timer.timeout.connect(self._refresh_preview)
        self.escape_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        self.escape_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.escape_shortcut.activated.connect(self.close_overlay)
        self._connect_settings()
        self._apply_styles()

    @staticmethod
    def _system_printers() -> list[QPrinterInfo]:
        return list(QPrinterInfo.availablePrinters())

    @staticmethod
    def _fallback_control_number(generated_at: datetime) -> str:
        return generated_at.astimezone().strftime("CSMS-PRN-%Y%m%d-%H%M%S")

    @staticmethod
    def _form_layout() -> QFormLayout:
        layout = QFormLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(7)
        layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        layout.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        layout.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        return layout

    @staticmethod
    def _combo(tooltip: str) -> NoWheelComboBox:
        combo = NoWheelComboBox()
        combo.setObjectName("dashboard_print_combo")
        combo.setToolTip(tooltip)
        combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        combo.setMinimumContentsLength(8)
        combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        return combo

    def _connect_settings(self) -> None:
        self.printer_combo.currentIndexChanged.connect(self._printer_changed)
        for combo in (
            self.paper_combo,
            self.orientation_combo,
            self.color_combo,
            self.duplex_combo,
            self.resolution_combo,
            self.scale_mode_combo,
        ):
            combo.currentIndexChanged.connect(self._settings_changed)
        for spin in (self.copies_spin, self.width_percent_spin, *self.margin_spins.values()):
            spin.valueChanged.connect(self._settings_changed)
        self.collate_check.toggled.connect(self._settings_changed)
        self.use_driver_preferences_check.toggled.connect(self._driver_preferences_toggled)
        self.appearance_combo.currentIndexChanged.connect(self._appearance_changed)
        self.page_range_combo.currentIndexChanged.connect(self._range_mode_changed)
        self.from_page_spin.valueChanged.connect(self._range_value_changed)
        self.to_page_spin.valueChanged.connect(self._range_value_changed)

    def open_overlay(self) -> None:
        self._cancel_open_reservation(
            "A newer Dashboard print preview replaced this reservation."
        )
        self._audit_record_id = ""
        self._audit_status = ""
        self.setGeometry(self.parentWidget().rect())
        self._position_children()
        self._filter_scope = str(getattr(self.dashboard, "print_scope_text", lambda: getattr(self.dashboard, "filter_summary_label").text())())
        try:
            generated_at = self._clock()
            if not isinstance(generated_at, datetime):
                raise TypeError("The print timestamp provider did not return a date and time.")
            current_records = getattr(
                self.dashboard,
                "current_filtered_records",
                lambda: [],
            )()
            all_records = getattr(self.dashboard, "_records", current_records)
            self._response_scope_text = (
                f"{len(current_records)} of {len(all_records)} responses"
            )
            if self.print_store is not None:
                reservation = self.print_store.reserve(
                    {
                        "scope": self._filter_scope,
                        "filter_summary": self._filter_scope,
                        "report_title": "Complete Dashboard Analysis",
                        "survey_count": len(current_records),
                        "status_message": "Reserved before print-preview rendering.",
                    },
                    generated_at=generated_at,
                )
                self._audit_record_id = str(reservation.get("id") or "")
                self._audit_status = "reserved"
                control_number = str(reservation.get("control_number") or "")
                self.audit_history_changed.emit()
            else:
                control_number = self._control_number_provider(generated_at)
            settings = self.settings_store.load()
            logo_relative = str(settings.get("school_logo_path") or "").strip()
            school_logo = (
                self.data_root / logo_relative
                if logo_relative
                else self.data_root / "data" / "csm_survey" / "school_logo.png"
            )
            deped_logo = resolve_deped_logo(self.project_root)
            app_logo = resolve_application_logo(self.project_root)
            mosslab_logo = resolve_mosslab_logo(self.project_root)
            mosslab_seal = resolve_mosslab_seal(self.project_root)
            self.report_metadata = PrintReportMetadata(
                control_number,
                generated_at,
                school_name=str(settings.get("school_name") or "School"),
                filter_scope=self._filter_scope,
                school_logo_path=str(school_logo) if school_logo.is_file() else "",
                deped_logo_path=str(deped_logo) if deped_logo is not None else "",
                app_logo_path=str(app_logo) if app_logo is not None else "",
                mosslab_logo_path=str(mosslab_logo) if mosslab_logo is not None else "",
                mosslab_seal_path=str(mosslab_seal) if mosslab_seal is not None else "",
            )
        except Exception as exc:
            self._fail_audit(str(exc))
            self.report_metadata = None
            self._source_snapshot = None
            self._snapshot = None
            self.show()
            self.raise_()
            self.print_button.setEnabled(False)
            message = f"The print report could not be identified: {exc}"
            self.scope_label.setText("Complete current Dashboard")
            self.status_label.setText(message)
            self.print_failed.emit(message)
            return
        self.scope_label.setText(
            f"{self.report_metadata.control_number} · {self.report_metadata.timestamp_text} · "
            f"{self._response_scope_text} · {self._filter_scope}"
        )
        self.show()
        self.raise_()
        self.print_button.setEnabled(False)
        self.status_label.setText("Arranging all Dashboard sections into a readable vertical report…")
        QApplication.processEvents()
        try:
            self._source_snapshot = DashboardPrintRenderer.capture(
                self.dashboard,
                render_scale=2.0,
                metadata=self.report_metadata,
            )
            self._snapshot = DashboardPrintRenderer.apply_ink_mode(
                self._source_snapshot, self._appearance_mode()
            )
        except (RuntimeError, ValueError) as exc:
            self._fail_audit(str(exc))
            self._snapshot = None
            self.status_label.setText(str(exc))
            self.print_button.setEnabled(False)
            self.print_failed.emit(str(exc))
            return
        self._populate_printers()
        self.preview.setCurrentPage(1)
        self._refresh_preview()
        QTimer.singleShot(0, self._fit_preview_page)
        (self.print_button if self.print_button.isEnabled() else self.close_button).setFocus(
            Qt.FocusReason.ActiveWindowFocusReason
        )

    def close_overlay(self) -> None:
        self.preview_timer.stop()
        self._cancel_open_reservation(
            "Print preview closed before the job was submitted."
        )
        self.hide()

    def _cancel_open_reservation(self, reason: str) -> None:
        if (
            self.print_store is None
            or not self._audit_record_id
            or self._audit_status not in {"reserved", "submitting"}
        ):
            return
        try:
            self.print_store.mark_cancelled(self._audit_record_id, reason)
            self._audit_status = "cancelled"
            self.audit_history_changed.emit()
        except Exception:
            pass

    def _fail_audit(self, message: str) -> None:
        if (
            self.print_store is None
            or not self._audit_record_id
            or self._audit_status not in {"reserved", "submitting", "submitted"}
        ):
            return
        try:
            self.print_store.mark_failed(self._audit_record_id, message)
            self._audit_status = "failed"
            self.audit_history_changed.emit()
        except Exception:
            pass

    def _recapture(self) -> None:
        self.print_button.setEnabled(False)
        self.status_label.setText("Refreshing the readable vertical Dashboard report…")
        try:
            self._source_snapshot = DashboardPrintRenderer.capture(
                self.dashboard,
                render_scale=2.0,
                metadata=self.report_metadata,
            )
            self._snapshot = DashboardPrintRenderer.apply_ink_mode(
                self._source_snapshot, self._appearance_mode()
            )
        except (RuntimeError, ValueError) as exc:
            self.status_label.setText(str(exc))
            self.print_failed.emit(str(exc))
            return
        self.print_button.setEnabled(bool(self.printer_combo.currentData()))
        self._refresh_preview()

    def _populate_printers(self) -> None:
        self._syncing = True
        try:
            try:
                infos = list(self._printer_provider())
            except Exception:
                infos = []
            self._printer_infos = {
                info.printerName(): info
                for info in infos
                if info is not None and str(info.printerName()).strip()
            }
            self.printer_combo.clear()
            if not self._printer_infos:
                self.printer_combo.addItem("No printers detected", "")
                self.printer_combo.setEnabled(False)
                self.print_button.setEnabled(False)
                self.system_preferences_button.setEnabled(False)
                self.printer_status.setText(
                    "No physical printer is available. The complete Dashboard preview remains available."
                )
                self._load_capabilities(None)
                self._apply_settings_to_printer()
                return

            self.printer_combo.setEnabled(True)
            self.system_preferences_button.setEnabled(True)
            default_name = QPrinterInfo.defaultPrinterName()
            for name in sorted(self._printer_infos, key=str.casefold):
                self.printer_combo.addItem(name, name)
            index = self.printer_combo.findData(default_name)
            self.printer_combo.setCurrentIndex(index if index >= 0 else 0)
            selected_name = str(self.printer_combo.currentData() or "")
            self.printer.setPrinterName(selected_name)
            self.print_button.setEnabled(bool(self._snapshot))
            self.printer_status.setText(f"Selected printer: {selected_name}")
            self._load_capabilities(self._printer_infos.get(selected_name))
        finally:
            self._syncing = False
        self._apply_settings_to_printer()

    def _printer_changed(self, *_args) -> None:
        if self._syncing:
            return
        name = str(self.printer_combo.currentData() or "")
        if not name:
            self.system_preferences_button.setEnabled(False)
            return
        self.system_preferences_button.setEnabled(True)
        self.printer.setPrinterName(name)
        self.printer_status.setText(f"Selected printer: {name}")
        self._syncing = True
        try:
            self._load_capabilities(self._printer_infos.get(name))
        finally:
            self._syncing = False
        self._settings_changed()

    @staticmethod
    def _open_windows_printer_preferences(printer_name: str) -> None:
        name = str(printer_name or "").strip()
        if not name:
            raise OSError("Select a printer before opening its preferences.")
        if os.name != "nt":
            raise OSError("Printer-driver preferences are available only on Windows.")
        command = [
            "rundll32.exe",
            "printui.dll,PrintUIEntry",
            "/e",
            "/n",
            name,
        ]
        completed = subprocess.run(command, check=False)
        if completed.returncode not in (0, None):
            raise OSError(f"Windows could not open printer preferences (code {completed.returncode}).")

    def _open_selected_printer_preferences(self) -> None:
        name = str(self.printer_combo.currentData() or "").strip()
        if not name:
            self.status_label.setText("Select a physical printer first.")
            return
        self.status_label.setText(f"Opening Windows preferences for {name}…")
        QApplication.processEvents()
        try:
            self._printer_preferences_opener(name)
        except Exception as exc:
            self.status_label.setText(str(exc))
            return
        self.use_driver_preferences_check.setChecked(True)
        self.printer.setPrinterName(name)
        self._load_capabilities(self._printer_infos.get(name))
        self._driver_preferences_toggled(True)
        self._apply_settings_to_printer()
        self.status_label.setText(
            "Windows printer preferences saved. Driver-managed quality will be used for printing."
        )
        self.preview_timer.start()

    def _load_capabilities(self, info: QPrinterInfo | None) -> None:
        previous_paper = str(self.paper_combo.currentData() or "")
        self._page_sizes.clear()
        self.paper_combo.clear()
        sizes: list[QPageSize] = []
        if info is not None:
            try:
                sizes = [size for size in info.supportedPageSizes() if size.isValid()]
            except Exception:
                sizes = []
        if not sizes:
            sizes = [
                QPageSize(QPageSize.PageSizeId.A4),
                QPageSize(QPageSize.PageSizeId.Letter),
                QPageSize(QPageSize.PageSizeId.Legal),
            ]
        for size in sizes:
            key = size.key() or str(int(size.id().value))
            if key in self._page_sizes:
                continue
            self._page_sizes[key] = size
            self.paper_combo.addItem(size.name(), key)
        default_size = info.defaultPageSize() if info is not None else QPageSize(QPageSize.PageSizeId.A4)
        preferred = previous_paper or default_size.key()
        index = self.paper_combo.findData(preferred)
        self.paper_combo.setCurrentIndex(index if index >= 0 else 0)

        resolutions: list[int] = []
        if info is not None:
            try:
                resolutions = sorted({int(value) for value in info.supportedResolutions() if int(value) > 0})
            except Exception:
                resolutions = []
        if not resolutions:
            resolutions = [300]
        self.resolution_combo.clear()
        for value in resolutions:
            self.resolution_combo.addItem(f"{value} dpi", value)
        resolution_index = self.resolution_combo.findData(300)
        self.resolution_combo.setCurrentIndex(resolution_index if resolution_index >= 0 else 0)

        color_modes: list[Any] = []
        if info is not None:
            try:
                color_modes = list(info.supportedColorModes())
            except Exception:
                color_modes = []
        if not color_modes:
            color_modes = [QPrinter.ColorMode.Color, QPrinter.ColorMode.GrayScale]
        self.color_combo.clear()
        for mode in color_modes:
            label = "Color" if mode == QPrinter.ColorMode.Color else "Grayscale"
            self.color_combo.addItem(label, int(mode.value))
        default_color = info.defaultColorMode() if info is not None else QPrinter.ColorMode.Color
        color_index = self.color_combo.findData(int(default_color.value))
        self.color_combo.setCurrentIndex(color_index if color_index >= 0 else 0)
        self.color_combo.setEnabled(
            len(color_modes) > 1 and not self.use_driver_preferences_check.isChecked()
        )

        duplex_modes: list[Any] = []
        if info is not None:
            try:
                duplex_modes = list(info.supportedDuplexModes())
            except Exception:
                duplex_modes = []
        if not duplex_modes:
            duplex_modes = [QPrinter.DuplexMode.DuplexNone]
        duplex_labels = {
            QPrinter.DuplexMode.DuplexNone: "Off",
            QPrinter.DuplexMode.DuplexAuto: "Automatic",
            QPrinter.DuplexMode.DuplexLongSide: "Long edge",
            QPrinter.DuplexMode.DuplexShortSide: "Short edge",
        }
        self.duplex_combo.clear()
        for mode in duplex_modes:
            self.duplex_combo.addItem(duplex_labels.get(mode, str(mode)), int(mode.value))
        default_duplex = info.defaultDuplexMode() if info is not None else QPrinter.DuplexMode.DuplexNone
        duplex_index = self.duplex_combo.findData(int(default_duplex.value))
        self.duplex_combo.setCurrentIndex(duplex_index if duplex_index >= 0 else 0)
        self.duplex_combo.setEnabled(
            len(duplex_modes) > 1 and not self.use_driver_preferences_check.isChecked()
        )
        self.resolution_combo.setEnabled(not self.use_driver_preferences_check.isChecked())

        page_count = len(self._page_sizes)
        resolution_text = ", ".join(str(value) for value in resolutions[:4])
        if len(resolutions) > 4:
            resolution_text += ", …"
        self.capability_label.setText(
            f"Printer preferences · {page_count} paper size{'s' if page_count != 1 else ''} · "
            f"quality {resolution_text} dpi · "
            f"{'duplex available' if len(duplex_modes) > 1 else 'one-sided printing'}"
        )
        self._load_margins_from_printer()

    def _load_margins_from_printer(self) -> None:
        layout = self.printer.pageLayout()
        layout.setUnits(QPageLayout.Unit.Millimeter)
        margins = layout.margins()
        horizontal_margin = max(
            MINIMUM_PRINT_MARGIN_MM,
            float(margins.left()),
            float(margins.right()),
        )
        values = {
            "left": horizontal_margin,
            "top": max(MINIMUM_PRINT_MARGIN_MM, float(margins.top())),
            "right": horizontal_margin,
            "bottom": max(MINIMUM_PRINT_MARGIN_MM, float(margins.bottom())),
        }
        for key, value in values.items():
            spin = self.margin_spins[key]
            spin.blockSignals(True)
            spin.setValue(max(MINIMUM_PRINT_MARGIN_MM, float(value)))
            spin.blockSignals(False)

    def _appearance_mode(self) -> str:
        return str(self.appearance_combo.currentData() or "standard")

    def _appearance_changed(self, *_args) -> None:
        if self._source_snapshot is None:
            return
        self._snapshot = DashboardPrintRenderer.apply_ink_mode(
            self._source_snapshot, self._appearance_mode()
        )
        self._refresh_preview()

    def _driver_preferences_toggled(self, enabled: bool) -> None:
        # Driver-managed mode preserves vendor-specific settings such as Draft
        # and Draft Vivid. Disable the overlapping generic controls to make the
        # source of quality settings explicit.
        for control in (self.color_combo, self.duplex_combo, self.resolution_combo):
            control.setEnabled(not enabled)
        self._settings_changed()

    def _settings_changed(self, *_args) -> None:
        if self._syncing:
            return
        custom = str(self.scale_mode_combo.currentData() or "") == "custom"
        self.width_percent_spin.setVisible(custom)
        self._apply_settings_to_printer()
        self.preview_timer.start()

    def _apply_settings_to_printer(self, printer: QPrinter | None = None) -> None:
        target = printer or self.printer
        paper = self._page_sizes.get(str(self.paper_combo.currentData() or ""))
        if paper is not None:
            target.setPageSize(paper)
        orientation = (
            QPageLayout.Orientation.Landscape
            if self.orientation_combo.currentData() == "landscape"
            else QPageLayout.Orientation.Portrait
        )
        target.setPageOrientation(orientation)
        if not self.use_driver_preferences_check.isChecked():
            color_data = self.color_combo.currentData()
            if color_data is not None:
                target.setColorMode(QPrinter.ColorMode(int(color_data)))
            duplex_data = self.duplex_combo.currentData()
            if duplex_data is not None:
                target.setDuplex(QPrinter.DuplexMode(int(duplex_data)))
            resolution = int(self.resolution_combo.currentData() or 300)
            target.setResolution(max(72, resolution))
        target.setCopyCount(self.copies_spin.value())
        if hasattr(target, "setCollateCopies"):
            target.setCollateCopies(self.collate_check.isChecked())

        layout = target.pageLayout()
        layout.setUnits(QPageLayout.Unit.Millimeter)
        minimums = layout.minimumMargins()
        horizontal_margin = max(
            MINIMUM_PRINT_MARGIN_MM,
            self.margin_spins["left"].value(),
            self.margin_spins["right"].value(),
            float(minimums.left()),
            float(minimums.right()),
        )
        top_margin = max(
            MINIMUM_PRINT_MARGIN_MM,
            self.margin_spins["top"].value(),
            float(minimums.top()),
        )
        bottom_margin = max(
            MINIMUM_PRINT_MARGIN_MM,
            self.margin_spins["bottom"].value(),
            float(minimums.bottom()),
        )
        margins = QMarginsF(
            horizontal_margin,
            top_margin,
            horizontal_margin,
            bottom_margin,
        )
        layout.setMargins(margins, QPageLayout.OutOfBoundsPolicy.Clamp)
        target.setPageLayout(layout)

        # Keep the displayed horizontal values synchronized with the uniform
        # margin actually sent to the printer.
        for key in ("left", "right"):
            spin = self.margin_spins[key]
            spin.blockSignals(True)
            spin.setValue(horizontal_margin)
            spin.blockSignals(False)

    def _range_mode_changed(self, *_args) -> None:
        custom = str(self.page_range_combo.currentData() or "all") == "custom"
        self.range_widget.setVisible(custom)
        self._update_page_controls()

    def _range_value_changed(self, *_args) -> None:
        if self._syncing:
            return
        if self.from_page_spin.value() > self.to_page_spin.value():
            sender = self.sender()
            if sender is self.from_page_spin:
                self.to_page_spin.setValue(self.from_page_spin.value())
            else:
                self.from_page_spin.setValue(self.to_page_spin.value())

    def _toggle_advanced(self) -> None:
        visible = not self.advanced_body.isVisible()
        self.advanced_body.setVisible(visible)
        self.advanced_button.set_action(
            "collapse" if visible else "expand",
            "Hide advanced printer preferences" if visible else "Show advanced printer preferences",
        )

    def _scale_mode(self) -> str:
        return str(self.scale_mode_combo.currentData() or "fit_width")

    def _current_plan(self):
        if self._snapshot is None:
            return []
        return DashboardPrintRenderer.printer_page_plan(
            self.printer,
            self._snapshot,
            scale_mode=self._scale_mode(),
            width_percent=self.width_percent_spin.value(),
        )

    def _refresh_preview(self) -> None:
        if self._snapshot is None:
            return
        self._apply_settings_to_printer()
        self.preview.updatePreview()
        self._update_page_controls()

    def _paint_preview(self, printer: QPrinter) -> None:
        if self._snapshot is None:
            return
        try:
            DashboardPrintRenderer.paint(
                printer,
                self._snapshot,
                scale_mode=self._scale_mode(),
                width_percent=self.width_percent_spin.value(),
            )
        except RuntimeError as exc:
            self.status_label.setText(str(exc))

    def _preview_changed(self) -> None:
        self._install_preview_wheel_zoom()
        self._update_page_controls()

    def _update_page_controls(self) -> None:
        count = max(1, len(self._current_plan()))
        current = min(count, max(1, self.preview.currentPage()))
        if self.preview.currentPage() != current:
            self.preview.setCurrentPage(current)
        self.page_label.setText(f"Page {current} of {count}")
        self.previous_button.setEnabled(current > 1)
        self.next_button.setEnabled(current < count)
        self._syncing = True
        try:
            for spin in (self.from_page_spin, self.to_page_spin):
                spin.setMaximum(count)
            self.from_page_spin.setValue(min(self.from_page_spin.value(), count))
            self.to_page_spin.setValue(max(self.from_page_spin.value(), min(self.to_page_spin.value(), count)))
            if self.to_page_spin.value() == 1 and count > 1:
                self.to_page_spin.setValue(count)
        finally:
            self._syncing = False
        printer_name = str(self.printer_combo.currentData() or "Preview only")
        self.status_label.setText(
            f"Readable vertical report · {count} page{'s' if count != 1 else ''} · {printer_name}"
        )

    def _selected_pages(self) -> list[int]:
        count = max(1, len(self._current_plan()))
        mode = str(self.page_range_combo.currentData() or "all")
        if mode == "current":
            return [min(count, max(1, self.preview.currentPage()))]
        if mode == "custom":
            start = min(count, max(1, self.from_page_spin.value()))
            end = min(count, max(start, self.to_page_spin.value()))
            return list(range(start, end + 1))
        return list(range(1, count + 1))

    def _completed_print_record(
        self,
        *,
        printer_name: str,
        selected_pages: list[int],
    ) -> dict[str, Any]:
        if self.report_metadata is None:
            raise RuntimeError("The print report control number is not available.")
        current_records = getattr(self.dashboard, "current_filtered_records", lambda: [])()
        margins = {
            key: round(spin.value(), 1)
            for key, spin in self.margin_spins.items()
        }
        total_pages = max(1, len(self._current_plan()))
        copies = self.copies_spin.value()
        return {
            "control_number": self.report_metadata.control_number,
            "generated_at": self.report_metadata.generated_at.isoformat(),
            "printed_at": self._clock().isoformat(),
            "system_name": self.report_metadata.system_name,
            "barcode_value": self.report_metadata.control_number,
            "scope": self._filter_scope,
            "survey_count": len(current_records),
            "printer_name": printer_name,
            "page_count": total_pages,
            "selected_pages": list(selected_pages),
            "selected_page_count": len(selected_pages),
            "copies": copies,
            "printed_sheet_count": len(selected_pages) * copies,
            "status": "printed",
            "settings": {
                "printer_name": printer_name,
                "paper_size": self.paper_combo.currentText(),
                "orientation": str(self.orientation_combo.currentData() or "portrait"),
                "color_mode": "Windows driver preference" if self.use_driver_preferences_check.isChecked() else self.color_combo.currentText(),
                "duplex": "Windows driver preference" if self.use_driver_preferences_check.isChecked() else self.duplex_combo.currentText(),
                "resolution_dpi": None if self.use_driver_preferences_check.isChecked() else int(self.resolution_combo.currentData() or 300),
                "copies": copies,
                "collated": self.collate_check.isChecked(),
                "margins_mm": margins,
                "scale_mode": self._scale_mode(),
                "width_percent": self.width_percent_spin.value(),
                "appearance_mode": self._appearance_mode(),
                "use_windows_driver_preferences": self.use_driver_preferences_check.isChecked(),
            },
        }

    def _print(self) -> None:
        if self._snapshot is None:
            self.status_label.setText("The complete Dashboard preview is not ready.")
            return
        if self.report_metadata is None:
            self.status_label.setText("The print control number is not available.")
            return
        try:
            if self.print_store is not None:
                audit = self.print_store.get(self._audit_record_id)
                if audit is None:
                    raise RuntimeError("The print-audit reservation was not found.")
                current_control = str(
                    audit.get("control_number") or ""
                ).strip().upper()
                current_status = str(audit.get("status") or "").casefold()
                if current_status != "reserved":
                    raise RuntimeError(
                        f"The print-audit reservation is {current_status or 'unavailable'}."
                    )
            else:
                current_control = str(
                    self._control_number_provider(
                        self.report_metadata.generated_at
                    )
                ).strip().upper()
        except Exception as exc:
            message = f"The print control number could not be verified: {exc}"
            self.status_label.setText(message)
            self._fail_audit(message)
            self.print_failed.emit(message)
            return
        if current_control != self.report_metadata.control_number:
            message = (
                "This preview's print control number is no longer available. "
                "Close and reopen Print Preview before printing."
            )
            self.status_label.setText(message)
            self.print_button.setEnabled(False)
            self._fail_audit(message)
            self.print_failed.emit(message)
            return
        printer_name = str(self.printer_combo.currentData() or "")
        if not printer_name:
            self.status_label.setText("No physical printer is available.")
            return
        self._apply_settings_to_printer()
        print_printer = self.printer
        if self._uses_native_print_executor:
            # Create a fresh printer object after the Windows preferences dialog
            # closes so vendor-specific DEVMODE settings (Draft, Draft Vivid,
            # economy modes, and similar options) are loaded for this job.
            print_printer = QPrinter(QPrinter.PrinterMode.HighResolution)
            print_printer.setPrinterName(printer_name)
            print_printer.setFullPage(False)
            if hasattr(print_printer, "setDocName"):
                print_printer.setDocName("School CSM Control Center Dashboard")
            self._apply_settings_to_printer(print_printer)
        print_printer.setOutputFormat(QPrinter.OutputFormat.NativeFormat)
        print_printer.setOutputFileName("")
        pages = self._selected_pages()
        try:
            record = self._completed_print_record(
                printer_name=printer_name,
                selected_pages=pages,
            )
            if self.print_store is not None:
                self.print_store.mark_submitting(
                    self._audit_record_id,
                    {
                        "printer_name": printer_name,
                        "page_count": record.get("page_count"),
                        "copies": record.get("copies"),
                        "selected_pages": pages,
                        "settings": record.get("settings"),
                        "scope": record.get("scope"),
                    },
                )
                self._audit_status = "submitting"
                self.audit_history_changed.emit()
        except Exception as exc:
            message = f"The Dashboard print audit could not be prepared: {exc}"
            self.status_label.setText(message)
            self._fail_audit(message)
            self.print_failed.emit(message)
            return
        self.status_label.setText(f"Sending {len(pages)} Dashboard page(s) to {printer_name}…")
        self.print_button.setEnabled(False)
        QApplication.processEvents()
        try:
            printed = self._print_executor(
                print_printer,
                self._snapshot,
                scale_mode=self._scale_mode(),
                width_percent=self.width_percent_spin.value(),
                selected_pages=pages,
            )
        except Exception as exc:
            message = f"Dashboard printing failed: {exc}"
            self.status_label.setText(message)
            self.print_button.setEnabled(True)
            self._fail_audit(message)
            self.print_failed.emit(message)
            return
        if printed <= 0:
            message = "Dashboard printing failed: the printer did not accept any pages."
            self.status_label.setText(message)
            self.print_button.setEnabled(True)
            self._fail_audit(message)
            self.print_failed.emit(message)
            return
        try:
            if self.print_store is not None:
                record = self.print_store.mark_submitted(
                    self._audit_record_id,
                    record,
                )
                self._audit_status = "submitted"
                self.audit_history_changed.emit()
        except Exception as exc:
            message = f"The Dashboard was printed, but its audit could not be finalized: {exc}"
            self.status_label.setText(message)
            self._fail_audit(message)
            self.print_failed.emit(message)
            return
        message = (
            f"{record['control_number']} sent to {printer_name} "
            f"({printed} page{'s' if printed != 1 else ''})."
        )
        self.print_job_completed.emit(record, message)
        self.print_completed.emit(message)
        self.close_overlay()

    @staticmethod
    def _execute_native_print(
        printer: QPrinter,
        snapshot: DashboardSnapshot,
        **options,
    ) -> int:
        return DashboardPrintRenderer.paint(printer, snapshot, **options)

    def _install_preview_wheel_zoom(self) -> None:
        """Attach wheel-to-zoom handling to the preview and its internal viewports."""

        targets = [self.preview, *self.preview.findChildren(QWidget)]
        for target in targets:
            target.installEventFilter(self._preview_wheel_filter)
            target.setProperty("dashboardPreviewWheelZoom", True)

    def _previous_page(self) -> None:
        self.preview.setCurrentPage(max(1, self.preview.currentPage() - 1))
        self._update_page_controls()

    def _next_page(self) -> None:
        count = max(1, len(self._current_plan()))
        self.preview.setCurrentPage(min(count, self.preview.currentPage() + 1))
        self._update_page_controls()

    def _fit_preview_page(self) -> None:
        self.preview.fitInView()

    def _fit_preview_width(self) -> None:
        try:
            self.preview.setZoomMode(QPrintPreviewWidget.ZoomMode.FitToWidth)
        except AttributeError:
            self.preview.fitInView()

    def _zoom_in(self) -> None:
        self.preview.zoomIn(1.2)

    def _zoom_out(self) -> None:
        self.preview.zoomOut(1.2)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self._position_children()

    def _position_children(self) -> None:
        self.scrim.setGeometry(self.rect())
        margin = 12 if self.width() < 900 else 20
        self.panel.setGeometry(
            margin,
            margin,
            max(1, self.width() - margin * 2),
            max(1, self.height() - margin * 2),
        )
        self.settings_scroll.setFixedWidth(270 if self.panel.width() < 940 else 320)

    def _apply_styles(self) -> None:
        self.setStyleSheet(
            f"""
            QWidget#dashboard_print_overlay {{ background: transparent; }}
            QFrame#dashboard_print_scrim {{ background: rgba(1,8,18,0.84); border: none; }}
            QFrame#dashboard_print_panel {{ background: {theme.WINDOW_BG_ALT}; border: 1px solid rgba(43,217,197,0.72); border-radius: 16px; }}
            QFrame#dashboard_print_header, QFrame#dashboard_print_footer {{ background: {theme.TITLE_BG}; border: none; }}
            QFrame#dashboard_print_header {{ border-bottom: 1px solid {theme.DIVIDER}; border-top-left-radius: 16px; border-top-right-radius: 16px; }}
            QFrame#dashboard_print_footer {{ border-top: 1px solid {theme.DIVIDER}; border-bottom-left-radius: 16px; border-bottom-right-radius: 16px; }}
            QLabel#dashboard_print_title {{ color: {theme.TEXT_PRIMARY}; font-size: 19px; font-weight: 800; }}
            QLabel#dashboard_print_scope {{ color: {theme.TEXT_SECONDARY}; font-size: 10px; }}
            QWidget#dashboard_print_content, QWidget#dashboard_print_settings_body {{ background: {theme.WINDOW_BG_ALT}; }}
            QScrollArea#dashboard_print_settings_scroll {{ background: transparent; border: none; }}
            QFrame#dashboard_print_settings_section, QFrame#dashboard_print_advanced_header {{ background: rgba(10,29,48,0.94); border: 1px solid {theme.CARD_BORDER}; border-radius: 9px; }}
            QLabel#dashboard_print_section_title {{ color: {theme.TEXT_PRIMARY}; font-size: 12px; font-weight: 800; }}
            QLabel#dashboard_print_section_subtitle, QLabel#dashboard_print_mini_label {{ color: {theme.TEXT_MUTED}; font-size: 9px; }}
            QLabel#dashboard_print_printer_status {{ color: {theme.ACCENT_CYAN}; background: rgba(7,25,42,0.72); border: 1px solid rgba(27,85,114,0.46); border-radius: 8px; padding: 8px; font-size: 10px; font-weight: 700; }}
            QLabel#dashboard_print_capabilities {{ color: {theme.TEXT_MUTED}; font-size: 9px; padding: 2px 4px; }}
            QComboBox#dashboard_print_combo, QSpinBox#dashboard_print_spin, QDoubleSpinBox#dashboard_print_margin_spin {{ min-height: 30px; font-size: 10px; }}
            QCheckBox#dashboard_print_checkbox {{ color: {theme.TEXT_SECONDARY}; font-size: 10px; font-weight: 700; }}
            QFrame#dashboard_print_preview_surface {{ background: rgba(5,19,33,0.72); border: 1px solid {theme.CARD_BORDER}; border-radius: 10px; }}
            QFrame#dashboard_print_preview_toolbar {{ background: {theme.PANEL_BG}; border: 1px solid {theme.DIVIDER}; border-radius: 8px; }}
            QLabel#dashboard_print_page_label {{ color: {theme.TEXT_PRIMARY}; font-size: 10px; font-weight: 800; }}
            QPrintPreviewWidget#dashboard_print_preview_widget {{ background: #14283A; border: none; }}
            QLabel#dashboard_print_status {{ color: {theme.TEXT_SECONDARY}; font-size: 10px; font-weight: 700; }}
            QFormLayout QLabel {{ color: {theme.TEXT_SECONDARY}; font-size: 10px; font-weight: 700; }}
            """
        )
