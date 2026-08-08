from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from school_csm_control_center.ui import theme
from school_csm_control_center.ui.overlays import ClickScrim
from school_csm_control_center.ui.widgets import TooltipIconButton
from school_csm_control_center.storage.field_preset_store import FieldPresetStoreError


PRESET_FIELD_LABELS = {
    "mode": ("Questionnaire", "Onsite or online questionnaire mode"),
    "survey_date": ("Survey date", "Fixed date restored for each new entry"),
    "agency_visited": ("Agency / office", "Agency or office receiving the transaction"),
    "service_availed": (
        "Service availed",
        "One or more school transactions selected from the checklist",
    ),
    "client_type": ("Client type", "Citizen, Business, Government, or custom online value"),
    "sex": ("Sex", "Printed choice or custom online value"),
    "age": ("Age", "Optional client age"),
    "region": ("Region", "Frequently repeated region of residence"),
    "email": ("Email address", "Optional onsite email value"),
}


class FieldPresetOverlay(QWidget):
    """Configures pre-fill and lock behavior without creating a dialog window."""

    applied = Signal(dict)

    def __init__(self, owner, parent: QWidget) -> None:
        super().__init__(parent)
        self.owner = owner
        self.setObjectName("field_preset_overlay")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet("QWidget#field_preset_overlay { background: transparent; }")
        self.hide()
        self._rows: dict[str, dict[str, Any]] = {}

        self.scrim = ClickScrim(self)
        self.scrim.setObjectName("field_preset_scrim")
        self.scrim.clicked.connect(self.close_overlay)

        self.panel = QFrame(self)
        self.panel.setObjectName("field_preset_panel")
        panel_layout = QVBoxLayout(self.panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.setSpacing(0)

        header = QFrame()
        header.setObjectName("field_preset_header")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(22, 18, 16, 18)
        header_layout.setSpacing(12)
        heading = QVBoxLayout()
        heading.setSpacing(4)
        title = QLabel("Pre-fill and lock fields")
        title.setObjectName("field_preset_title")
        heading.addWidget(title)
        subtitle = QLabel(
            "The current form value becomes the pre-fill. Locked fields keep that value and cannot be changed on new entries."
        )
        subtitle.setObjectName("field_preset_subtitle")
        subtitle.setWordWrap(True)
        heading.addWidget(subtitle)
        header_layout.addLayout(heading, 1)
        self.close_button = TooltipIconButton("close", "Close field preset settings")
        self.close_button.clicked.connect(self.close_overlay)
        header_layout.addWidget(self.close_button)
        panel_layout.addWidget(header)

        self.status = QLabel()
        self.status.setObjectName("field_preset_status")
        self.status.setWordWrap(True)
        self.status.hide()
        panel_layout.addWidget(self.status)

        scroll = QScrollArea()
        scroll.setObjectName("field_preset_scroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName("field_preset_body")
        self.rows_layout = QVBoxLayout(body)
        self.rows_layout.setContentsMargins(18, 16, 18, 18)
        self.rows_layout.setSpacing(9)
        for key, (label, description) in PRESET_FIELD_LABELS.items():
            row = self._make_row(key, label, description)
            self.rows_layout.addWidget(row)
        self.rows_layout.addStretch(1)
        scroll.setWidget(body)
        panel_layout.addWidget(scroll, 1)

        footer = QFrame()
        footer.setObjectName("field_preset_footer")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(18, 13, 18, 13)
        footer_layout.setSpacing(10)
        self.clear_button = TooltipIconButton("reset", "Clear all pre-fill and lock selections", role="subtle")
        self.clear_button.clicked.connect(self._clear_selections)
        footer_layout.addWidget(self.clear_button)
        footer_layout.addStretch(1)
        self.cancel_button = TooltipIconButton("cancel", "Cancel field preset changes", role="subtle")
        self.cancel_button.clicked.connect(self.close_overlay)
        footer_layout.addWidget(self.cancel_button)
        self.apply_button = TooltipIconButton(
            "check", "Apply field pre-fill and lock settings", icon_color=theme.SUCCESS, role="success", button_size=44
        )
        self.apply_button.clicked.connect(self._apply)
        footer_layout.addWidget(self.apply_button)
        panel_layout.addWidget(footer)

        self.escape_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        self.escape_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.escape_shortcut.activated.connect(self.close_overlay)
        self._apply_styles()

    def _make_row(self, key: str, label: str, description: str) -> QFrame:
        row = QFrame()
        row.setObjectName("field_preset_row")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(13, 10, 13, 10)
        layout.setSpacing(12)
        copy = QVBoxLayout()
        copy.setSpacing(3)
        label_widget = QLabel(label)
        label_widget.setObjectName("field_preset_field_name")
        copy.addWidget(label_widget)
        description_widget = QLabel(description)
        description_widget.setObjectName("field_preset_field_description")
        description_widget.setWordWrap(True)
        copy.addWidget(description_widget)
        preview = QLabel()
        preview.setObjectName("field_preset_preview")
        preview.setWordWrap(True)
        preview.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        copy.addWidget(preview)
        layout.addLayout(copy, 1)
        prefill = QCheckBox("Pre-fill")
        prefill.setObjectName("field_prefill_checkbox")
        prefill.setToolTip(f"Use the current {label} value for each new survey")
        layout.addWidget(prefill)
        locked = QCheckBox("Lock")
        locked.setObjectName("field_lock_checkbox")
        locked.setToolTip(f"Prevent changes to the pre-filled {label} value")
        locked.toggled.connect(lambda checked, target=prefill: self._lock_toggled(target, checked))
        layout.addWidget(locked)
        self._rows[key] = {"preview": preview, "prefill": prefill, "locked": locked}
        return row

    @staticmethod
    def _lock_toggled(prefill: QCheckBox, checked: bool) -> None:
        if checked:
            prefill.setChecked(True)
        prefill.setEnabled(not checked)

    def open_overlay(self) -> None:
        self.owner.escape_shortcut.setEnabled(False)
        self.status.hide()
        existing = self.owner.field_presets()
        for key, row in self._rows.items():
            value, preview = self.owner.preset_field_snapshot(key)
            row["preview"].setText(f"Current value: {preview}")
            settings = existing.get(key, {}) if isinstance(existing, dict) else {}
            locked = bool(settings.get("locked"))
            prefill = bool(settings.get("prefill")) or locked
            row["locked"].blockSignals(True)
            row["prefill"].setEnabled(True)
            row["prefill"].setChecked(prefill)
            row["locked"].setChecked(locked)
            row["prefill"].setEnabled(not locked)
            row["locked"].blockSignals(False)
        self.setGeometry(self.parentWidget().rect())
        self._position_children()
        self.show()
        self.raise_()
        self.apply_button.setFocus(Qt.FocusReason.ActiveWindowFocusReason)

    def close_overlay(self) -> None:
        self.hide()
        self.owner.escape_shortcut.setEnabled(True)

    def _clear_selections(self) -> None:
        for row in self._rows.values():
            row["locked"].setChecked(False)
            row["prefill"].setEnabled(True)
            row["prefill"].setChecked(False)
        self.status.hide()

    def _apply(self) -> None:
        fields: dict[str, dict[str, Any]] = {}
        for key, row in self._rows.items():
            locked = row["locked"].isChecked()
            prefill = row["prefill"].isChecked() or locked
            if not prefill:
                continue
            value, _ = self.owner.preset_field_snapshot(key)
            fields[key] = {"value": value, "prefill": True, "locked": locked}
        try:
            saved = self.owner.save_field_presets(fields)
        except (OSError, ValueError, FieldPresetStoreError) as exc:
            self.status.setText(str(exc))
            self.status.show()
            return
        self.applied.emit(saved)
        self.close_overlay()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self._position_children()

    def _position_children(self) -> None:
        self.scrim.setGeometry(self.rect())
        width = min(780, max(430, self.width() - 48))
        height = min(720, max(430, self.height() - 48))
        self.panel.setGeometry((self.width() - width) // 2, (self.height() - height) // 2, width, height)

    def _apply_styles(self) -> None:
        self.setStyleSheet(
            f"""
            QFrame#field_preset_scrim {{ background: rgba(1, 8, 18, 0.80); border: none; }}
            QFrame#field_preset_panel {{ background: {theme.WINDOW_BG_ALT}; border: 1px solid rgba(43,217,197,0.68); border-radius: 16px; }}
            QFrame#field_preset_header, QFrame#field_preset_footer {{ background: {theme.TITLE_BG}; border: none; }}
            QFrame#field_preset_header {{ border-bottom: 1px solid {theme.DIVIDER}; border-top-left-radius: 16px; border-top-right-radius: 16px; }}
            QFrame#field_preset_footer {{ border-top: 1px solid {theme.DIVIDER}; border-bottom-left-radius: 16px; border-bottom-right-radius: 16px; }}
            QLabel#field_preset_title {{ color: {theme.TEXT_PRIMARY}; font-size: 18px; font-weight: 800; }}
            QLabel#field_preset_subtitle {{ color: {theme.TEXT_MUTED}; font-size: 10px; }}
            QLabel#field_preset_status {{ color: {theme.DANGER}; background: rgba(83,30,43,0.72); border-bottom: 1px solid {theme.DANGER}; padding: 9px 18px; font-size: 10px; font-weight: 700; }}
            QWidget#field_preset_body {{ background: {theme.WINDOW_BG_ALT}; }}
            QFrame#field_preset_row {{ background: rgba(10,29,48,0.90); border: 1px solid rgba(27,85,114,0.55); border-radius: 9px; }}
            QLabel#field_preset_field_name {{ color: {theme.TEXT_PRIMARY}; font-size: 11px; font-weight: 800; }}
            QLabel#field_preset_field_description {{ color: {theme.TEXT_MUTED}; font-size: 9px; }}
            QLabel#field_preset_preview {{ color: {theme.ACCENT_CYAN}; font-size: 9px; font-weight: 700; }}
            QCheckBox#field_prefill_checkbox, QCheckBox#field_lock_checkbox {{ color: {theme.TEXT_SECONDARY}; font-size: 10px; font-weight: 700; }}
            QScrollArea#field_preset_scroll {{ background: transparent; border: none; }}
            """
        )
