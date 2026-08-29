"""In-window Internet Gateway setup, transfer, and diagnostic surfaces."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QRadioButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from school_csm_control_center.ui import theme
from school_csm_control_center.ui.overlays import ClickScrim
from school_csm_control_center.ui.widgets import TooltipIconButton


class _GatewayOverlay(QWidget):
    closed = Signal()

    def __init__(
        self,
        parent: QWidget,
        *,
        title: str,
        subtitle: str,
        panel_width: int = 820,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("gateway_overlay")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._operation_locked = False
        self._return_focus: QWidget | None = None
        self._panel_width = max(540, int(panel_width))
        self.hide()

        self.scrim = ClickScrim(self)
        self.scrim.setObjectName("gateway_overlay_scrim")
        self.scrim.clicked.connect(self.close_overlay)
        self.panel = QFrame(self)
        self.panel.setObjectName("gateway_overlay_panel")
        panel_layout = QVBoxLayout(self.panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.setSpacing(0)

        header = QFrame()
        header.setObjectName("gateway_overlay_header")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(22, 15, 14, 15)
        copy = QVBoxLayout()
        copy.setSpacing(3)
        heading = QLabel(title)
        heading.setObjectName("gateway_overlay_title")
        helper = QLabel(subtitle)
        helper.setObjectName("gateway_overlay_subtitle")
        helper.setWordWrap(True)
        copy.addWidget(heading)
        copy.addWidget(helper)
        header_layout.addLayout(copy, 1)
        self.close_button = TooltipIconButton(
            "close", f"Close {title}", icon_color=theme.TEXT_SECONDARY, role="subtle"
        )
        self.close_button.clicked.connect(self.close_overlay)
        header_layout.addWidget(self.close_button, 0, Qt.AlignmentFlag.AlignTop)
        panel_layout.addWidget(header)

        self.body = QWidget()
        self.body.setObjectName("gateway_overlay_body")
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(22, 18, 22, 22)
        self.body_layout.setSpacing(14)
        panel_layout.addWidget(self.body, 1)
        self._apply_styles()

    @property
    def operation_locked(self) -> bool:
        return self._operation_locked

    def set_operation_locked(self, locked: bool) -> None:
        self._operation_locked = bool(locked)
        self.close_button.setEnabled(not self._operation_locked)

    def open_overlay(self, return_focus: QWidget | None = None) -> None:
        self._return_focus = return_focus
        self.setGeometry(self.parentWidget().rect())
        self._position_children()
        self.show()
        self.raise_()
        self.setFocus(Qt.FocusReason.ActiveWindowFocusReason)

    def close_overlay(self) -> None:
        if not self.isVisible() or self._operation_locked:
            return
        return_focus = self._return_focus
        self._return_focus = None
        self.hide()
        self.closed.emit()
        if return_focus is not None:
            return_focus.setFocus(Qt.FocusReason.ShortcutFocusReason)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._position_children()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            if not self._operation_locked:
                self.close_overlay()
            event.accept()
            return
        super().keyPressEvent(event)

    def _position_children(self) -> None:
        self.scrim.setGeometry(self.rect())
        margin = 14
        width = min(self._panel_width, max(520, self.width() - (margin * 2)))
        height = min(max(500, self.panel.sizeHint().height()), max(500, self.height() - 36))
        left = max(margin, (self.width() - width) // 2)
        top = max(18, (self.height() - height) // 2)
        self.panel.setGeometry(left, top, width, height)

    def _apply_styles(self) -> None:
        self.setStyleSheet(
            f"""
            QWidget#gateway_overlay {{ background: transparent; }}
            QFrame#gateway_overlay_scrim {{ background: rgba(1, 8, 18, 0.82); border: none; }}
            QFrame#gateway_overlay_panel {{
                background: {theme.WINDOW_BG};
                border: 1px solid rgba(50, 217, 204, 0.68);
                border-radius: 18px;
            }}
            QFrame#gateway_overlay_header {{
                background: {theme.PANEL_BG}; border: none;
                border-bottom: 1px solid {theme.DIVIDER};
                border-top-left-radius: 18px; border-top-right-radius: 18px;
            }}
            QWidget#gateway_overlay_body {{ background: {theme.WINDOW_BG}; }}
            QLabel#gateway_overlay_title {{
                color: {theme.TEXT_PRIMARY}; background: transparent;
                font-size: 19px; font-weight: 850;
            }}
            QLabel#gateway_overlay_subtitle {{
                color: {theme.TEXT_SECONDARY}; background: transparent; font-size: 12px;
            }}
            QLabel#gateway_overlay_section {{
                color: {theme.TEXT_PRIMARY}; background: transparent;
                font-size: 14px; font-weight: 800;
            }}
            QLabel#gateway_overlay_note {{
                color: {theme.TEXT_SECONDARY}; background: rgba(4, 22, 38, 0.72);
                border: 1px solid rgba(87, 214, 220, 0.22);
                border-radius: 10px; padding: 11px; font-size: 12px;
            }}
            QLabel#gateway_overlay_warning {{
                color: {theme.WARNING}; background: rgba(93, 58, 7, 0.28);
                border: 1px solid rgba(245, 158, 11, 0.42);
                border-radius: 10px; padding: 11px; font-size: 12px;
            }}
            QLabel#gateway_overlay_field {{
                color: {theme.TEXT_MUTED}; background: transparent;
                font-size: 11px; font-weight: 700;
            }}
            QLabel#gateway_diagnostic_value {{
                color: {theme.TEXT_PRIMARY}; background: transparent;
                font-size: 12px; font-weight: 650;
            }}
            QFrame#gateway_diagnostic_row {{
                background: rgba(4, 22, 38, 0.56);
                border: 1px solid rgba(112, 139, 164, 0.18);
                border-radius: 8px;
            }}
            """
        )


class InternetGatewaySetupOverlay(_GatewayOverlay):
    browser_authorization_requested = Signal(str)
    completion_code_requested = Signal(str)
    open_school_information_requested = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(
            parent,
            title="Set Up Internet Gateway",
            subtitle="Optional secure Internet access for this school's existing Survey and Scanner service.",
        )
        self._school_id = ""
        self._school_name = ""
        self._provider_available = False

        prerequisite = QLabel(
            "Local-Only operation remains available whether or not Internet Gateway setup is completed. No CSM response database is created in the Registration Service."
        )
        prerequisite.setObjectName("gateway_overlay_note")
        prerequisite.setWordWrap(True)
        self.body_layout.addWidget(prerequisite)

        identity_heading = QLabel("Official school identity")
        identity_heading.setObjectName("gateway_overlay_section")
        self.body_layout.addWidget(identity_heading)
        identity = QGridLayout()
        identity.setHorizontalSpacing(12)
        identity.setVerticalSpacing(7)
        identity.addWidget(self._field_label("School name"), 0, 0)
        identity.addWidget(self._field_label("Official School ID"), 0, 1)
        self.school_name = self._readonly()
        self.school_id = self._readonly()
        identity.addWidget(self.school_name, 1, 0)
        identity.addWidget(self.school_id, 1, 1)
        self.body_layout.addLayout(identity)

        self.school_id_requirement = QLabel()
        self.school_id_requirement.setObjectName("gateway_overlay_warning")
        self.school_id_requirement.setWordWrap(True)
        self.body_layout.addWidget(self.school_id_requirement)

        browser_heading = QLabel("Authorize in your secure browser")
        browser_heading.setObjectName("gateway_overlay_section")
        self.body_layout.addWidget(browser_heading)
        browser_copy = QLabel(
            "The managed registration page opens in your browser. Enter the School Activation Code and create the administrator passkey there. The Control Center never collects, stores, or sees either value."
        )
        browser_copy.setObjectName("gateway_overlay_note")
        browser_copy.setWordWrap(True)
        self.body_layout.addWidget(browser_copy)

        actions = QHBoxLayout()
        actions.addStretch(1)
        self.school_information_button = TooltipIconButton(
            "school", "Open School Information", role="subtle"
        )
        self.register_button = TooltipIconButton(
            "open", "Open the secure Internet Gateway authorization page", role="primary"
        )
        actions.addWidget(self.school_information_button)
        actions.addWidget(self.register_button)
        self.body_layout.addLayout(actions)

        completion_heading = QLabel("Finish in the Control Center")
        completion_heading.setObjectName("gateway_overlay_section")
        self.body_layout.addWidget(completion_heading)
        completion_copy = QLabel(
            "After the browser confirms authorization, copy its completion code here. It expires in 10 minutes and authorizes only this installation. If secure delivery is interrupted, enter the same unexpired code again."
        )
        completion_copy.setObjectName("gateway_overlay_note")
        completion_copy.setWordWrap(True)
        self.body_layout.addWidget(completion_copy)
        completion_row = QHBoxLayout()
        self.completion_code = QLineEdit()
        self.completion_code.setPlaceholderText("One-time completion code")
        self.completion_code.setAccessibleName("Internet Gateway completion code")
        self.redeem_button = TooltipIconButton(
            "check", "Redeem the one-time completion code", role="success"
        )
        completion_row.addWidget(self.completion_code, 1)
        completion_row.addWidget(self.redeem_button)
        self.body_layout.addLayout(completion_row)
        self.status_label = QLabel()
        self.status_label.setObjectName("gateway_overlay_note")
        self.status_label.setWordWrap(True)
        self.status_label.hide()
        self.body_layout.addWidget(self.status_label)
        self.body_layout.addStretch(1)

        self.school_information_button.clicked.connect(
            self.open_school_information_requested.emit
        )
        self.register_button.clicked.connect(self._submit)
        self.redeem_button.clicked.connect(self._redeem)

    def configure(
        self,
        *,
        school_id: str,
        school_name: str,
        provider_available: bool,
        detail: str = "",
    ) -> None:
        self._school_id = str(school_id or "").strip()
        self._school_name = " ".join(str(school_name or "").split())
        self._provider_available = bool(provider_available)
        self.school_id.setText(self._school_id)
        self.school_name.setText(self._school_name)
        missing = not self._school_id
        self.school_id_requirement.setVisible(missing or not self._provider_available)
        if missing:
            requirement = (
                "Internet Gateway setup requires the official School ID. Complete School Information first."
            )
        elif not self._provider_available:
            requirement = (
                detail
                or "Managed Internet Gateway provider configuration is unavailable. Local-Only operation continues."
            )
        else:
            requirement = ""
        self.school_id_requirement.setText(requirement)
        self.register_button.setEnabled(not missing and self._provider_available)
        self.completion_code.setEnabled(not missing and self._provider_available)
        self.redeem_button.setEnabled(not missing and self._provider_available)
        self.completion_code.clear()
        self.status_label.hide()

    def clear_sensitive_fields(self) -> None:
        self.completion_code.clear()

    def set_status(self, message: str, *, busy: bool = False) -> None:
        self.status_label.setText(str(message or ""))
        self.status_label.setVisible(bool(message))
        self.set_operation_locked(busy)
        self.register_button.setEnabled(
            not busy and bool(self._school_id) and self._provider_available
        )
        self.completion_code.setEnabled(
            not busy and bool(self._school_id) and self._provider_available
        )
        self.redeem_button.setEnabled(
            not busy and bool(self._school_id) and self._provider_available
        )

    def close_overlay(self) -> None:
        if not self.operation_locked:
            self.clear_sensitive_fields()
        super().close_overlay()

    def _submit(self) -> None:
        self.browser_authorization_requested.emit("registration")

    def _redeem(self) -> None:
        code = self.completion_code.text().strip()
        if not code:
            self.set_status("Enter the one-time completion code shown in the browser.")
            self.completion_code.setFocus()
            return
        self.completion_code_requested.emit(code)
        self.completion_code.clear()

    @staticmethod
    def _field_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("gateway_overlay_field")
        return label

    @staticmethod
    def _readonly() -> QLineEdit:
        line = QLineEdit()
        line.setReadOnly(True)
        return line


class MigrationPackageExportOverlay(_GatewayOverlay):
    package_requested = Signal(object)

    def __init__(self, parent: QWidget) -> None:
        super().__init__(
            parent,
            title="Prepare Server and Data Package",
            subtitle="Create a destination-bound encrypted copy for a replacement Control Center. The local Survey Server is stopped first for a consistent snapshot.",
            panel_width=760,
        )
        self._school_id = ""
        self._source_installation_id = ""

        self.source_identity = QLabel()
        self.source_identity.setObjectName("gateway_overlay_note")
        self.source_identity.setWordWrap(True)
        self.body_layout.addWidget(self.source_identity)

        destination_label = QLabel("Replacement installation ID")
        destination_label.setObjectName("gateway_overlay_field")
        self.body_layout.addWidget(destination_label)
        self.destination_installation_id = QLineEdit()
        self.destination_installation_id.setPlaceholderText(
            "Paste the installation ID shown on the replacement computer"
        )
        self.destination_installation_id.setAccessibleName(
            "Replacement application installation ID"
        )
        self.body_layout.addWidget(self.destination_installation_id)

        package_label = QLabel("Encrypted package destination")
        package_label.setObjectName("gateway_overlay_field")
        self.body_layout.addWidget(package_label)
        destination_row = QHBoxLayout()
        self.destination_path = QLineEdit()
        self.destination_path.setReadOnly(True)
        self.destination_path.setPlaceholderText("Choose a .mossmig file")
        self.choose_destination = TooltipIconButton(
            "save", "Choose where to save the encrypted migration package", role="subtle"
        )
        destination_row.addWidget(self.destination_path, 1)
        destination_row.addWidget(self.choose_destination)
        self.body_layout.addLayout(destination_row)

        guidance = QLabel(
            "After creation, a new 256-bit migration key is shown once. Send the .mossmig package and its key to the replacement operator through separate trusted channels. The Control Center does not save the key."
        )
        guidance.setObjectName("gateway_overlay_warning")
        guidance.setWordWrap(True)
        self.body_layout.addWidget(guidance)

        action_row = QHBoxLayout()
        action_row.addStretch(1)
        self.prepare_button = TooltipIconButton(
            "export", "Create the encrypted Server and Data package", role="success"
        )
        action_row.addWidget(self.prepare_button)
        self.body_layout.addLayout(action_row)

        self.result_label = QLabel()
        self.result_label.setObjectName("gateway_overlay_note")
        self.result_label.setWordWrap(True)
        self.result_label.hide()
        self.body_layout.addWidget(self.result_label)
        key_row = QHBoxLayout()
        self.migration_key = QLineEdit()
        self.migration_key.setReadOnly(True)
        self.migration_key.setPlaceholderText("Migration key appears here once")
        self.migration_key.setAccessibleName("One-time migration package key")
        self.copy_key = TooltipIconButton(
            "copy", "Copy the one-time migration key", role="primary"
        )
        key_row.addWidget(self.migration_key, 1)
        key_row.addWidget(self.copy_key)
        self.body_layout.addLayout(key_row)
        self.body_layout.addStretch(1)

        self.choose_destination.clicked.connect(self._choose_destination)
        self.prepare_button.clicked.connect(self._submit)
        self.copy_key.clicked.connect(self._copy_key)
        self.copy_key.setEnabled(False)

    def configure(
        self,
        *,
        school_id: str,
        school_name: str,
        source_installation_id: str,
    ) -> None:
        self._school_id = str(school_id or "").strip()
        self._source_installation_id = str(source_installation_id or "").strip()
        self.source_identity.setText(
            f"Active source: {' '.join(str(school_name or 'School').split())}\n"
            f"School ID: {self._school_id or 'Not available'}\n"
            f"Source installation ID: {self._source_installation_id or 'Not available'}"
        )
        self.destination_installation_id.clear()
        self.destination_path.clear()
        self.migration_key.clear()
        self.result_label.hide()
        self.copy_key.setEnabled(False)
        self.set_progress("", busy=False)

    def set_progress(self, message: str, *, busy: bool) -> None:
        self.set_operation_locked(busy)
        self.result_label.setText(str(message or ""))
        self.result_label.setVisible(bool(message))
        enabled = not busy
        self.destination_installation_id.setEnabled(enabled)
        self.destination_path.setEnabled(enabled)
        self.choose_destination.setEnabled(enabled)
        self.prepare_button.setEnabled(enabled and bool(self._school_id))
        self.copy_key.setEnabled(enabled and bool(self.migration_key.text()))

    def show_result(self, result: Mapping[str, Any]) -> None:
        key = str(result.get("migration_key") or "").strip()
        package_path = str(result.get("package_path") or "").strip()
        self.destination_path.setText(package_path)
        self.migration_key.setText(key)
        counts = result.get("record_counts")
        count_text = ", ".join(
            f"{name}: {count}" for name, count in sorted(dict(counts or {}).items())
        )
        self.set_progress(
            "Encrypted package created. Copy the key now; it disappears when this window closes."
            + (f" Included records — {count_text}." if count_text else ""),
            busy=False,
        )

    def close_overlay(self) -> None:
        if not self.operation_locked:
            self.migration_key.clear()
            self.copy_key.setEnabled(False)
        super().close_overlay()

    def _choose_destination(self) -> None:
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Save encrypted Server and Data package",
            str(Path.home() / f"school-csm-{self._school_id or 'transfer'}.mossmig"),
            "School CSM migration package (*.mossmig)",
        )
        if filename:
            if not filename.casefold().endswith(".mossmig"):
                filename += ".mossmig"
            self.destination_path.setText(filename)

    def _submit(self) -> None:
        destination_id = self.destination_installation_id.text().strip()
        destination_path = self.destination_path.text().strip()
        if not destination_id:
            self.set_progress(
                "Enter the replacement computer's installation ID.", busy=False
            )
            self.destination_installation_id.setFocus()
            return
        if not destination_path:
            self.set_progress("Choose where to save the .mossmig package.", busy=False)
            return
        self.migration_key.clear()
        self.copy_key.setEnabled(False)
        self.package_requested.emit(
            {
                "destination_installation_id": destination_id,
                "destination_path": destination_path,
            }
        )

    def _copy_key(self) -> None:
        key = self.migration_key.text().strip()
        if not key:
            return
        QApplication.clipboard().setText(key)
        self.result_label.setText(
            "Migration key copied. Keep it separate from the .mossmig package and close this window after recording it securely."
        )
        self.result_label.show()


class RecoveryBackupExportOverlay(_GatewayOverlay):
    """Create a reusable, encrypted school recovery backup."""

    backup_requested = Signal(object)

    def __init__(self, parent: QWidget) -> None:
        super().__init__(
            parent,
            title="Create Recovery Backup",
            subtitle=(
                "Create a portable encrypted backup for recovery on a replacement "
                "Control Center. The local Survey Server and Internet Gateway are "
                "paused first for a consistent snapshot."
            ),
            panel_width=760,
        )
        self._school_id = ""
        self._source_installation_id = ""

        self.source_identity = QLabel()
        self.source_identity.setObjectName("gateway_overlay_note")
        self.source_identity.setWordWrap(True)
        self.body_layout.addWidget(self.source_identity)

        destination_label = QLabel("Encrypted recovery-backup destination")
        destination_label.setObjectName("gateway_overlay_field")
        self.body_layout.addWidget(destination_label)
        destination_row = QHBoxLayout()
        self.destination_path = QLineEdit()
        self.destination_path.setReadOnly(True)
        self.destination_path.setPlaceholderText("Choose a .mossbak file")
        self.choose_destination = TooltipIconButton(
            "save", "Choose where to save the encrypted recovery backup", role="subtle"
        )
        destination_row.addWidget(self.destination_path, 1)
        destination_row.addWidget(self.choose_destination)
        self.body_layout.addLayout(destination_row)

        guidance = QLabel(
            "A new 256-bit backup key is generated for every file and shown only "
            "once. Keep the .mossbak file and its key in separate trusted places. "
            "The Control Center does not save the key."
        )
        guidance.setObjectName("gateway_overlay_warning")
        guidance.setWordWrap(True)
        self.body_layout.addWidget(guidance)

        action_row = QHBoxLayout()
        action_row.addStretch(1)
        self.create_button = TooltipIconButton(
            "export", "Create the encrypted portable recovery backup", role="success"
        )
        action_row.addWidget(self.create_button)
        self.body_layout.addLayout(action_row)

        self.result_label = QLabel()
        self.result_label.setObjectName("gateway_overlay_note")
        self.result_label.setWordWrap(True)
        self.result_label.hide()
        self.body_layout.addWidget(self.result_label)

        key_row = QHBoxLayout()
        self.backup_key = QLineEdit()
        self.backup_key.setReadOnly(True)
        self.backup_key.setPlaceholderText("Recovery-backup key appears here once")
        self.backup_key.setAccessibleName("One-time portable recovery-backup key")
        self.copy_key = TooltipIconButton(
            "copy", "Copy the one-time recovery-backup key", role="primary"
        )
        key_row.addWidget(self.backup_key, 1)
        key_row.addWidget(self.copy_key)
        self.body_layout.addLayout(key_row)
        self.body_layout.addStretch(1)

        self.choose_destination.clicked.connect(self._choose_destination)
        self.create_button.clicked.connect(self._submit)
        self.copy_key.clicked.connect(self._copy_key)
        self.copy_key.setEnabled(False)

    def configure(
        self,
        *,
        school_id: str,
        school_name: str,
        source_installation_id: str,
    ) -> None:
        self._school_id = str(school_id or "").strip()
        self._source_installation_id = str(source_installation_id or "").strip()
        self.source_identity.setText(
            f"Active source: {' '.join(str(school_name or 'School').split())}\n"
            f"School ID: {self._school_id or 'Not available'}\n"
            f"Source installation ID: {self._source_installation_id or 'Not available'}"
        )
        self.destination_path.clear()
        self.backup_key.clear()
        self.result_label.hide()
        self.copy_key.setEnabled(False)
        self.set_progress("", busy=False)

    def set_progress(self, message: str, *, busy: bool) -> None:
        self.set_operation_locked(busy)
        self.result_label.setText(str(message or ""))
        self.result_label.setVisible(bool(message))
        enabled = not busy
        self.destination_path.setEnabled(enabled)
        self.choose_destination.setEnabled(enabled)
        self.create_button.setEnabled(enabled and bool(self._school_id))
        self.copy_key.setEnabled(enabled and bool(self.backup_key.text()))

    def show_result(self, result: Mapping[str, Any]) -> None:
        key = str(result.get("backup_key") or "").strip()
        package_path = str(result.get("package_path") or "").strip()
        self.destination_path.setText(package_path)
        self.backup_key.setText(key)
        counts = result.get("record_counts")
        count_text = ", ".join(
            f"{name}: {count}" for name, count in sorted(dict(counts or {}).items())
        )
        self.set_progress(
            "Encrypted recovery backup created. Copy the key now; it disappears "
            "when this window closes."
            + (f" Included records: {count_text}." if count_text else ""),
            busy=False,
        )

    def close_overlay(self) -> None:
        if not self.operation_locked:
            self.backup_key.clear()
            self.copy_key.setEnabled(False)
        super().close_overlay()

    def _choose_destination(self) -> None:
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Save encrypted recovery backup",
            str(Path.home() / f"school-csm-{self._school_id or 'recovery'}.mossbak"),
            "School CSM recovery backup (*.mossbak)",
        )
        if filename:
            if not filename.casefold().endswith(".mossbak"):
                filename += ".mossbak"
            self.destination_path.setText(filename)

    def _submit(self) -> None:
        destination_path = self.destination_path.text().strip()
        if not destination_path:
            self.set_progress("Choose where to save the .mossbak backup.", busy=False)
            return
        self.backup_key.clear()
        self.copy_key.setEnabled(False)
        self.backup_requested.emit({"destination_path": destination_path})

    def _copy_key(self) -> None:
        key = self.backup_key.text().strip()
        if not key:
            return
        QApplication.clipboard().setText(key)
        self.result_label.setText(
            "Recovery-backup key copied. Keep it separate from the .mossbak file "
            "and close this window after recording it securely."
        )
        self.result_label.show()


class InternetServerTransferOverlay(_GatewayOverlay):
    browser_transfer_requested = Signal(object)
    migration_import_requested = Signal(object)
    recovery_restore_requested = Signal(object)
    completion_code_requested = Signal(str)

    def __init__(self, parent: QWidget) -> None:
        super().__init__(
            parent,
            title="Transfer Existing School Server",
            subtitle="Authorize a replacement device without requiring the previous computer to initiate or approve the transfer.",
        )
        self._school_id = ""
        self._school_name = ""
        self._installation_id = ""
        self._package_path = ""
        self._data_validation: dict[str, Any] = {}
        self._selected_data_mode = ""

        self.identity = QLabel()
        self.identity.setObjectName("gateway_overlay_note")
        self.identity.setWordWrap(True)
        self.body_layout.addWidget(self.identity)

        heading = QLabel("Transfer option")
        heading.setObjectName("gateway_overlay_section")
        self.body_layout.addWidget(heading)
        self.option_group = QButtonGroup(self)
        self.server_and_data = QRadioButton(
            "Server and Data — Recommended"
        )
        self.server_and_data.setChecked(True)
        self.backup_assisted = QRadioButton(
            "Recovery Backup and Server Authority"
        )
        self.server_only = QRadioButton("Server Only")
        self.option_group.addButton(self.server_and_data)
        self.option_group.addButton(self.backup_assisted)
        self.option_group.addButton(self.server_only)
        self.body_layout.addWidget(self.server_and_data)
        data_note = QLabel(
            "Transfers the school's Control Center records and Internet Server authority. Authority is transferred only after successful staged data validation."
        )
        data_note.setObjectName("gateway_overlay_note")
        data_note.setWordWrap(True)
        self.body_layout.addWidget(data_note)
        self.body_layout.addWidget(self.backup_assisted)
        self.package_note = QLabel(
            "Server and Data uses a destination-bound .mossmig package prepared "
            "by the active source Control Center."
        )
        self.package_note.setObjectName("gateway_overlay_warning")
        self.package_note.setWordWrap(True)
        self.body_layout.addWidget(self.package_note)

        installation_row = QHBoxLayout()
        self.installation_id = QLineEdit()
        self.installation_id.setReadOnly(True)
        self.installation_id.setPlaceholderText("Replacement installation ID")
        self.copy_installation_id = TooltipIconButton(
            "copy", "Copy this replacement installation ID", role="subtle"
        )
        installation_row.addWidget(self.installation_id, 1)
        installation_row.addWidget(self.copy_installation_id)
        self.body_layout.addLayout(installation_row)

        package_row = QHBoxLayout()
        self.package_path = QLineEdit()
        self.package_path.setReadOnly(True)
        self.package_path.setPlaceholderText("Select the encrypted migration package")
        # Compatibility aliases for early UI integrations.
        self.backup_path = self.package_path
        self.choose_package = TooltipIconButton(
            "open", "Select an encrypted School CSM migration package", role="subtle"
        )
        self.choose_backup = self.choose_package
        package_row.addWidget(self.package_path, 1)
        package_row.addWidget(self.choose_package)
        self.body_layout.addLayout(package_row)

        self.migration_key = QLineEdit()
        self.migration_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.migration_key.setPlaceholderText("Migration key for this package")
        self.migration_key.setAccessibleName("Encrypted migration package key")
        self.body_layout.addWidget(self.migration_key)
        self.replace_existing = QCheckBox(
            "I authorize replacement of existing School CSM records on this computer"
        )
        self.replace_existing.setToolTip(
            "Required only when this computer already contains School CSM records. A rollback copy is retained."
        )
        self.body_layout.addWidget(self.replace_existing)

        self.body_layout.addWidget(self.server_only)
        warning = QLabel(
            "Server Only does not recover historical data from the previous installation. Restore any available backup later. Continuing requires explicit confirmation in the transfer service."
        )
        warning.setObjectName("gateway_overlay_warning")
        warning.setWordWrap(True)
        self.body_layout.addWidget(warning)
        self.server_only_confirmation = QCheckBox(
            "I understand that this transfers Internet authority without recovering historical data"
        )
        self.body_layout.addWidget(self.server_only_confirmation)

        auth_note = QLabel(
            "The managed HTTPS page opens in your browser and requests the registered administrator passkey. The Control Center never collects or stores passkey material."
        )
        auth_note.setObjectName("gateway_overlay_note")
        auth_note.setWordWrap(True)
        self.body_layout.addWidget(auth_note)

        actions = QHBoxLayout()
        actions.addStretch(1)
        self.continue_button = TooltipIconButton(
            "next", "Authenticate and continue with Server Transfer", role="primary"
        )
        actions.addWidget(self.continue_button)
        self.body_layout.addLayout(actions)
        completion_row = QHBoxLayout()
        self.completion_code = QLineEdit()
        self.completion_code.setPlaceholderText("One-time completion code from browser")
        self.completion_code.setAccessibleName("Server Transfer completion code")
        self.redeem_button = TooltipIconButton(
            "check", "Redeem the one-time Server Transfer completion code", role="success"
        )
        completion_row.addWidget(self.completion_code, 1)
        completion_row.addWidget(self.redeem_button)
        self.body_layout.addLayout(completion_row)
        self.progress_label = QLabel()
        self.progress_label.setObjectName("gateway_overlay_note")
        self.progress_label.setWordWrap(True)
        self.progress_label.hide()
        self.body_layout.addWidget(self.progress_label)
        self.body_layout.addStretch(1)

        self.choose_package.clicked.connect(self._choose_package)
        self.copy_installation_id.clicked.connect(
            lambda: QApplication.clipboard().setText(self._installation_id)
        )
        self.server_and_data.toggled.connect(self._sync_transfer_controls)
        self.backup_assisted.toggled.connect(self._sync_transfer_controls)
        self.server_only.toggled.connect(self._sync_transfer_controls)
        self.continue_button.clicked.connect(self._submit)
        self.redeem_button.clicked.connect(self._redeem)
        self._sync_transfer_controls()

    def configure(
        self,
        *,
        school_id: str,
        school_name: str,
        installation_id: str = "",
    ) -> None:
        self._school_id = str(school_id or "").strip()
        self._school_name = " ".join(str(school_name or "").split())
        self._installation_id = str(installation_id or "").strip()
        self.identity.setText(
            f"Registered School: {self._school_name or 'School'}\nSchool ID: {self._school_id or 'Not available'}"
        )
        self.server_and_data.setChecked(True)
        self.installation_id.setText(self._installation_id)
        self.copy_installation_id.setEnabled(bool(self._installation_id))
        self._package_path = ""
        self._data_validation = {}
        self.package_path.clear()
        self.migration_key.clear()
        self.replace_existing.setChecked(False)
        self.server_only_confirmation.setChecked(False)
        self.progress_label.hide()
        self.completion_code.clear()
        self.set_operation_locked(False)
        self.continue_button.setEnabled(bool(self._school_id))
        self._sync_transfer_controls()

    def set_data_validation_result(self, validation: Mapping[str, Any]) -> None:
        """Hold the exact receipt produced by the local committed import."""

        self._data_validation = dict(validation or {})
        option = self._selected_option()
        common_fields = {
            "verified",
            "transaction_id",
            "manifest_sha256",
            "active_tree_sha256",
            "record_counts",
        }
        backup_fields = {
            "source_kind",
            "backup_id",
            "backup_manifest_sha256",
        }
        expected_fields = common_fields | (
            backup_fields if option == "backup_assisted" else set()
        )
        verified = bool(
            option in {"server_and_data", "backup_assisted"}
            and set(self._data_validation) == expected_fields
            and self._data_validation.get("verified") is True
            and (
                option != "backup_assisted"
                or self._data_validation.get("source_kind") == "portable_backup"
            )
        )
        if not verified:
            self._data_validation = {}
        self.set_progress(
            "School CSM data was validated and activated. Opening secure transfer authorization..."
            if verified
            else "The data-validation receipt does not match the selected transfer mode."
        )
        self._sync_transfer_controls()

    def set_data_validation_verified(self, verified: bool) -> None:
        """Deprecated safe shim: a Boolean can never authorize a transfer."""

        self._data_validation = {}
        if verified:
            self.set_progress(
                "A signed migration receipt is required; a verification checkbox is not sufficient."
            )

    def set_progress(self, message: str, *, cutover_locked: bool = False) -> None:
        self.progress_label.setText(str(message or ""))
        self.progress_label.setVisible(bool(message))
        self.set_operation_locked(cutover_locked)
        self.continue_button.setEnabled(not cutover_locked and bool(self._school_id))
        self.completion_code.setEnabled(not cutover_locked and bool(self._school_id))
        self.redeem_button.setEnabled(not cutover_locked and bool(self._school_id))
        self._sync_transfer_controls()

    def _sync_transfer_controls(self) -> None:
        option = self._selected_option()
        data_mode = option != "server_only"
        if data_mode and self._selected_data_mode != option:
            self._selected_data_mode = option
            self._package_path = ""
            self._data_validation = {}
            self.package_path.clear()
            self.migration_key.clear()
        if option == "backup_assisted":
            self.package_path.setPlaceholderText(
                "Select the encrypted portable recovery backup (.mossbak)"
            )
            self.migration_key.setPlaceholderText("Recovery-backup key for this file")
            self.migration_key.setAccessibleName("Encrypted portable recovery-backup key")
            self.choose_package.setToolTip(
                "Select an encrypted School CSM portable recovery backup"
            )
            self.package_note.setText(
                "Recovery Backup validates and restores a reusable .mossbak file "
                "before Internet authority can move to this computer."
            )
        else:
            self.package_path.setPlaceholderText(
                "Select the destination-bound migration package (.mossmig)"
            )
            self.migration_key.setPlaceholderText("Migration key for this package")
            self.migration_key.setAccessibleName("Encrypted migration package key")
            self.choose_package.setToolTip(
                "Select an encrypted School CSM migration package"
            )
            self.package_note.setText(
                "Server and Data uses a destination-bound .mossmig package prepared "
                "by the active source Control Center."
            )
        editable = data_mode and not self.operation_locked and not self._data_validation
        self.package_path.setEnabled(editable)
        self.choose_package.setEnabled(editable)
        self.migration_key.setEnabled(editable)
        self.replace_existing.setEnabled(editable)
        self.server_only_confirmation.setEnabled(
            self.server_only.isChecked() and not self.operation_locked
        )

    def _choose_package(self) -> None:
        backup_mode = self.backup_assisted.isChecked()
        filename, _ = QFileDialog.getOpenFileName(
            self,
            (
                "Select encrypted School CSM recovery backup"
                if backup_mode
                else "Select encrypted School CSM migration package"
            ),
            str(Path.home()),
            (
                "School CSM recovery backup (*.mossbak)"
                if backup_mode
                else "School CSM migration package (*.mossmig)"
            ),
        )
        if filename:
            self._package_path = filename
            self.package_path.setText(filename)
            self._data_validation = {}

    def _submit(self) -> None:
        option = self._selected_option()
        if option == "server_only":
            if not self.server_only_confirmation.isChecked():
                self.set_progress(
                    "Confirm the Server Only historical-data warning before continuing."
                )
                return
            self.browser_transfer_requested.emit(
                {
                    "transfer_mode": option,
                    "data_validation": {"warning_confirmed": True},
                }
            )
            return
        if self._data_validation.get("verified") is True:
            self.browser_transfer_requested.emit(
                {
                    "transfer_mode": option,
                    "data_validation": dict(self._data_validation),
                }
            )
            return
        if not self._package_path:
            self.set_progress(
                "Select the encrypted .mossbak recovery backup."
                if option == "backup_assisted"
                else "Select the encrypted .mossmig migration package."
            )
            return
        expected_suffix = ".mossbak" if option == "backup_assisted" else ".mossmig"
        if Path(self._package_path).suffix.casefold() != expected_suffix:
            self.set_progress(
                f"The selected {option.replace('_', ' ')} file must use the "
                f"{expected_suffix} extension."
            )
            return
        key = self.migration_key.text().strip()
        if not key:
            self.set_progress(
                "Enter the recovery-backup key supplied for this file."
                if option == "backup_assisted"
                else "Enter the migration key supplied for this package."
            )
            self.migration_key.setFocus()
            return
        request = {
            "transfer_mode": option,
            "package_path": self._package_path,
            "migration_key": key,
            "replace_existing": self.replace_existing.isChecked(),
        }
        if option == "backup_assisted":
            self.recovery_restore_requested.emit(request)
        else:
            self.migration_import_requested.emit(request)
        self.migration_key.clear()

    def _selected_option(self) -> str:
        if self.server_only.isChecked():
            return "server_only"
        if self.backup_assisted.isChecked():
            return "backup_assisted"
        return "server_and_data"

    def _redeem(self) -> None:
        code = self.completion_code.text().strip()
        if not code:
            self.set_progress("Enter the one-time completion code shown in the browser.")
            self.completion_code.setFocus()
            return
        self.completion_code_requested.emit(code)
        self.completion_code.clear()


class InternetGatewayDiagnosticsOverlay(_GatewayOverlay):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(
            parent,
            title="Internet Gateway Diagnostics",
            subtitle="Infrastructure checks only. No CSM responses, remarks, scanner images, or credentials are included.",
            panel_width=760,
        )
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.results = QWidget()
        self.results_layout = QVBoxLayout(self.results)
        self.results_layout.setContentsMargins(0, 0, 0, 0)
        self.results_layout.setSpacing(7)
        self.results_layout.addStretch(1)
        self.scroll.setWidget(self.results)
        self.body_layout.addWidget(self.scroll, 1)

    def show_results(self, diagnostics: Mapping[str, Any]) -> None:
        while self.results_layout.count() > 1:
            item = self.results_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        labels = {
            "registration": "Registration",
            "school_id_match": "School ID match",
            "authorization": "Authorization",
            "authorization_checked_now": "Authorization checked now",
            "registration_service_https": "Registration service HTTPS",
            "credential_present": "Gateway credential",
            "component_installed": "Tunnel component",
            "tunnel_service": "Tunnel service",
            "local_server_running": "Local Survey Server",
            "public_dns": "Public DNS",
            "public_https": "Public HTTPS",
            "host_routing": "School-ID host routing",
            "survey_form_status": "Survey Form",
            "scanner_remote_enabled": "Scanner Remote",
            "last_authorization_check_at": "Last authorization check",
            "public_host": "Public host",
            "detail": "Details",
            "provider_error": "Provider diagnostic error",
        }
        for key, label in labels.items():
            if key not in diagnostics:
                continue
            value = diagnostics.get(key)
            if isinstance(value, bool):
                value = "Passed" if value else "Failed"
            row = QFrame()
            row.setObjectName("gateway_diagnostic_row")
            layout = QHBoxLayout(row)
            layout.setContentsMargins(11, 8, 11, 8)
            name = QLabel(label)
            name.setObjectName("gateway_overlay_field")
            result = QLabel(str(value or "Not available"))
            result.setObjectName("gateway_diagnostic_value")
            result.setWordWrap(True)
            result.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(name, 1)
            layout.addWidget(result, 2)
            self.results_layout.insertWidget(self.results_layout.count() - 1, row)


class RegistrationDetailsOverlay(_GatewayOverlay):
    manage_passkeys_requested = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(
            parent,
            title="Internet Gateway Registration Details",
            subtitle="Sanitized infrastructure identity for this installation. Secrets and passkey material are never displayed.",
            panel_width=720,
        )
        self.details = QLabel()
        self.details.setObjectName("gateway_overlay_note")
        self.details.setWordWrap(True)
        self.details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.body_layout.addWidget(self.details)
        self.manage_passkeys_button = TooltipIconButton(
            "key",
            "Manage Administrator Passkeys in the secure browser page",
            role="primary",
        )
        self.manage_passkeys_button.setAccessibleName("Manage Administrator Passkeys")
        self.manage_passkeys_button.clicked.connect(self.manage_passkeys_requested.emit)
        passkey_row = QHBoxLayout()
        passkey_label = QLabel("Manage Administrator Passkeys")
        passkey_label.setObjectName("gateway_overlay_section")
        passkey_row.addStretch(1)
        passkey_row.addWidget(passkey_label)
        passkey_row.addWidget(self.manage_passkeys_button)
        self.body_layout.addLayout(passkey_row)
        self.body_layout.addStretch(1)

    def show_state(self, state: Mapping[str, Any]) -> None:
        lines = (
            ("School", state.get("registered_school_name")),
            ("School ID", state.get("school_id")),
            ("Public host", state.get("public_host")),
            ("Registration", state.get("registration_state")),
            ("Authorization", state.get("authorization_state")),
            ("Gateway", state.get("gateway_state")),
            ("Last authorization check", state.get("last_authorization_check_at")),
            ("Last connected", state.get("last_connected_at")),
        )
        self.details.setText(
            "\n".join(f"{label}: {value or 'Not available'}" for label, value in lines)
        )
        self.manage_passkeys_button.setEnabled(
            bool(state.get("provider_available"))
            and bool(state.get("school_id"))
            and str(state.get("registration_state") or "") == "registered"
            and not bool(state.get("retirement"))
        )
