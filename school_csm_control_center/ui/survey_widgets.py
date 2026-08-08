from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QKeyEvent, QMouseEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFrame,
    QGridLayout,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from school_csm_control_center.questionnaire import RATING_LABELS, SQD_QUESTIONS
from school_csm_control_center.school_services import (
    SCHOOL_SERVICE_SECTIONS,
    SCHOOL_TRANSACTIONS,
    normalize_service_values,
    service_display,
)
from school_csm_control_center.ui.widgets import TooltipIconButton


RATING_EMOJIS = {
    1: "😠",
    2: "🙁",
    3: "😐",
    4: "🙂",
    5: "😄",
    0: "➖",
}
RATING_COLUMNS = (1, 2, 3, 4, 5, 0)


class ExclusiveCheckGroup(QFrame):
    """Optional single-choice answers rendered as ordinary tick boxes."""

    value_changed = Signal(str)

    def __init__(
        self,
        options: Iterable[str],
        *,
        other_placeholder: str = "Enter another value",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("survey_checkbox_group")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self._syncing = False
        self._canonical = tuple(str(option).strip() for option in options if str(option).strip())
        self.checks: dict[str, QCheckBox] = {}

        layout = QGridLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setHorizontalSpacing(14)
        layout.setVerticalSpacing(7)
        for index, option in enumerate(self._canonical):
            check = QCheckBox(option)
            check.setObjectName("survey_choice_check")
            check.setAccessibleName(option)
            check.setToolTip(f"Select {option}")
            check.toggled.connect(
                lambda checked, key=option: self._toggled(key, checked)
            )
            layout.addWidget(check, index // 2, index % 2)
            self.checks[option] = check

        other_row = (len(self._canonical) + 1) // 2
        self.other_check = QCheckBox("Other")
        self.other_check.setObjectName("survey_choice_check")
        self.other_check.setAccessibleName("Other")
        self.other_check.setToolTip("Enter a value not listed above")
        self.other_check.toggled.connect(
            lambda checked: self._toggled("Other", checked)
        )
        self.checks["Other"] = self.other_check
        layout.addWidget(self.other_check, other_row, 0)

        self.other_input = QLineEdit()
        self.other_input.setObjectName("survey_choice_other_input")
        self.other_input.setPlaceholderText(other_placeholder)
        self.other_input.setAccessibleName(other_placeholder)
        self.other_input.textChanged.connect(lambda _text: self._emit_value())
        self.other_input.hide()
        layout.addWidget(self.other_input, other_row + 1, 0, 1, 2)
        layout.setColumnStretch(0, 1)
        layout.setColumnStretch(1, 1)

    def value(self) -> str:
        for option in self._canonical:
            if self.checks[option].isChecked():
                return option
        if self.other_check.isChecked():
            return self.other_input.text().strip()
        return ""

    def set_value(self, value: Any) -> None:
        wanted = str(value or "").strip()
        matched = next(
            (option for option in self._canonical if option.casefold() == wanted.casefold()),
            None,
        )
        self._syncing = True
        try:
            for check in self.checks.values():
                check.setChecked(False)
            self.other_input.clear()
            if matched is not None:
                self.checks[matched].setChecked(True)
            elif wanted:
                self.other_check.setChecked(True)
                self.other_input.setText(wanted)
        finally:
            self._syncing = False
        self.other_input.setVisible(self.other_check.isChecked())
        self._emit_value()

    def clear(self) -> None:
        self.set_value("")

    def _toggled(self, key: str, checked: bool) -> None:
        if self._syncing:
            return
        self._syncing = True
        try:
            if checked:
                for other_key, other in self.checks.items():
                    if other_key != key:
                        other.setChecked(False)
            self.other_input.setVisible(self.other_check.isChecked())
            if self.other_check.isChecked():
                self.other_input.setFocus(Qt.FocusReason.MouseFocusReason)
        finally:
            self._syncing = False
        self._emit_value()

    def _emit_value(self) -> None:
        if not self._syncing:
            self.value_changed.emit(self.value())


class _ChecklistHeader(QFrame):
    activated = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("service_checklist_header")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName("Service availed school-transactions checklist")
        self.setAccessibleDescription(
            "Press Enter or Space to expand or collapse the school-transactions checklist."
        )

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self.isEnabled()
            and self.rect().contains(event.position().toPoint())
        ):
            self.activated.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt API
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.activated.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class _ServiceOptionRow(QFrame):
    def __init__(self, label: str, check: QCheckBox, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.check = check
        self.setObjectName("service_checklist_option")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(label)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 7, 8, 7)
        layout.setSpacing(9)
        layout.addWidget(check, 0, Qt.AlignmentFlag.AlignTop)
        copy = QLabel(label)
        copy.setObjectName("service_checklist_option_label")
        copy.setWordWrap(True)
        copy.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(copy, 1)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self.check.isEnabled()
            and self.rect().contains(event.position().toPoint())
        ):
            self.check.setFocus(Qt.FocusReason.MouseFocusReason)
            self.check.click()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class CollapsibleServiceChecklist(QFrame):
    """Inline, collapsible, multi-select catalog of official school services."""

    selection_changed = Signal(object)
    expanded_changed = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("service_checklist")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self._syncing = False
        self._expanded = False
        self.checks: dict[str, QCheckBox] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.header = _ChecklistHeader()
        header_layout = QHBoxLayout(self.header)
        header_layout.setContentsMargins(12, 3, 5, 3)
        header_layout.setSpacing(8)
        self.summary_label = QLabel("Not Provided")
        self.summary_label.setObjectName("service_checklist_summary")
        self.summary_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        header_layout.addWidget(self.summary_label, 1)
        self.count_label = QLabel("0 selected")
        self.count_label.setObjectName("service_checklist_count")
        self.count_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        header_layout.addWidget(self.count_label)
        self.expand_button = TooltipIconButton(
            "expand",
            "Expand school-transactions checklist",
            button_size=34,
            icon_size=16,
            role="subtle",
        )
        self.expand_button.clicked.connect(self.toggle_expanded)
        header_layout.addWidget(self.expand_button)
        self.header.activated.connect(self.toggle_expanded)
        root.addWidget(self.header)
        self.setFocusProxy(self.header)

        self.body = QFrame()
        self.body.setObjectName("service_checklist_body")
        body_layout = QVBoxLayout(self.body)
        body_layout.setContentsMargins(8, 8, 8, 8)
        body_layout.setSpacing(7)
        actions = QHBoxLayout()
        hint = QLabel("Select every school transaction covered by this response.")
        hint.setObjectName("service_checklist_hint")
        hint.setWordWrap(True)
        actions.addWidget(hint, 1)
        self.clear_button = TooltipIconButton(
            "reset",
            "Clear selected school transactions",
            button_size=32,
            icon_size=15,
            role="subtle",
        )
        self.clear_button.clicked.connect(self.clear)
        actions.addWidget(self.clear_button)
        body_layout.addLayout(actions)

        scroll = QScrollArea()
        scroll.setObjectName("service_checklist_scroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setMaximumHeight(310)
        options_body = QWidget()
        options_body.setObjectName("service_checklist_options")
        options_layout = QVBoxLayout(options_body)
        options_layout.setContentsMargins(0, 0, 0, 0)
        options_layout.setSpacing(5)
        for section, services in SCHOOL_SERVICE_SECTIONS:
            section_label = QLabel(section)
            section_label.setObjectName("service_checklist_section")
            options_layout.addWidget(section_label)
            for service in services:
                check = QCheckBox()
                check.setObjectName("service_checklist_check")
                check.setAccessibleName(service)
                check.setToolTip(service)
                check.toggled.connect(self._selection_updated)
                options_layout.addWidget(_ServiceOptionRow(service, check))
                self.checks[service] = check

        self.other_check = QCheckBox("Other school transaction")
        self.other_check.setObjectName("service_checklist_other_check")
        self.other_check.setToolTip("Enter a school transaction not listed in the official catalog")
        self.other_check.toggled.connect(self._other_toggled)
        options_layout.addWidget(self.other_check)
        self.other_input = QLineEdit()
        self.other_input.setObjectName("service_checklist_other_input")
        self.other_input.setPlaceholderText("Enter another school transaction")
        self.other_input.setToolTip("Separate multiple other transactions with semicolons")
        self.other_input.textChanged.connect(self._selection_updated)
        self.other_input.hide()
        options_layout.addWidget(self.other_input)
        options_layout.addStretch(1)
        scroll.setWidget(options_body)
        body_layout.addWidget(scroll)
        root.addWidget(self.body)
        self.body.hide()
        self._update_summary()

    @property
    def is_expanded(self) -> bool:
        return self._expanded

    def toggle_expanded(self) -> None:
        self.set_expanded(not self._expanded)

    def set_expanded(self, expanded: bool) -> None:
        self._expanded = bool(expanded)
        self.body.setVisible(self._expanded)
        self.expand_button.set_action(
            "collapse" if self._expanded else "expand",
            "Collapse school-transactions checklist"
            if self._expanded
            else "Expand school-transactions checklist",
        )
        self.header.setProperty("expanded", self._expanded)
        style = self.header.style()
        style.unpolish(self.header)
        style.polish(self.header)
        self.expanded_changed.emit(self._expanded)

    def values(self) -> list[str]:
        selected = [service for service in SCHOOL_TRANSACTIONS if self.checks[service].isChecked()]
        if self.other_check.isChecked():
            selected.extend(
                normalize_service_values(self.other_input.text().split(";"))
            )
        return normalize_service_values(selected)

    def set_values(self, value: Any) -> None:
        wanted = normalize_service_values(value)
        catalog = {service.casefold(): service for service in SCHOOL_TRANSACTIONS}
        selected_catalog: set[str] = set()
        custom: list[str] = []
        for service in wanted:
            matched = catalog.get(service.casefold())
            if matched is None:
                custom.append(service)
            else:
                selected_catalog.add(matched)
        self._syncing = True
        try:
            for service, check in self.checks.items():
                check.setChecked(service in selected_catalog)
            self.other_check.setChecked(bool(custom))
            self.other_input.setText("; ".join(custom))
            self.other_input.setVisible(bool(custom))
        finally:
            self._syncing = False
        self._update_summary()
        self.selection_changed.emit(self.values())

    def text(self) -> str:
        return service_display(self.values(), fallback="")

    def setText(self, value: str) -> None:  # noqa: N802 - QLineEdit compatibility
        self.set_values(value)

    def clear(self) -> None:
        self.set_values([])

    def _other_toggled(self, checked: bool) -> None:
        self.other_input.setVisible(bool(checked))
        if checked and not self._syncing:
            self.other_input.setFocus(Qt.FocusReason.MouseFocusReason)
        if not checked and not self._syncing:
            self.other_input.clear()
        self._selection_updated()

    def _selection_updated(self, *_args) -> None:
        if self._syncing:
            return
        self._update_summary()
        self.selection_changed.emit(self.values())

    def _update_summary(self) -> None:
        services = self.values()
        if not services:
            summary = "Not Provided"
        elif len(services) == 1:
            summary = services[0]
        else:
            summary = f"{len(services)} school transactions selected"
        self.summary_label.setText(summary)
        self.summary_label.setToolTip(service_display(services, fallback="No transaction selected"))
        self.count_label.setText(f"{len(services)} selected")


def build_sqd_legend(parent: QWidget | None = None) -> QFrame:
    legend = QFrame(parent)
    legend.setObjectName("sqd_legend")
    layout = QGridLayout(legend)
    layout.setContentsMargins(12, 9, 12, 9)
    layout.setHorizontalSpacing(12)
    layout.setVerticalSpacing(6)
    emoji_font = QFont("Segoe UI Emoji", 10)
    for index, score in enumerate(RATING_COLUMNS):
        label = RATING_LABELS.get(score, "Not Applicable")
        item = QLabel(f"{RATING_EMOJIS[score]}  {label}")
        item.setObjectName("sqd_legend_item")
        item.setProperty("score", score)
        item.setFont(emoji_font)
        item.setToolTip(f"{label} ({'N/A' if score == 0 else score})")
        layout.addWidget(item, index // 3, index % 3)
    for column in range(3):
        layout.setColumnStretch(column, 1)
    return legend


class SQDRatingCell(QFrame):
    """Full-cell pointer target that keeps its checkbox as the accessible control."""

    activated = Signal()

    def __init__(self, check: QCheckBox, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.check = check
        self.setObjectName("sqd_tick_cell")
        self.setAccessibleName(check.accessibleName())
        self.setToolTip(check.toolTip())
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setProperty("selected", check.isChecked())
        self.setProperty("requiredMissing", False)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(check, 0, Qt.AlignmentFlag.AlignCenter)
        self.activated.connect(check.click)
        check.toggled.connect(self._sync_selected)

    def _sync_selected(self, checked: bool) -> None:
        self.setProperty("selected", bool(checked))
        style = self.style()
        style.unpolish(self)
        style.polish(self)
        self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton and self.check.isEnabled():
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self.check.isEnabled()
            and self.rect().contains(event.position().toPoint())
        ):
            self.check.setFocus(Qt.FocusReason.MouseFocusReason)
            self.activated.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class SQDRatingTable(QTableWidget):
    """One checkbox matrix for all Service Quality Dimension answers."""

    ROW_HEIGHT = 94

    def __init__(self, parent: QWidget | None = None) -> None:
        codes = tuple(f"sqd{number}" for number in range(9))
        super().__init__(len(codes), 1 + len(RATING_COLUMNS), parent)
        self.setObjectName("survey_sqd_table")
        self.setAccessibleName("Service Quality Dimensions response table")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAlternatingRowColors(True)
        self.setWordWrap(True)
        self.setShowGrid(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.verticalHeader().hide()
        self.horizontalHeader().setSectionsClickable(False)
        self.horizontalHeader().setHighlightSections(False)
        self.horizontalHeader().setMinimumHeight(42)
        self.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)

        question_header = QTableWidgetItem("Question / Dimension")
        question_header.setToolTip("Service Quality Dimension statement")
        self.setHorizontalHeaderItem(0, question_header)
        emoji_font = QFont("Segoe UI Emoji", 14)
        for column, score in enumerate(RATING_COLUMNS, start=1):
            label = RATING_LABELS.get(score, "Not Applicable")
            item = QTableWidgetItem(RATING_EMOJIS[score])
            item.setFont(emoji_font)
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item.setToolTip(f"{label} ({'N/A' if score == 0 else score})")
            self.setHorizontalHeaderItem(column, item)
            self.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            self.setColumnWidth(column, 44)

        self._codes = codes
        self._all_checks: dict[str, dict[int, QCheckBox]] = {}
        self._question_items: dict[str, QTableWidgetItem] = {}
        self._cells: dict[str, dict[int, SQDRatingCell]] = {}
        self._required_missing: set[str] = set()
        self._syncing = False
        self._mode = "onsite"
        for row, code in enumerate(codes):
            question = QTableWidgetItem()
            question.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.setItem(row, 0, question)
            self._question_items[code] = question
            row_checks: dict[int, QCheckBox] = {}
            row_cells: dict[int, SQDRatingCell] = {}
            for column, score in enumerate(RATING_COLUMNS, start=1):
                check = QCheckBox()
                check.setObjectName("sqd_rating_check")
                label = RATING_LABELS.get(score, "Not Applicable")
                check.setAccessibleName(f"{code.upper()} {label}")
                check.setToolTip(
                    f"{code.upper()}: {label} ({'N/A' if score == 0 else score})"
                )
                check.toggled.connect(
                    lambda checked, row_code=code, row_score=score: self._rating_toggled(
                        row_code, row_score, checked
                    )
                )
                cell = SQDRatingCell(check)
                self.setCellWidget(row, column, cell)
                row_checks[score] = check
                row_cells[score] = cell
            self._all_checks[code] = row_checks
            self._cells[code] = row_cells
            self.setRowHeight(row, self.ROW_HEIGHT)
        self.set_mode("onsite")

    def set_mode(self, mode: str) -> None:
        selected_mode = "online" if str(mode).casefold() == "online" else "onsite"
        self._mode = selected_mode
        self.set_required_missing(())
        visible_questions = SQD_QUESTIONS[selected_mode]
        online = selected_mode == "online"
        self.setColumnHidden(1 + RATING_COLUMNS.index(0), online)
        for row, code in enumerate(self._codes):
            visible = code in visible_questions
            self.setRowHidden(row, not visible)
            if not visible:
                self.set_value(code, None)
                continue
            spec = visible_questions[code]
            self._question_items[code].setText(
                f"{spec['code']} — {spec['dimension']}\n{spec['prompt']}"
            )
            self._question_items[code].setToolTip(str(spec["prompt"]))
            if online and self.value(code) == 0:
                self.set_value(code, None)
        self._update_height()

    def visible_checks(self) -> dict[str, dict[int, QCheckBox]]:
        allowed_scores = (1, 2, 3, 4, 5) if self._mode == "online" else RATING_COLUMNS
        return {
            code: {score: self._all_checks[code][score] for score in allowed_scores}
            for code in SQD_QUESTIONS[self._mode]
        }

    def value(self, code: str) -> int | None:
        for score, check in self._all_checks[str(code)].items():
            if check.isChecked():
                return score
        return None

    def set_value(self, code: str, value: Any) -> None:
        normalized = value if value in RATING_COLUMNS else None
        if self._mode == "online" and normalized == 0:
            normalized = None
        self._syncing = True
        try:
            for score, check in self._all_checks[str(code)].items():
                check.setChecked(score == normalized)
        finally:
            self._syncing = False

    def clear(self) -> None:
        for code in self._codes:
            self.set_value(code, None)
        self.set_required_missing(())

    def set_required_missing(self, codes: Iterable[str]) -> None:
        """Highlight rows that still require an answer without opening a dialog."""

        self._required_missing = {
            str(code).casefold() for code in codes if str(code).casefold() in self._codes
        }
        missing_background = QBrush(QColor(78, 24, 38, 210))
        missing_foreground = QBrush(QColor("#FF8390"))
        clear_brush = QBrush()
        for code in self._codes:
            missing = code in self._required_missing
            question = self._question_items[code]
            question.setBackground(missing_background if missing else clear_brush)
            question.setForeground(missing_foreground if missing else clear_brush)
            for cell in self._cells[code].values():
                cell.setProperty("requiredMissing", missing)
                style = cell.style()
                style.unpolish(cell)
                style.polish(cell)
                cell.update()

    def _rating_toggled(self, code: str, score: int, checked: bool) -> None:
        if self._syncing or not checked:
            return
        self._syncing = True
        try:
            for other_score, other in self._all_checks[code].items():
                if other_score != score:
                    other.setChecked(False)
        finally:
            self._syncing = False
        if code in self._required_missing:
            self.set_required_missing(self._required_missing - {code})

    def _update_height(self) -> None:
        visible_rows = sum(
            not self.isRowHidden(row) for row in range(self.rowCount())
        )
        header_height = max(42, self.horizontalHeader().height())
        self.setFixedHeight(header_height + (visible_rows * self.ROW_HEIGHT) + 4)
