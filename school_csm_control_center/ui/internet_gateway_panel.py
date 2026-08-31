"""Server-board section for optional Internet Gateway access."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any, Mapping

from PySide6.QtCore import QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from school_csm_control_center.internet_gateway.controller import (
    AUTHORIZATION_LABELS,
    GATEWAY_LABELS,
    REGISTRATION_LABELS,
)
from school_csm_control_center.ui import theme
from school_csm_control_center.ui.widgets import TooltipIconButton


class InternetGatewayPanel(QFrame):
    """Display gateway state and emit operator intent for privileged actions."""

    setup_requested = Signal()
    connect_requested = Signal()
    diagnostics_requested = Signal()
    transfer_requested = Signal()
    prepare_package_requested = Signal()
    prepare_backup_requested = Signal()
    registration_details_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("internet_gateway_section")
        self._state: dict[str, Any] = {}
        self._survey_url = ""
        self._scanner_url = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        status_card = self._card("Internet Gateway")
        status = status_card.layout()
        self.registration_metric = self._metric("Registration", "Not Configured")
        self.school_id_metric = self._metric("School ID", "Not available")
        self.public_host_metric = self._metric("Public Host", "Not available")
        self.gateway_metric = self._metric("Gateway Status", "Not Configured")
        self.authorized_metric = self._metric("Authorized Server", "Not Registered")
        self.internet_survey_metric = self._metric("Internet Survey", "Not available")
        self.internet_scanner_metric = self._metric("Internet Scanner", "Not available")

        metrics = (
            self.registration_metric,
            self.school_id_metric,
            self.public_host_metric,
            self.gateway_metric,
            self.authorized_metric,
            self.internet_survey_metric,
            self.internet_scanner_metric,
        )
        for index, metric in enumerate(metrics):
            status.addWidget(metric[0], 1 + (index // 4), index % 4)

        self.setup_button = TooltipIconButton(
            "settings",
            "Set up or update the optional Internet Gateway",
            button_size=36,
            icon_size=19,
        )
        self.connect_button = TooltipIconButton(
            "reset", "Connect or reconnect the Internet Gateway", button_size=36, icon_size=19
        )
        self.diagnostics_button = TooltipIconButton(
            "check", "Run Internet Gateway diagnostics", button_size=36, icon_size=19
        )
        self.details_button = TooltipIconButton(
            "info", "View Internet Gateway registration details", button_size=36, icon_size=19
        )
        self.transfer_button = TooltipIconButton(
            "next",
            "Transfer this school's Internet Server to another authorized device",
            button_size=36,
            icon_size=19,
            icon_color=theme.WARNING,
        )
        self.prepare_package_button = TooltipIconButton(
            "export",
            "Prepare an encrypted Server and Data package for a replacement computer",
            button_size=36,
            icon_size=19,
            icon_color=theme.SUCCESS,
        )
        self.prepare_backup_button = TooltipIconButton(
            "save",
            "Create an encrypted portable recovery backup",
            button_size=36,
            icon_size=19,
            icon_color=theme.ACCENT_CYAN,
        )
        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        action_label = QLabel("Actions")
        action_label.setObjectName("internet_gateway_action_label")
        action_row.addWidget(action_label)
        action_row.addStretch(1)
        for button in (
            self.setup_button,
            self.connect_button,
            self.diagnostics_button,
            self.details_button,
        ):
            action_row.addWidget(button)
        status.addLayout(action_row, 3, 0, 1, 4)

        continuity_row = QHBoxLayout()
        continuity_row.setSpacing(8)
        continuity_heading = QLabel("Data continuity")
        continuity_heading.setObjectName("internet_gateway_action_label")
        continuity_row.addWidget(continuity_heading)
        continuity_row.addStretch(1)
        prepare_label = QLabel("Prepare Server + Data Package")
        prepare_label.setObjectName("internet_gateway_action_label")
        continuity_row.addWidget(prepare_label)
        continuity_row.addWidget(self.prepare_package_button)
        backup_label = QLabel("Recovery Backup")
        backup_label.setObjectName("internet_gateway_action_label")
        continuity_row.addWidget(backup_label)
        continuity_row.addWidget(self.prepare_backup_button)
        transfer_label = QLabel("Transfer Server")
        transfer_label.setObjectName("internet_gateway_action_label")
        continuity_row.addWidget(transfer_label)
        continuity_row.addWidget(self.transfer_button)
        status.addLayout(continuity_row, 4, 0, 1, 4)

        self.detail_label = QLabel(
            "Internet Gateway is optional. The Control Center continues operating locally when it is not configured or the Internet is unavailable."
        )
        self.detail_label.setObjectName("internet_gateway_note")
        self.detail_label.setWordWrap(True)
        self.detail_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        status.addWidget(self.detail_label, 5, 0, 1, 4)
        layout.addWidget(status_card)

        access_card = self._card("Internet access links")
        access = access_card.layout()
        access_helper = QLabel(
            "The permanent public host uses the official School ID. The Survey link keeps the current temporary access key; regenerating that key invalidates earlier Survey QR codes."
        )
        access_helper.setObjectName("internet_gateway_note")
        access_helper.setWordWrap(True)
        access.addWidget(access_helper, 1, 0, 1, 2)

        self.survey_qr = self._qr_label("Complete Internet Gateway setup")
        self.scanner_qr = self._qr_label("Complete Internet Gateway setup")
        self.survey_address = self._readonly_line()
        self.scanner_address = self._readonly_line()

        self.copy_survey = TooltipIconButton(
            "copy", "Copy Internet Survey link", button_size=36, icon_size=19
        )
        self.open_survey = TooltipIconButton(
            "open", "Open Internet Survey", button_size=36, icon_size=19
        )
        self.save_survey_qr = TooltipIconButton(
            "save", "Save Internet Survey QR as PNG", button_size=36, icon_size=19
        )
        self.copy_scanner = TooltipIconButton(
            "copy", "Copy Internet Scanner Remote link", button_size=36, icon_size=19
        )
        self.open_scanner = TooltipIconButton(
            "open", "Open Internet Scanner Remote", button_size=36, icon_size=19
        )
        self.save_scanner_qr = TooltipIconButton(
            "save", "Save Internet Scanner Remote QR as PNG", button_size=36, icon_size=19
        )

        survey_actions = self._action_row(
            self.copy_survey, self.open_survey, self.save_survey_qr
        )
        scanner_actions = self._action_row(
            self.copy_scanner, self.open_scanner, self.save_scanner_qr
        )
        access.addWidget(
            self._access_tile(
                "Internet Survey",
                "For respondents using mobile data or an external network",
                self.survey_qr,
                self.survey_address,
                survey_actions,
            ),
            2,
            0,
        )
        access.addWidget(
            self._access_tile(
                "Internet Scanner Remote",
                "Uses the existing Scanner Operator sign-in and local processing",
                self.scanner_qr,
                self.scanner_address,
                scanner_actions,
            ),
            2,
            1,
        )
        access.setColumnStretch(0, 1)
        access.setColumnStretch(1, 1)
        layout.addWidget(access_card)

        self.setup_button.clicked.connect(self.setup_requested.emit)
        self.connect_button.clicked.connect(self.connect_requested.emit)
        self.diagnostics_button.clicked.connect(self.diagnostics_requested.emit)
        self.transfer_button.clicked.connect(self.transfer_requested.emit)
        self.prepare_package_button.clicked.connect(self.prepare_package_requested.emit)
        self.prepare_backup_button.clicked.connect(self.prepare_backup_requested.emit)
        self.details_button.clicked.connect(self.registration_details_requested.emit)
        self.copy_survey.clicked.connect(
            lambda: QApplication.clipboard().setText(self._survey_url)
        )
        self.open_survey.clicked.connect(lambda: self._open_url(self._survey_url))
        self.save_survey_qr.clicked.connect(
            lambda: self._save_qr(
                self._survey_url, "school_csm_internet_survey_qr.png"
            )
        )
        self.copy_scanner.clicked.connect(
            lambda: QApplication.clipboard().setText(self._scanner_url)
        )
        self.open_scanner.clicked.connect(lambda: self._open_url(self._scanner_url))
        self.save_scanner_qr.clicked.connect(
            lambda: self._save_qr(
                self._scanner_url, "school_csm_internet_scanner_qr.png"
            )
        )
        self.apply_state({})
        self._apply_styles()

    def apply_state(self, state: Mapping[str, Any]) -> None:
        self._state = dict(state or {})
        registration_state = str(
            self._state.get("registration_state") or "not_configured"
        )
        gateway_state = str(self._state.get("gateway_state") or "stopped")
        authorization_state = str(
            self._state.get("authorization_state") or "unregistered"
        )
        self.registration_metric[1].setText(
            REGISTRATION_LABELS.get(registration_state, "Registration Error")
        )
        self.school_id_metric[1].setText(
            str(
                self._state.get("school_id")
                or self._state.get("local_school_id")
                or "Not available"
            )
        )
        self.public_host_metric[1].setText(
            str(self._state.get("public_host") or "Not available")
        )
        self.gateway_metric[1].setText(
            GATEWAY_LABELS.get(gateway_state, "Error")
        )
        self.authorized_metric[1].setText(
            AUTHORIZATION_LABELS.get(authorization_state, "Registration Error")
        )

        connected = gateway_state == "connected"
        survey_online = connected and str(
            self._state.get("survey_form_status") or "offline"
        ) == "online"
        scanner_enabled = bool(self._state.get("scanner_remote_enabled", True))
        self.internet_survey_metric[1].setText(
            "Available" if survey_online else ("Survey Form Offline" if connected else "Unavailable")
        )
        self.internet_scanner_metric[1].setText(
            "Available"
            if connected and scanner_enabled
            else ("Scanner Remote Disabled" if connected else "Unavailable")
        )

        self._set_metric_state(self.registration_metric, registration_state)
        self._set_metric_state(self.gateway_metric, gateway_state)
        self._set_metric_state(self.authorized_metric, authorization_state)
        self._set_metric_state(
            self.internet_survey_metric, "connected" if survey_online else "disconnected"
        )
        self._set_metric_state(
            self.internet_scanner_metric,
            "connected" if connected and scanner_enabled else "disconnected",
        )

        self._survey_url = str(self._state.get("survey_url") or "").strip()
        self._scanner_url = str(self._state.get("scanner_url") or "").strip()
        self.survey_address.setText(self._survey_url)
        self.scanner_address.setText(self._scanner_url)
        self._render_qr(
            self.survey_qr, self._survey_url, "Complete Internet Gateway setup"
        )
        self._render_qr(
            self.scanner_qr, self._scanner_url, "Complete Internet Gateway setup"
        )
        for button in (self.copy_survey, self.open_survey, self.save_survey_qr):
            button.setEnabled(bool(self._survey_url))
        for button in (self.copy_scanner, self.open_scanner, self.save_scanner_qr):
            button.setEnabled(bool(self._scanner_url) and scanner_enabled)

        configured = registration_state == "registered" and bool(
            self._state.get("public_host")
        )
        direct_mode = self._state.get("deployment_mode") == "direct_worker_vpc"
        privileged = authorization_state == "active"
        busy = gateway_state == "connecting" or bool(self._state.get("operation"))
        retired = authorization_state in {"transferred", "revoked"} or bool(
            self._state.get("retirement")
        )
        self.setup_button.setEnabled(
            (not configured or direct_mode)
            and gateway_state != "connected"
            and not busy
            and not retired
        )
        self.connect_button.setEnabled(configured and privileged and not busy and not retired)
        self.diagnostics_button.setEnabled(configured and not busy and not retired)
        self.details_button.setEnabled(configured or retired)
        transfer_identity_ready = bool(
            self._state.get("school_id") or self._state.get("local_school_id")
        )
        self.transfer_button.setEnabled(
            bool(self._state.get("provider_available"))
            and transfer_identity_ready
            and not busy
            and not retired
        )
        self.prepare_package_button.setEnabled(
            configured and privileged and not busy and not retired
        )
        self.prepare_backup_button.setEnabled(
            configured and privileged and not busy and not retired
        )
        self.detail_label.setText(
            str(self._state.get("detail") or "").strip()
            or "Internet Gateway is optional. Local-Only operation remains available."
        )

    @staticmethod
    def _set_metric_state(metric: tuple[QWidget, QLabel], state: str) -> None:
        label = metric[1]
        normalized = str(state).casefold()
        if normalized in {"registered", "active", "connected"}:
            visual = "online"
        elif normalized in {"connecting", "transfer_pending", "registration_required"}:
            visual = "maintenance"
        elif normalized in {
            "registration_error",
            "error",
            "authorization_transferred",
            "transferred",
            "revoked",
        }:
            visual = "error"
        else:
            visual = "offline"
        label.setProperty("state", visual)
        label.style().unpolish(label)
        label.style().polish(label)

    @staticmethod
    def _open_url(url: str) -> None:
        value = str(url or "").strip()
        if value.casefold().startswith("https://"):
            QDesktopServices.openUrl(QUrl(value))

    def _save_qr(self, value: str, suggested_name: str) -> None:
        if not value:
            return
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Save QR code",
            str(Path.home() / suggested_name),
            "PNG image (*.png)",
        )
        if not filename:
            return
        if not filename.casefold().endswith(".png"):
            filename += ".png"
        import qrcode

        qrcode.make(value).save(filename, format="PNG")

    @staticmethod
    def _render_qr(label: QLabel, value: str, fallback: str) -> None:
        if not value:
            label.setPixmap(QPixmap())
            label.setText(fallback)
            return
        try:
            import qrcode

            image = qrcode.make(value)
            buffer = BytesIO()
            image.save(buffer, format="PNG")
            pixmap = QPixmap()
            pixmap.loadFromData(buffer.getvalue(), "PNG")
            label.setText("")
            label.setPixmap(
                pixmap.scaled(
                    142,
                    142,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        except Exception:
            label.setPixmap(QPixmap())
            label.setText("QR generation unavailable")

    @staticmethod
    def _card(title: str) -> QFrame:
        card = QFrame()
        card.setObjectName("internet_gateway_card")
        grid = QGridLayout(card)
        grid.setContentsMargins(18, 16, 18, 18)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(10)
        heading = QLabel(title)
        heading.setObjectName("internet_gateway_card_title")
        grid.addWidget(heading, 0, 0, 1, 4)
        return card

    @staticmethod
    def _metric(title: str, value: str) -> tuple[QWidget, QLabel]:
        box = QWidget()
        box.setObjectName("internet_gateway_metric_box")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        title_label = QLabel(title)
        title_label.setObjectName("internet_gateway_metric_title")
        value_label = QLabel(value)
        value_label.setObjectName("internet_gateway_metric")
        value_label.setWordWrap(True)
        value_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(title_label)
        layout.addWidget(value_label)
        return box, value_label

    @staticmethod
    def _readonly_line() -> QLineEdit:
        line = QLineEdit()
        line.setReadOnly(True)
        line.setPlaceholderText("Internet Gateway is not configured")
        return line

    @staticmethod
    def _qr_label(fallback: str) -> QLabel:
        label = QLabel(fallback)
        label.setObjectName("internet_gateway_qr")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setWordWrap(True)
        label.setFixedSize(154, 154)
        return label

    @staticmethod
    def _action_row(*buttons: TooltipIconButton) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(7)
        row.addStretch(1)
        for button in buttons:
            row.addWidget(button)
        row.addStretch(1)
        return row

    @staticmethod
    def _access_tile(
        title: str,
        subtitle: str,
        qr: QLabel,
        address: QLineEdit,
        actions: QHBoxLayout,
    ) -> QFrame:
        tile = QFrame()
        tile.setObjectName("internet_gateway_access_tile")
        layout = QVBoxLayout(tile)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)
        title_label = QLabel(title)
        title_label.setObjectName("internet_gateway_access_title")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle_label = QLabel(subtitle)
        subtitle_label.setObjectName("internet_gateway_access_subtitle")
        subtitle_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle_label.setWordWrap(True)
        layout.addWidget(title_label)
        layout.addWidget(subtitle_label)
        layout.addWidget(qr, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(address)
        layout.addLayout(actions)
        return tile

    def _apply_styles(self) -> None:
        self.setStyleSheet(
            f"""
            QFrame#internet_gateway_section {{ background: transparent; border: none; }}
            QFrame#internet_gateway_card {{
                background: {theme.PANEL_BG};
                border: 1px solid {theme.DIVIDER};
                border-radius: 14px;
            }}
            QLabel#internet_gateway_card_title {{
                color: {theme.TEXT_PRIMARY}; background: transparent;
                font-size: 15px; font-weight: 800;
            }}
            QWidget#internet_gateway_metric_box {{ background: transparent; }}
            QLabel#internet_gateway_metric_title {{
                color: {theme.TEXT_MUTED}; background: transparent;
                font-size: 10px; font-weight: 700;
            }}
            QLabel#internet_gateway_metric {{
                color: {theme.TEXT_PRIMARY}; background: transparent;
                font-size: 13px; font-weight: 800;
            }}
            QLabel#internet_gateway_metric[state="online"] {{ color: {theme.SUCCESS}; }}
            QLabel#internet_gateway_metric[state="maintenance"] {{ color: {theme.WARNING}; }}
            QLabel#internet_gateway_metric[state="error"] {{ color: {theme.DANGER}; }}
            QLabel#internet_gateway_metric[state="offline"] {{ color: {theme.TEXT_MUTED}; }}
            QLabel#internet_gateway_action_label {{
                color: {theme.TEXT_SECONDARY}; background: transparent;
                font-size: 12px; font-weight: 700;
            }}
            QLabel#internet_gateway_note {{
                color: {theme.TEXT_SECONDARY}; background: transparent;
                font-size: 12px;
            }}
            QFrame#internet_gateway_access_tile {{
                background: rgba(4, 22, 38, 0.74);
                border: 1px solid rgba(87, 214, 220, 0.24);
                border-radius: 12px;
            }}
            QLabel#internet_gateway_access_title {{
                color: {theme.TEXT_PRIMARY}; background: transparent;
                font-size: 14px; font-weight: 800;
            }}
            QLabel#internet_gateway_access_subtitle {{
                color: {theme.TEXT_MUTED}; background: transparent;
                font-size: 11px;
            }}
            QLabel#internet_gateway_qr {{
                color: {theme.TEXT_MUTED}; background: white;
                border: 1px solid rgba(87, 214, 220, 0.34);
                border-radius: 10px; padding: 5px;
            }}
            """
        )
