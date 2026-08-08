from __future__ import annotations

from copy import deepcopy
from typing import Any

from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QDate, QTimer, Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from school_csm_control_center.demographics import (
    REGION_OPTIONS,
    age_bracket_for,
    normalize_region_code,
)
from school_csm_control_center.questionnaire import (
    CC_QUESTIONS,
    MODE_LABELS,
    SQD_QUESTIONS,
)
from school_csm_control_center.school_services import service_value_from_record
from school_csm_control_center.storage.field_preset_store import FieldPresetStore
from school_csm_control_center.storage.survey_store import SurveyStoreError
from school_csm_control_center.ui import theme
from school_csm_control_center.ui.calendar_overlay import CalendarPickerOverlay
from school_csm_control_center.ui.controls import NoWheelComboBox, NoWheelDateEdit, OptionalAgeSpinBox
from school_csm_control_center.ui.field_preset_overlay import FieldPresetOverlay
from school_csm_control_center.ui.overlays import ClickScrim, OverlayPrompt
from school_csm_control_center.ui.survey_widgets import (
    CollapsibleServiceChecklist,
    ExclusiveCheckGroup,
    SQDRatingTable,
    build_sqd_legend,
)
from school_csm_control_center.ui.widgets import TooltipIconButton


class FormField(QWidget):
    def __init__(
        self,
        label: str,
        control: QWidget,
        hint: str = "",
        parent: QWidget | None = None,
        *,
        buddy: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("survey_form_field")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        self.label = QLabel(label)
        self.label.setObjectName("survey_field_label")
        self.label.setWordWrap(True)
        buddy_control = buddy or control
        if buddy_control.focusPolicy() == Qt.FocusPolicy.NoFocus:
            focusable_children = [
                child
                for child in buddy_control.findChildren(QWidget)
                if child.focusPolicy() != Qt.FocusPolicy.NoFocus
            ]
            buddy_control = focusable_children[0] if focusable_children else buddy_control
        if buddy_control.focusPolicy() != Qt.FocusPolicy.NoFocus:
            self.label.setBuddy(buddy_control)
            buddy_control.setAccessibleName(label)
            if hint:
                buddy_control.setAccessibleDescription(hint)
        layout.addWidget(self.label)
        layout.addWidget(control)
        self.hint = QLabel(hint)
        self.hint.setObjectName("survey_field_hint")
        self.hint.setWordWrap(True)
        self.hint.setVisible(bool(hint))
        layout.addWidget(self.hint)


class FormSection(QFrame):
    def __init__(self, title: str, subtitle: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("survey_form_section")
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 16)
        root.setSpacing(11)
        title_label = QLabel(title)
        title_label.setObjectName("survey_section_title")
        root.addWidget(title_label)
        subtitle_label = QLabel(subtitle)
        subtitle_label.setObjectName("survey_section_subtitle")
        subtitle_label.setWordWrap(True)
        subtitle_label.setVisible(bool(subtitle))
        root.addWidget(subtitle_label)
        self.content = QVBoxLayout()
        self.content.setSpacing(12)
        root.addLayout(self.content)


class SurveyEntryOverlay(QWidget):
    """Full-window scrim with an animated, draft-preserving left drawer."""

    survey_saved = Signal(object, bool)
    drawer_closed = Signal()

    def __init__(self, store, parent: QWidget, *, settings_provider=None) -> None:
        super().__init__(parent)
        self.store = store
        self.settings_provider = settings_provider or (lambda: {})
        self.preset_store = FieldPresetStore(self.store.project_root)
        self._field_presets = self.preset_store.load()
        self._is_open = False
        self._editing_id: str | None = None
        self._animation: QPropertyAnimation | None = None
        self._previous_focus: QWidget | None = None
        self.setObjectName("survey_entry_overlay")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet("QWidget#survey_entry_overlay { background: transparent; }")
        self.hide()

        self.scrim = ClickScrim(self)
        self.scrim.setObjectName("survey_drawer_scrim")
        self.scrim.setStyleSheet(
            "QFrame#survey_drawer_scrim { background: rgba(1, 8, 18, 0.70); border: none; }"
        )
        self.scrim.clicked.connect(self.close_drawer)

        self.drawer = QFrame(self)
        self.drawer.setObjectName("survey_entry_drawer")
        self.drawer.setStyleSheet(self._drawer_stylesheet())
        drawer_layout = QVBoxLayout(self.drawer)
        drawer_layout.setContentsMargins(0, 0, 0, 0)
        drawer_layout.setSpacing(0)

        header = QFrame()
        header.setObjectName("survey_drawer_header")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(22, 17, 16, 17)
        header_layout.setSpacing(12)
        heading = QVBoxLayout()
        heading.setSpacing(3)
        self.title_label = QLabel("Add Survey Result")
        self.title_label.setObjectName("survey_drawer_title")
        heading.addWidget(self.title_label)
        subtitle = QLabel("ARTA Annex A - answers update the analysis immediately")
        subtitle.setObjectName("survey_drawer_subtitle")
        subtitle.setWordWrap(True)
        heading.addWidget(subtitle)
        header_layout.addLayout(heading, 1)
        self.preset_button = TooltipIconButton("lock", "Configure pre-filled and locked fields")
        self.preset_button.clicked.connect(self._open_preset_overlay)
        header_layout.addWidget(self.preset_button)
        self.close_button = TooltipIconButton("close", "Collapse survey drawer")
        self.close_button.clicked.connect(self.close_drawer)
        header_layout.addWidget(self.close_button)
        drawer_layout.addWidget(header)

        self.status_banner = QLabel()
        self.status_banner.setObjectName("survey_status_banner")
        self.status_banner.setWordWrap(True)
        self.status_banner.hide()
        drawer_layout.addWidget(self.status_banner)

        self.form_scroll = QScrollArea()
        self.form_scroll.setObjectName("survey_drawer_scroll")
        self.form_scroll.setWidgetResizable(True)
        self.form_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.form_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.form_body = QWidget()
        self.form_body.setObjectName("survey_form_body")
        self.form_layout = QVBoxLayout(self.form_body)
        self.form_layout.setContentsMargins(18, 16, 18, 22)
        self.form_layout.setSpacing(14)
        self._build_form()
        self.form_layout.addStretch(1)
        self.form_scroll.setWidget(self.form_body)
        drawer_layout.addWidget(self.form_scroll, 1)

        footer = QFrame()
        footer.setObjectName("survey_drawer_footer")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(18, 13, 18, 13)
        footer_layout.setSpacing(10)
        footer_layout.addStretch(1)
        self.reset_button = TooltipIconButton("reset", "Clear survey form", role="subtle")
        self.reset_button.clicked.connect(self._confirm_reset)
        footer_layout.addWidget(self.reset_button)
        self.save_button = TooltipIconButton(
            "save", "Save survey result", icon_color=theme.SUCCESS, role="success", button_size=44
        )
        self.save_button.clicked.connect(self._save)
        footer_layout.addWidget(self.save_button)
        drawer_layout.addWidget(footer)

        self.prompt = OverlayPrompt(self)
        self.preset_overlay = FieldPresetOverlay(self, self)
        self.calendar_overlay = CalendarPickerOverlay(self, self)
        self.calendar_overlay.date_selected.connect(self._calendar_date_selected)
        self.escape_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        self.escape_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.escape_shortcut.activated.connect(self.close_drawer)
        self._apply_mode("onsite")
        self._apply_field_presets()
        self._refresh_control_number()

    @property
    def is_open(self) -> bool:
        return self._is_open

    def _build_form(self) -> None:
        source_section = FormSection(
            "Survey source",
            "Encode either the onsite sheet or the separate online questionnaire.",
        )
        source_grid = QGridLayout()
        source_grid.setHorizontalSpacing(12)
        source_grid.setVerticalSpacing(11)
        source_grid.setColumnStretch(0, 1)
        source_grid.setColumnStretch(1, 1)
        self.mode_combo = NoWheelComboBox()
        self.mode_combo.setObjectName("survey_mode_combo")
        for key in ("onsite", "online"):
            self.mode_combo.addItem(MODE_LABELS[key], key)
        self._prepare_combo(self.mode_combo, 10)
        self.mode_combo.currentIndexChanged.connect(self._mode_changed)
        source_grid.addWidget(FormField("Questionnaire", self.mode_combo), 0, 0)

        self.control_input = QLineEdit()
        self.control_input.setObjectName("survey_control_number")
        self.control_input.setReadOnly(True)
        self.control_input.setPlaceholderText("Generated after selecting a survey date")
        self.control_input.setToolTip(
            "Generated automatically from the survey year, month, and response sequence"
        )
        self.control_input.setFixedHeight(40)
        self.control_field = FormField("Control No.", self.control_input)
        source_grid.addWidget(self.control_field, 0, 1)

        self.date_control = QFrame()
        self.date_control.setObjectName("survey_date_control")
        date_control_layout = QHBoxLayout(self.date_control)
        date_control_layout.setContentsMargins(0, 0, 0, 0)
        date_control_layout.setSpacing(8)
        self.date_edit = NoWheelDateEdit()
        self.date_edit.setObjectName("survey_date_edit")
        self.date_edit.setDisplayFormat("MMMM d, yyyy")
        self.date_edit.setCalendarPopup(False)
        self.date_edit.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.date_edit.setReadOnly(True)
        self.date_edit.setFixedHeight(40)
        self.date_edit.setDate(QDate.currentDate())
        self.date_edit.dateChanged.connect(self._survey_date_changed)
        date_control_layout.addWidget(self.date_edit, 1)
        self.calendar_button = TooltipIconButton(
            "calendar", "Select survey date", button_size=40
        )
        self.calendar_button.clicked.connect(self._open_calendar_overlay)
        date_control_layout.addWidget(self.calendar_button)
        self.date_field = FormField(
            "Survey date",
            self.date_control,
            "Use the date written on the onsite form.",
        )
        source_grid.addWidget(
            self.date_field,
            1,
            0,
            alignment=Qt.AlignmentFlag.AlignTop,
        )

        self.office_input = QLineEdit(self._configured_school_name())
        self.office_input.setPlaceholderText("Agency or office")
        self.office_input.setFixedHeight(40)
        self.office_field = FormField("Agency / office", self.office_input)
        source_grid.addWidget(
            self.office_field,
            1,
            1,
            alignment=Qt.AlignmentFlag.AlignTop,
        )
        source_section.content.addLayout(source_grid)
        self.form_layout.addWidget(source_section)

        profile_section = FormSection(
            "Client and transaction",
            "Personal fields are optional, matching the confidentiality notice on the form.",
        )
        profile_grid = QGridLayout()
        profile_grid.setHorizontalSpacing(12)
        profile_grid.setVerticalSpacing(11)
        self.service_input = CollapsibleServiceChecklist()
        profile_grid.addWidget(
            FormField(
                "Service availed",
                self.service_input,
                "Select one or more official school transactions.",
            ),
            0,
            0,
            1,
            2,
        )

        self.client_type_group = ExclusiveCheckGroup(
            ("Citizen", "Business", "Government"),
            other_placeholder="Other client type",
        )
        profile_grid.addWidget(
            FormField("Client type", self.client_type_group),
            1,
            0,
            alignment=Qt.AlignmentFlag.AlignTop,
        )
        self.sex_group = ExclusiveCheckGroup(
            ("Male", "Female"),
            other_placeholder="Self-described sex",
        )
        profile_grid.addWidget(
            FormField("Sex", self.sex_group),
            1,
            1,
            alignment=Qt.AlignmentFlag.AlignTop,
        )

        self.age_spin = OptionalAgeSpinBox()
        profile_grid.addWidget(FormField("Age", self.age_spin), 2, 0)
        self.region_input = NoWheelComboBox()
        self.region_input.setObjectName("survey_region_combo")
        self.region_input.addItem("Not Provided", "did_not_specify")
        for option in REGION_OPTIONS:
            if option["code"] != "did_not_specify":
                self.region_input.addItem(option["label"], option["code"])
        self._prepare_combo(self.region_input, 22)
        profile_grid.addWidget(FormField("Region", self.region_input), 2, 1)

        self.email_input = QLineEdit()
        self.email_input.setPlaceholderText("Optional email address")
        self.email_field = FormField("Email address", self.email_input)
        profile_grid.addWidget(self.email_field, 3, 0, 1, 2)
        profile_section.content.addLayout(profile_grid)
        self.form_layout.addWidget(profile_section)

        cc_section = FormSection(
            "Citizen's Charter",
            "The available answers and skip logic follow the selected questionnaire.",
        )
        self.cc_labels: dict[str, QLabel] = {}
        self.cc_combos: dict[str, QComboBox] = {}
        for code in ("cc1", "cc2", "cc3"):
            label = QLabel()
            label.setObjectName("survey_question_label")
            label.setWordWrap(True)
            combo = NoWheelComboBox()
            combo.setObjectName(f"survey_{code}_combo")
            self._prepare_combo(combo, 18)
            combo.currentIndexChanged.connect(self._update_cc_branching)
            field = FormField("", combo)
            field.label.hide()
            field.layout().insertWidget(0, label)
            label.setBuddy(combo)
            combo.setAccessibleName(code.upper())
            cc_section.content.addWidget(field)
            self.cc_labels[code] = label
            self.cc_combos[code] = combo
        self.cc_reason_input = QLineEdit()
        self.cc_reason_input.setPlaceholderText("Reason the Charter could not be used")
        self.cc_reason_field = FormField("CC3 reason", self.cc_reason_input)
        self.cc_reason_field.hide()
        cc_section.content.addWidget(self.cc_reason_field)
        self.form_layout.addWidget(cc_section)

        self.sqd_section = FormSection(
            "Service Quality Dimensions",
            "Required: tick one response for every statement. N/A is available only for the onsite form.",
        )
        self.sqd_legend = build_sqd_legend()
        self.sqd_section.content.addWidget(self.sqd_legend)
        self.sqd_table = SQDRatingTable()
        self.sqd_section.content.addWidget(self.sqd_table)
        self.sqd_checks = self.sqd_table.visible_checks()
        self.form_layout.addWidget(self.sqd_section)

        feedback_section = FormSection(
            "Client feedback",
            "Suggestions and remarks are optional and remain attached to this response.",
        )
        self.feedback_input = QTextEdit()
        self.feedback_input.setObjectName("survey_feedback_input")
        self.feedback_input.setAcceptRichText(False)
        self.feedback_input.setPlaceholderText("Suggestions on how services can be improved...")
        self.feedback_input.setMinimumHeight(92)
        self.feedback_input.setMaximumHeight(130)
        feedback_section.content.addWidget(self.feedback_input)
        self.form_layout.addWidget(feedback_section)

    @staticmethod
    def _prepare_combo(combo: QComboBox, minimum_contents: int) -> None:
        combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        combo.setMinimumContentsLength(minimum_contents)
        combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def _mode_changed(self) -> None:
        mode = self.mode_combo.currentData() or "onsite"
        self._apply_mode(str(mode))

    def _apply_mode(self, mode: str) -> None:
        online = mode == "online"
        self.control_field.setVisible(True)
        self.date_field.setVisible(True)
        self.email_field.setVisible(not online)
        self.feedback_input.setPlaceholderText(
            "Optional remarks..." if online else "Suggestions on how services can be improved..."
        )
        for code, spec in CC_QUESTIONS[mode].items():
            self.cc_labels[code].setText(f"{spec['code']}. {spec['prompt']}")
            combo = self.cc_combos[code]
            current = combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("Not answered", None)
            for option in spec["options"]:
                combo.addItem(option["label"], option["value"])
            self._select_data(combo, current)
            combo.blockSignals(False)

        self.sqd_table.set_mode(mode)
        self.sqd_checks = self.sqd_table.visible_checks()
        for legend_item in self.sqd_legend.findChildren(QLabel):
            if legend_item.property("score") == 0:
                legend_item.setVisible(not online)
        self._update_cc_branching()
        self._refresh_control_number()

    def _update_cc_branching(self) -> None:
        mode = str(self.mode_combo.currentData() or "onsite")
        cc1 = self.cc_combos["cc1"].currentData()
        cc2 = self.cc_combos["cc2"].currentData()
        if mode == "onsite":
            skipped = cc1 == 4
            self.cc_combos["cc2"].setEnabled(not skipped)
            self.cc_combos["cc3"].setEnabled(not skipped)
            if skipped:
                self._select_data(self.cc_combos["cc2"], 5)
                self._select_data(self.cc_combos["cc3"], 4)
            self.cc_reason_field.hide()
        else:
            skip_cc2 = cc1 == 3
            self.cc_combos["cc2"].setEnabled(not skip_cc2)
            if skip_cc2:
                self._select_data(self.cc_combos["cc2"], None)
            skip_cc3 = skip_cc2 or cc2 == 3
            self.cc_combos["cc3"].setEnabled(not skip_cc3)
            if skip_cc3:
                self._select_data(self.cc_combos["cc3"], None)
            self.cc_reason_field.setVisible(
                not skip_cc3 and self.cc_combos["cc3"].currentData() == 2
            )

    @staticmethod
    def _select_data(combo: QComboBox, value: Any) -> None:
        index = combo.findData(value)
        combo.setCurrentIndex(index if index >= 0 else 0)

    def open_for_add(self) -> None:
        if self._editing_id is not None:
            self.reset_form()
        self._editing_id = None
        if not self._field_presets.get("agency_visited", {}).get("prefill"):
            self.office_input.setText(self._configured_school_name())
        self.title_label.setText("Add Survey Result")
        self.save_button.setToolTip("Save survey result")
        self._set_preset_lock_state(True)
        self._refresh_control_number()
        self._open()

    def open_for_edit(self, record: dict[str, Any]) -> None:
        self.reset_form()
        self._editing_id = str(record.get("id") or "") or None
        self.title_label.setText("Edit Survey Result")
        self.save_button.setToolTip("Save survey changes")
        self._load_record(record)
        self._set_preset_lock_state(False)
        self._open()

    def _open(self) -> None:
        self._previous_focus = QApplication.focusWidget()
        self.setGeometry(self.parentWidget().rect())
        self._layout_overlay(open_state=False)
        self.show()
        self.raise_()
        self._is_open = True
        self._animate_drawer(self.drawer.pos(), QPoint(0, 0))
        self.service_input.setFocus(Qt.FocusReason.ActiveWindowFocusReason)

    def close_drawer(self) -> None:
        if not self.isVisible() or not self._is_open:
            return
        self._is_open = False
        end = QPoint(-self.drawer.width(), 0)
        self._animate_drawer(self.drawer.pos(), end, hide_when_finished=True)

    def _animate_drawer(self, start: QPoint, end: QPoint, *, hide_when_finished: bool = False) -> None:
        if self._animation is not None:
            self._animation.stop()
        self._animation = QPropertyAnimation(self.drawer, b"pos", self)
        self._animation.setDuration(230)
        self._animation.setStartValue(start)
        self._animation.setEndValue(end)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        if hide_when_finished:
            self._animation.finished.connect(self._finish_close)
        self._animation.start()
        # Some graphics drivers can suspend a property animation while a
        # frameless window is being exposed. Always settle at the requested
        # state after the nominal animation interval.
        if hide_when_finished:
            QTimer.singleShot(260, self._settle_closed)
        else:
            QTimer.singleShot(260, self._settle_open)

    def _settle_open(self) -> None:
        if self._is_open and self.isVisible():
            self.drawer.move(0, 0)

    def _settle_closed(self) -> None:
        if not self._is_open and self.isVisible():
            self.drawer.move(-self.drawer.width(), 0)
            self._finish_close()

    def _finish_close(self) -> None:
        if not self._is_open:
            self.hide()
            self.drawer_closed.emit()
            previous = self._previous_focus
            self._previous_focus = None
            try:
                if previous is not None and previous.isVisible() and previous.isEnabled():
                    previous.setFocus(Qt.FocusReason.OtherFocusReason)
            except RuntimeError:
                pass

    def focusNextPrevChild(self, next: bool) -> bool:  # noqa: A002, N802 - Qt API
        """Keep keyboard traversal inside the visible survey drawer."""

        focusable = [
            child
            for child in self.drawer.findChildren(QWidget)
            if child.isVisibleTo(self.drawer)
            and child.isEnabled()
            and child.focusPolicy() in {
                Qt.FocusPolicy.TabFocus,
                Qt.FocusPolicy.StrongFocus,
                Qt.FocusPolicy.WheelFocus,
            }
        ]
        current = QApplication.focusWidget()
        if focusable and current in focusable:
            index = focusable.index(current)
            at_boundary = (next and index == len(focusable) - 1) or (
                not next and index == 0
            )
            if at_boundary:
                target = focusable[0] if next else focusable[-1]
                target.setFocus(Qt.FocusReason.TabFocusReason)
                return True
        return super().focusNextPrevChild(next)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self._layout_overlay(open_state=self._is_open)

    def _layout_overlay(self, *, open_state: bool) -> None:
        self.scrim.setGeometry(self.rect())
        width = min(820, max(560, int(self.width() * 0.56)))
        width = min(width, max(420, self.width() - 36))
        self.drawer.resize(width, self.height())
        self.drawer.move(0 if open_state else -width, 0)
        if self.prompt.isVisible():
            self.prompt.setGeometry(self.rect())
            self.prompt.raise_()
        if self.preset_overlay.isVisible():
            self.preset_overlay.setGeometry(self.rect())
            self.preset_overlay.raise_()
        if self.calendar_overlay.isVisible():
            self.calendar_overlay.setGeometry(self.rect())
            self.calendar_overlay.raise_()

    def _save(self) -> None:
        record = self._record_from_form()
        missing_sqd = self._missing_required_sqd(record)
        self.sqd_table.set_required_missing(missing_sqd)
        error = self._validation_error(record)
        if error:
            self._show_status(error, kind="error")
            if missing_sqd:
                self.form_scroll.ensureWidgetVisible(self.sqd_section, 0, 18)
                first_code = missing_sqd[0]
                first_check = next(iter(self.sqd_checks[first_code].values()))
                first_check.setFocus(Qt.FocusReason.OtherFocusReason)
            return
        editing = self._editing_id is not None
        try:
            if editing:
                saved = self.store.update(self._editing_id, record)
            else:
                settings = self.settings_provider() or {}
                school_id = str(settings.get("school_id") or "")
                survey_date = self.date_edit.date().toString(Qt.DateFormat.ISODate)
                saved = self.store.add_with_next_control_number(
                    record,
                    survey_date,
                    school_id,
                    "SCC",
                )
        except (ValueError, OSError, SurveyStoreError) as exc:
            self._show_status(str(exc), kind="error")
            return
        self._show_status("Survey saved. The Dashboard and History are now up to date.", kind="success")
        self.survey_saved.emit(saved, editing)
        if editing:
            self._editing_id = None
            self.reset_form()
            self.close_drawer()
        else:
            mode = str(self.mode_combo.currentData() or "onsite")
            self.reset_form(keep_mode=mode)
            self.service_input.setFocus(Qt.FocusReason.ActiveWindowFocusReason)

    def _record_from_form(self) -> dict[str, Any]:
        mode = str(self.mode_combo.currentData() or "onsite")
        meta = {
            "survey_date": self.date_edit.date().toString(Qt.DateFormat.ISODate) if mode == "onsite" else "",
            "client_type": self.client_type_group.value(),
            "sex": self.sex_group.value(),
            "age": self.age_spin.optional_value(),
            "age_bracket": age_bracket_for(self.age_spin.optional_value()),
            "region": str(self.region_input.currentData() or "did_not_specify"),
            "agency_visited": self.office_input.text().strip(),
            "service_availed": self.service_input.values(),
        }
        cc = {code: combo.currentData() for code, combo in self.cc_combos.items()}
        cc["cc3_reason"] = self.cc_reason_input.text().strip()
        sqd = {
            code: self.sqd_table.value(code)
            for code in SQD_QUESTIONS[mode]
        }
        return {
            "source_code": "SCC",
            "mode": mode,
            "control_number": self.control_input.text().strip(),
            "meta": meta,
            "cc": cc,
            "sqd": sqd,
            "feedback": {
                "comments": self.feedback_input.toPlainText().strip(),
                "email": self.email_input.text().strip() if mode == "onsite" else "",
            },
        }

    def _validation_error(self, record: dict[str, Any]) -> str:
        missing_sqd = self._missing_required_sqd(record)
        if missing_sqd:
            missing_labels = ", ".join(code.upper() for code in missing_sqd)
            return (
                "Answer every Service Quality Dimension before saving. "
                f"Missing: {missing_labels}."
            )
        email = record["feedback"].get("email", "")
        if email and ("@" not in email or email.startswith("@") or email.endswith("@")):
            return "Enter a valid email address or leave the optional email field blank."
        if (
            record["mode"] == "online"
            and record["cc"].get("cc3") == 2
            and not record["cc"].get("cc3_reason")
        ):
            return "Enter the reason the Citizen's Charter could not be used."
        return ""

    @staticmethod
    def _missing_required_sqd(record: dict[str, Any]) -> list[str]:
        mode = "online" if str(record.get("mode")).casefold() == "online" else "onsite"
        valid_ratings = {1, 2, 3, 4, 5} if mode == "online" else {0, 1, 2, 3, 4, 5}
        answers = record.get("sqd") if isinstance(record.get("sqd"), dict) else {}
        return [
            code
            for code in SQD_QUESTIONS[mode]
            if answers.get(code) not in valid_ratings
        ]

    def _open_calendar_overlay(self) -> None:
        self.calendar_overlay.open_for(self.date_edit.date())

    def _calendar_date_selected(self, selected_date: QDate) -> None:
        if selected_date.isValid():
            self.date_edit.setDate(selected_date)

    def _survey_date_changed(self, _selected_date: QDate) -> None:
        self._refresh_control_number()

    def _refresh_control_number(self) -> None:
        if self._editing_id is not None or not hasattr(self, "control_input"):
            return
        mode = str(self.mode_combo.currentData() or "onsite")
        survey_date = self.date_edit.date().toString(Qt.DateFormat.ISODate)
        try:
            settings = self.settings_provider() or {}
            control_number = self.store.next_control_number(
                survey_date,
                str(settings.get("school_id") or ""),
                "SCC",
            )
        except (ValueError, OSError, SurveyStoreError) as exc:
            self.control_input.clear()
            self.control_input.setToolTip(str(exc))
            return
        self.control_input.setText(control_number)
        self.control_input.setToolTip(
            "Generated automatically as YYYY-MM-#### using the shared monthly SCC/WBS sequence"
        )

    def _open_preset_overlay(self) -> None:
        self.preset_overlay.open_overlay()

    def _configured_school_name(self) -> str:
        settings = self.settings_provider() or {}
        return " ".join(str(settings.get("school_name") or "School").split())

    def reload_configured_identity(self) -> None:
        """Refresh identity-dependent defaults without overwriting an open draft."""

        if (
            not self._is_open
            and self._editing_id is None
            and not self._field_presets.get("agency_visited", {}).get("prefill")
        ):
            self.office_input.setText(self._configured_school_name())

    def field_presets(self) -> dict[str, dict[str, Any]]:
        return deepcopy(self._field_presets)

    def preset_field_snapshot(self, key: str) -> tuple[Any, str]:
        value = self._preset_field_value(key)
        if key == "mode":
            preview = self.mode_combo.currentText()
        elif key == "survey_date":
            preview = self.date_edit.date().toString("MMMM d, yyyy")
        elif key == "age":
            preview = "Not Provided" if value is None else str(value)
        elif key == "service_availed":
            preview = self.service_input.text() or "Not Provided"
        else:
            preview = str(value or "Not provided")
        return value, preview

    def save_field_presets(self, fields: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        saved = self.preset_store.save(fields)
        self._field_presets = saved
        self._apply_field_presets()
        locked_count = sum(1 for settings in saved.values() if settings.get("locked"))
        prefill_count = sum(1 for settings in saved.values() if settings.get("prefill"))
        self._show_status(
            f"Field settings applied: {prefill_count} pre-filled, {locked_count} locked.",
            kind="success",
        )
        return deepcopy(saved)

    def _preset_field_value(self, key: str) -> Any:
        if key == "mode":
            return str(self.mode_combo.currentData() or "onsite")
        if key == "survey_date":
            return self.date_edit.date().toString(Qt.DateFormat.ISODate)
        if key == "agency_visited":
            return self.office_input.text().strip()
        if key == "service_availed":
            return self.service_input.values()
        if key == "client_type":
            return self.client_type_group.value()
        if key == "sex":
            return self.sex_group.value()
        if key == "age":
            return self.age_spin.optional_value()
        if key == "region":
            return str(self.region_input.currentData() or "did_not_specify")
        if key == "email":
            return self.email_input.text().strip()
        raise ValueError(f"Unsupported preset field: {key}")

    def _set_preset_field_value(self, key: str, value: Any) -> None:
        if key == "mode":
            self._select_data(self.mode_combo, str(value or "onsite"))
            return
        if key == "survey_date":
            parsed = QDate.fromString(str(value or ""), Qt.DateFormat.ISODate)
            if parsed.isValid():
                self.date_edit.setDate(parsed)
            return
        if key == "agency_visited":
            self.office_input.setText(str(value or ""))
        elif key == "service_availed":
            self.service_input.set_values(value)
        elif key == "client_type":
            self.client_type_group.set_value(value)
        elif key == "sex":
            self.sex_group.set_value(value)
        elif key == "age":
            self.age_spin.set_optional_value(value)
        elif key == "region":
            self._select_data(
                self.region_input,
                normalize_region_code(value) or "did_not_specify",
            )
        elif key == "email":
            self.email_input.setText(str(value or ""))

    def _apply_field_presets(self) -> None:
        mode_settings = self._field_presets.get("mode", {})
        if mode_settings.get("prefill"):
            self._set_preset_field_value("mode", mode_settings.get("value"))
        for key, settings in self._field_presets.items():
            if key == "mode" or not settings.get("prefill"):
                continue
            self._set_preset_field_value(key, settings.get("value"))
        self._set_preset_lock_state(self._editing_id is None)
        self._refresh_control_number()

    def _set_preset_lock_state(self, for_add: bool) -> None:
        controls = {
            "mode": self.mode_combo,
            "survey_date": self.date_control,
            "agency_visited": self.office_input,
            "service_availed": self.service_input,
            "client_type": self.client_type_group,
            "sex": self.sex_group,
            "age": self.age_spin,
            "region": self.region_input,
            "email": self.email_input,
        }
        for key, control in controls.items():
            locked = for_add and bool(self._field_presets.get(key, {}).get("locked"))
            control.setEnabled(not locked)
            control.setToolTip(
                "Locked to the saved pre-fill value for new survey results." if locked else ""
            )

    def _load_record(self, record: dict[str, Any]) -> None:
        mode = str(record.get("mode") or "onsite")
        self._select_data(self.mode_combo, mode)
        self._apply_mode(mode)
        self.control_input.setText(str(record.get("control_number") or ""))
        meta = record.get("meta") if isinstance(record.get("meta"), dict) else {}
        date = QDate.fromString(
            str(record.get("survey_date") or meta.get("survey_date") or ""),
            Qt.DateFormat.ISODate,
        )
        if date.isValid():
            self.date_edit.setDate(date)
        self.office_input.setText(
            str(meta.get("agency_visited") or self._configured_school_name())
        )
        self.service_input.set_values(service_value_from_record(record))
        self.client_type_group.set_value(meta.get("client_type") or "")
        self.sex_group.set_value(meta.get("sex") or "")
        self.age_spin.set_optional_value(meta.get("age"))
        self._select_data(
            self.region_input,
            normalize_region_code(meta.get("region")) or "did_not_specify",
        )
        feedback = record.get("feedback") if isinstance(record.get("feedback"), dict) else {}
        self.email_input.setText(str(feedback.get("email") or ""))
        self.feedback_input.setPlainText(str(feedback.get("comments") or ""))
        cc = record.get("cc") if isinstance(record.get("cc"), dict) else {}
        for code, combo in self.cc_combos.items():
            self._select_data(combo, cc.get(code))
        self.cc_reason_input.setText(str(cc.get("cc3_reason") or ""))
        sqd = record.get("sqd") if isinstance(record.get("sqd"), dict) else {}
        for code in SQD_QUESTIONS[mode]:
            self.sqd_table.set_value(code, sqd.get(code))
        self._update_cc_branching()

    def _confirm_reset(self) -> None:
        self.prompt.show_prompt(
            "Clear this form?",
            "All answers currently entered in the drawer will be cleared.",
            accept_tooltip="Clear form",
            cancel_tooltip="Keep editing",
            marker="!",
            destructive=True,
            on_accept=self.reset_form,
        )

    def reset_form(self, keep_mode: str | None = None) -> None:
        mode = keep_mode or "onsite"
        self.status_banner.hide()
        self._select_data(self.mode_combo, mode)
        self.control_input.clear()
        self.date_edit.setDate(QDate.currentDate())
        self.office_input.setText(self._configured_school_name())
        self.service_input.clear()
        self.service_input.set_expanded(False)
        self.client_type_group.clear()
        self.sex_group.clear()
        self.age_spin.set_optional_value(None)
        self._select_data(self.region_input, "did_not_specify")
        self.email_input.clear()
        self.feedback_input.clear()
        self.cc_reason_input.clear()
        self._apply_mode(mode)
        for combo in self.cc_combos.values():
            combo.setCurrentIndex(0)
        self.sqd_table.clear()
        self._update_cc_branching()
        self._apply_field_presets()
        self._refresh_control_number()

    def _show_status(self, message: str, *, kind: str) -> None:
        color = theme.SUCCESS if kind == "success" else theme.DANGER
        self.status_banner.setText(message)
        self.status_banner.setStyleSheet(
            f"background: rgba(7, 28, 44, 0.98); color: {color}; border-bottom: 1px solid {color}; "
            "padding: 10px 18px; font-size: 12px; font-weight: 700;"
        )
        self.status_banner.show()

    @staticmethod
    def _drawer_stylesheet() -> str:
        return f"""
        QFrame#survey_entry_drawer {{
            background: {theme.WINDOW_BG_ALT};
            border-right: 1px solid rgba(43, 217, 197, 0.62);
        }}
        QFrame#survey_drawer_header, QFrame#survey_drawer_footer {{
            background: {theme.TITLE_BG};
            border: none;
        }}
        QFrame#survey_drawer_header {{ border-bottom: 1px solid {theme.DIVIDER}; }}
        QFrame#survey_drawer_footer {{ border-top: 1px solid {theme.DIVIDER}; }}
        QLabel#survey_drawer_title {{ color: {theme.TEXT_PRIMARY}; font-size: 19px; font-weight: 800; }}
        QLabel#survey_drawer_subtitle {{ color: {theme.TEXT_MUTED}; font-size: 12px; }}
        QWidget#survey_form_body {{ background: {theme.WINDOW_BG_ALT}; }}
        QFrame#survey_form_section {{
            background: rgba(10, 29, 48, 0.94);
            border: 1px solid {theme.CARD_BORDER};
            border-radius: 12px;
        }}
        QLabel#survey_section_title {{ color: {theme.ACCENT_TEAL}; font-size: 13px; font-weight: 800; }}
        QLabel#survey_section_subtitle, QLabel#survey_field_hint {{ color: {theme.TEXT_MUTED}; font-size: 11px; }}
        QLabel#survey_field_label {{ color: {theme.TEXT_SECONDARY}; font-size: 12px; font-weight: 700; }}
        QLabel#survey_question_label {{ color: {theme.TEXT_SECONDARY}; font-size: 12px; font-weight: 600; }}
        QFrame#survey_date_control {{ background: transparent; border: none; }}
        QLineEdit#survey_control_number {{
            color: {theme.ACCENT_CYAN};
            font-weight: 800;
        }}
        QFrame#survey_checkbox_group {{
            background: rgba(5, 19, 33, 0.72);
            border: 1px solid rgba(43, 217, 197, 0.38);
            border-radius: 8px;
        }}
        QCheckBox#survey_choice_check {{ color: {theme.TEXT_SECONDARY}; font-size: 12px; font-weight: 700; }}
        QFrame#service_checklist {{ background: transparent; border: none; }}
        QFrame#service_checklist_header {{
            background: rgba(5, 19, 33, 0.92);
            border: 1px solid rgba(43, 217, 197, 0.38);
            border-radius: 8px;
            min-height: 40px;
        }}
        QFrame#service_checklist_header:hover,
        QFrame#service_checklist_header:focus {{
            border-color: {theme.ACCENT_CYAN};
            background: rgba(8, 31, 49, 0.96);
        }}
        QFrame#service_checklist_header[expanded="true"] {{
            border-bottom-left-radius: 0;
            border-bottom-right-radius: 0;
        }}
        QLabel#service_checklist_summary {{
            color: {theme.TEXT_PRIMARY};
            font-size: 12px;
            font-weight: 700;
        }}
        QLabel#service_checklist_count {{
            color: {theme.ACCENT_CYAN};
            font-size: 11px;
            font-weight: 800;
        }}
        QFrame#service_checklist_body {{
            background: rgba(5, 19, 33, 0.84);
            border: 1px solid rgba(43, 217, 197, 0.38);
            border-top: none;
            border-bottom-left-radius: 8px;
            border-bottom-right-radius: 8px;
        }}
        QScrollArea#service_checklist_scroll,
        QWidget#service_checklist_options {{
            background: transparent;
            border: none;
        }}
        QLabel#service_checklist_hint {{ color: {theme.TEXT_MUTED}; font-size: 11px; }}
        QLabel#service_checklist_section {{
            color: {theme.ACCENT_TEAL};
            font-size: 12px;
            font-weight: 800;
            padding: 7px 3px 3px 3px;
        }}
        QFrame#service_checklist_option {{
            background: rgba(10, 35, 55, 0.66);
            border: 1px solid rgba(27, 85, 114, 0.42);
            border-radius: 7px;
        }}
        QFrame#service_checklist_option:hover {{
            background: rgba(43, 217, 197, 0.10);
            border-color: rgba(98, 219, 255, 0.56);
        }}
        QLabel#service_checklist_option_label {{
            color: {theme.TEXT_SECONDARY};
            font-size: 11px;
        }}
        QCheckBox#service_checklist_other_check {{
            color: {theme.TEXT_SECONDARY};
            font-size: 12px;
            font-weight: 700;
            padding: 7px 4px;
        }}
        QFrame#sqd_legend {{
            background: rgba(6, 28, 45, 0.92);
            border: 1px solid rgba(43, 217, 197, 0.38);
            border-radius: 9px;
        }}
        QLabel#sqd_legend_item {{ color: {theme.TEXT_SECONDARY}; font-size: 12px; font-weight: 700; }}
        QTableWidget#survey_sqd_table {{
            background: rgba(5, 19, 33, 0.92);
            alternate-background-color: rgba(10, 35, 55, 0.92);
            color: {theme.TEXT_SECONDARY};
            border: 1px solid rgba(43, 217, 197, 0.42);
            border-radius: 9px;
            gridline-color: rgba(27, 85, 114, 0.62);
            font-size: 12px;
        }}
        QTableWidget#survey_sqd_table QHeaderView::section {{
            background: {theme.PANEL_BG_ALT};
            color: {theme.TEXT_PRIMARY};
            border: none;
            border-right: 1px solid rgba(27, 85, 114, 0.62);
            border-bottom: 1px solid rgba(43, 217, 197, 0.42);
            padding: 6px 3px;
            font-weight: 800;
        }}
        QTableWidget#survey_sqd_table QTableCornerButton::section {{
            background: {theme.PANEL_BG_ALT};
            border: none;
        }}
        QFrame#sqd_tick_cell {{ background: transparent; border: none; }}
        QFrame#sqd_tick_cell:hover {{ background: rgba(98, 219, 255, 0.08); }}
        QFrame#sqd_tick_cell[selected="true"] {{ background: rgba(43, 217, 197, 0.15); }}
        QFrame#sqd_tick_cell[requiredMissing="true"] {{ background: rgba(255, 107, 120, 0.12); }}
        QCheckBox#sqd_rating_check::indicator {{ width: 20px; height: 20px; border-radius: 5px; }}
        QCheckBox#sqd_rating_check::indicator:hover {{ border-color: {theme.ACCENT_CYAN}; }}
        QCheckBox#sqd_rating_check::indicator:checked {{
            background: {theme.ACCENT_TEAL};
            border: 2px solid {theme.ACCENT_CYAN};
        }}
        QScrollArea#survey_drawer_scroll {{ background: transparent; border: none; }}
        """
