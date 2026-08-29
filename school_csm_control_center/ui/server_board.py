from __future__ import annotations

from datetime import date
from io import BytesIO
from pathlib import Path
from typing import Any

from PySide6.QtCore import QDate, QSignalBlocker, QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from school_csm_control_center.ui import theme
from school_csm_control_center.ui.controls import NoWheelComboBox, NoWheelDateEdit, NoWheelSpinBox
from school_csm_control_center.ui.overlays import OverlayPrompt
from school_csm_control_center.ui.internet_gateway_panel import InternetGatewayPanel
from school_csm_control_center.ui.widgets import TooltipIconButton
from school_csm_control_center.web_server.controller import SurveyServerController


class SurveyServerBoard(QWidget):
    """Server, Survey Form status, local address, and QR access controls."""

    background_startup_requested = Signal(bool)

    def __init__(
        self,
        controller: SurveyServerController,
        parent: QWidget | None = None,
        *,
        gateway_controller: Any | None = None,
    ) -> None:
        super().__init__(parent)
        self.controller = controller
        self.gateway_controller = gateway_controller
        self._named_access_url = ""
        self._direct_access_url = ""
        self._wifi_payload = ""
        self._access_capability: dict[str, Any] = {}
        self.setObjectName("survey_server_board")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setObjectName("server_scroll")
        content = QWidget()
        content.setObjectName("server_content")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(28, 24, 28, 34)
        layout.setSpacing(18)

        title = QLabel("Survey Server and Access")
        title.setObjectName("server_title")
        subtitle = QLabel(
            "Operate the local CSM Survey Form and optionally publish the same service through the managed Internet Gateway. Local access remains independent."
        )
        subtitle.setWordWrap(True)
        subtitle.setObjectName("server_subtitle")
        layout.addWidget(title)
        layout.addWidget(subtitle)

        status_card = self._card("Current operation")
        status_layout = status_card.layout()
        self.server_status = self._metric("Local server", "Stopped")
        self.form_status = self._metric("Survey Form", "Offline")
        self.response_count = self._metric("Responses this session", "0")
        self.active_sessions = self._metric("Active sessions", "0")
        self.portal_status = self._metric("Captive Portal", "Stopped")
        self.scanner_status = self._metric("Scanner Remote", "Available")
        for column, metric in enumerate((self.server_status, self.form_status, self.response_count, self.active_sessions, self.portal_status, self.scanner_status)):
            status_layout.addWidget(metric[0], 1, column)
        self.status_detail = QLabel("Start the server when the local survey network is ready.")
        self.status_detail.setWordWrap(True)
        self.status_detail.setObjectName("server_note")
        status_layout.addWidget(self.status_detail, 2, 0, 1, 6)
        layout.addWidget(status_card)

        settings_card = self._card("Survey Form settings")
        grid = settings_card.layout()
        self.mode_combo = NoWheelComboBox()
        self.mode_combo.addItem("Onsite questionnaire", "onsite")
        self.date_edit = NoWheelDateEdit()
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("yyyy-MM-dd")
        today = date.today()
        self.date_edit.setDate(QDate(today.year, today.month, today.day))
        self.port_spin = NoWheelSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(80)
        self.server_ip_combo = NoWheelComboBox()
        self.server_ip_combo.setToolTip("Select the laptop IPv4 address reachable from respondent phones. For Windows Mobile Hotspot this is commonly 192.168.137.1.")
        self.status_combo = NoWheelComboBox()
        self.status_combo.addItem("Online", "online")
        self.status_combo.addItem("Offline", "offline")
        self.status_combo.addItem("Under Maintenance", "maintenance")
        self.status_message = QLineEdit()
        self.status_message.setPlaceholderText("Optional public status message")
        self.identifier = QLineEdit()
        self.identifier.setPlaceholderText("school-csm")
        self.session_minutes = NoWheelSpinBox()
        self.session_minutes.setRange(1, 60)
        self.session_minutes.setValue(5)
        controls = [
            ("Questionnaire", self.mode_combo),
            ("Survey date", self.date_edit),
            ("Preferred port", self.port_spin),
            ("Respondent access IP", self.server_ip_combo),
            ("Survey Form status", self.status_combo),
            ("School address identifier", self.identifier),
            ("QR access duration (minutes)", self.session_minutes),
        ]
        for index, (label, widget) in enumerate(controls):
            row = 1 + (index // 3) * 2
            col = index % 3
            grid.addWidget(self._field_label(label), row, col)
            grid.addWidget(widget, row + 1, col)
        grid.addWidget(self._field_label("Public status message"), 7, 0)
        grid.addWidget(self.status_message, 8, 0, 1, 3)
        self.apply_button = TooltipIconButton("check", "Apply Survey Form and server settings", role="subtle")
        self.stop_button = TooltipIconButton("stop", "Stop the local survey server", icon_color=theme.DANGER, role="danger")
        self.start_button = TooltipIconButton("play", "Start the local survey server", icon_color=theme.SUCCESS, role="success")
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addStretch(1)
        buttons.addWidget(self.apply_button)
        buttons.addWidget(self.stop_button)
        buttons.addWidget(self.start_button)
        grid.addLayout(buttons, 9, 0, 1, 3)
        layout.addWidget(settings_card)

        background_card = self._card("Background operation")
        background = background_card.layout()
        background_title = QLabel("Start the app and survey server with Windows")
        background_title.setObjectName("background_startup_title")
        background_helper = QLabel(
            "When enabled, the installed app starts hidden in the Windows notification area and starts the local survey server automatically. If this device has an active Internet Gateway registration, it reconnects only after local server health and device authorization are verified. Setup authorizes Private-network access so Windows does not request permission again at sign-in."
        )
        background_helper.setObjectName("server_note")
        background_helper.setWordWrap(True)
        self.background_startup_switch = QCheckBox("OFF")
        self.background_startup_switch.setObjectName("background_startup_switch")
        self.background_startup_switch.setFixedSize(84, 36)
        self.background_startup_switch.setAccessibleName(
            "Start the app and survey server with Windows"
        )
        self.background_startup_switch.setAccessibleDescription(
            "Enable or disable automatic background startup, unattended local server startup, and verified Internet Gateway reconnect for a registered device."
        )
        self.background_startup_switch.setToolTip(
            "Start hidden, start the local survey server, then reconnect an authorized Internet Gateway"
        )
        self.background_startup_state = QLabel("Disabled")
        self.background_startup_state.setObjectName("background_startup_state")
        background.addWidget(background_title, 1, 0, 1, 2)
        background.addWidget(self.background_startup_switch, 1, 2, Qt.AlignmentFlag.AlignRight)
        background.addWidget(self.background_startup_state, 2, 2, Qt.AlignmentFlag.AlignRight)
        background.addWidget(background_helper, 2, 0, 1, 2)
        layout.addWidget(background_card)

        self.internet_gateway = InternetGatewayPanel()
        layout.addWidget(self.internet_gateway)
        if self.gateway_controller is not None:
            self.internet_gateway.apply_state(self.gateway_controller.state())
            self.gateway_controller.state_changed.connect(
                self.internet_gateway.apply_state
            )

        network_card = self._card("Laptop hotspot and captive portal")
        net = network_card.layout()
        self.network_mode = NoWheelComboBox()
        self.network_mode.addItem("Laptop Hotspot — Recommended", "hotspot")
        self.network_mode.addItem("Existing Wi-Fi — Advanced", "existing_wifi")
        self.access_mode = NoWheelComboBox()
        self.access_mode.addItem("Captive Portal — One-scan default", "captive_portal")
        self.access_mode.addItem("Two-Step QR fallback", "two_step")
        self.internet_mode = NoWheelComboBox()
        self.internet_mode.addItem("Survey Network Only", "survey_only")
        self.internet_mode.addItem("Share Laptop Internet", "share_internet")
        self.ssid = QLineEdit()
        self.ssid.setPlaceholderText("Laptop hotspot SSID")
        self.security = NoWheelComboBox()
        self.security.addItem("WPA/WPA2/WPA3", "WPA")
        self.security.addItem("WEP", "WEP")
        self.security.addItem("Open network", "NOPASS")
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText("Hotspot password")
        self.hidden_network = QCheckBox("Hidden network")
        self.detect_network = TooltipIconButton("reset", "Refresh laptop network adapters", role="subtle")
        self.open_hotspot_settings = TooltipIconButton("settings", "Open Windows Mobile Hotspot settings", role="subtle")
        net.addWidget(self._field_label("Respondent network"), 1, 0)
        net.addWidget(self._field_label("Access method"), 1, 1)
        net.addWidget(self._field_label("Internet access"), 1, 2)
        net.addWidget(self.network_mode, 2, 0)
        net.addWidget(self.access_mode, 2, 1)
        net.addWidget(self.internet_mode, 2, 2)
        net.addWidget(self._field_label("Hotspot name (SSID)"), 3, 0)
        net.addWidget(self._field_label("Security"), 3, 1)
        net.addWidget(self._field_label("Password"), 3, 2)
        net.addWidget(self.ssid, 4, 0)
        net.addWidget(self.security, 4, 1)
        net.addWidget(self.password, 4, 2)
        net.addWidget(self.hidden_network, 5, 0)
        net.addWidget(self.detect_network, 5, 1)
        net.addWidget(self.open_hotspot_settings, 5, 2)
        network_note = QLabel(
            "Default workflow: scan the Wi-Fi QR and join the laptop hotspot. The phone may then offer a network-login page when the port-80 redirector and app-owned DNS responder pass their local checks. Phone behavior varies, so the verified direct-IP Survey Form QR remains the primary manual address."
        )
        network_note.setObjectName("server_note")
        network_note.setWordWrap(True)
        net.addWidget(network_note, 6, 0, 1, 3)
        layout.addWidget(network_card)

        scanner_card = self._card("CSM Sheet Scanner Remote and processing")
        scanner = scanner_card.layout()
        self.scanner_remote_enabled = QCheckBox("Enable the authenticated CSM Sheet Scanner Remote")
        self.scanner_intake_enabled = QCheckBox("Accept scanner image uploads and finalized MRS responses")
        self.scanner_store_preview = QCheckBox("Archive the corrected scanner preview when included")
        self.scanner_endpoint = self._readonly_line()
        self.copy_scanner_endpoint = TooltipIconButton("copy", "Copy CSM Sheet Scanner Remote address", button_size=36, icon_size=19)
        self.open_scanner_remote = TooltipIconButton("open", "Open CSM Sheet Scanner Remote", button_size=36, icon_size=19)
        self.scanner_processing_slider = QSlider(Qt.Orientation.Horizontal)
        self.scanner_processing_slider.setRange(1, 10)
        self.scanner_processing_slider.setSingleStep(1)
        self.scanner_processing_slider.setPageStep(1)
        self.scanner_processing_slider.setValue(1)
        self.scanner_processing_slider.setToolTip("Maximum image-processing jobs that may run at the same time")
        self.scanner_processing_value = QLabel("1")
        self.scanner_processing_value.setObjectName("scanner_processing_value")
        self.scanner_processing_value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scanner_processing_value.setFixedSize(42, 32)
        self.scanner_load_label = QLabel("Cool · Recommended")
        self.scanner_load_label.setObjectName("scanner_load_label")
        self.scanner_load_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        scanner.addWidget(self.scanner_remote_enabled, 1, 0, 1, 3)
        scanner.addWidget(self.scanner_intake_enabled, 2, 0, 1, 3)
        scanner.addWidget(self.scanner_store_preview, 3, 0, 1, 3)
        scanner.addWidget(self._field_label("Scanner Remote address"), 4, 0, 1, 3)
        scanner.addWidget(self.scanner_endpoint, 5, 0, 1, 2)
        scanner_endpoint_actions = QHBoxLayout()
        scanner_endpoint_actions.setSpacing(8)
        scanner_endpoint_actions.addWidget(self.copy_scanner_endpoint)
        scanner_endpoint_actions.addWidget(self.open_scanner_remote)
        scanner.addLayout(scanner_endpoint_actions, 5, 2)
        scanner.addWidget(self._field_label("Maximum simultaneous scan processing"), 6, 0, 1, 2)
        scanner.addWidget(self.scanner_load_label, 6, 2)
        scanner.addWidget(self.scanner_processing_slider, 7, 0, 1, 2)
        scanner.addWidget(self.scanner_processing_value, 7, 2)
        scanner_note = QLabel(
            "One to ten image jobs may process simultaneously. Additional uploads remain in a first-in, first-out queue and the Scanner Remote displays each operator's queue position. A limit of 1 is recommended for ordinary school laptops; 10 is very hot and not recommended."
        )
        scanner_note.setObjectName("server_note")
        scanner_note.setWordWrap(True)
        scanner.addWidget(scanner_note, 8, 0, 1, 3)
        layout.addWidget(scanner_card)

        operator_card = self._card("Scanner Operator accounts")
        operator = operator_card.layout()
        self.operator_combo = NoWheelComboBox()
        self.operator_combo.addItem("Create new Scanner Operator", "")
        self.operator_display_name = QLineEdit()
        self.operator_display_name.setPlaceholderText("Operator display name")
        self.operator_username = QLineEdit()
        self.operator_username.setPlaceholderText("scanner01")
        self.operator_password = QLineEdit()
        self.operator_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.operator_password.setPlaceholderText("New password or leave blank to retain")
        self.operator_enabled = QCheckBox("Account enabled")
        self.operator_enabled.setChecked(True)
        self.new_operator_button = TooltipIconButton("add", "Create a new Scanner Operator", button_size=36, icon_size=19)
        self.save_operator_button = TooltipIconButton("save", "Save Scanner Operator account", icon_color=theme.SUCCESS, role="success", button_size=36, icon_size=19)
        self.archive_operator_button = TooltipIconButton("delete", "Disable and archive selected Scanner Operator", icon_color=theme.DANGER, role="danger", button_size=36, icon_size=19)
        operator.addWidget(self._field_label("Registered account"), 1, 0, 1, 2)
        operator.addWidget(self.operator_combo, 2, 0, 1, 2)
        operator.addWidget(self.new_operator_button, 2, 2)
        operator.addWidget(self._field_label("Display name"), 3, 0)
        operator.addWidget(self._field_label("Username"), 3, 1)
        operator.addWidget(self._field_label("Password or PIN"), 3, 2)
        operator.addWidget(self.operator_display_name, 4, 0)
        operator.addWidget(self.operator_username, 4, 1)
        operator.addWidget(self.operator_password, 4, 2)
        operator.addWidget(self.operator_enabled, 5, 0)
        operator_actions = QHBoxLayout()
        operator_actions.setSpacing(8)
        operator_actions.addStretch(1)
        operator_actions.addWidget(self.archive_operator_button)
        operator_actions.addWidget(self.save_operator_button)
        operator.addLayout(operator_actions, 5, 1, 1, 2)
        operator_note = QLabel(
            "Credentials are stored as salted password hashes. The signed-in account identity is attached by the server to every finalized scanned response and cannot be supplied as an editable browser field."
        )
        operator_note.setObjectName("server_note")
        operator_note.setWordWrap(True)
        operator.addWidget(operator_note, 6, 0, 1, 3)
        layout.addWidget(operator_card)

        access_card = self._card("Respondent QR access")
        access_card.setMaximumWidth(1080)
        access = access_card.layout()

        self.wifi_qr = self._qr_label("Enter hotspot details", size=154)
        self.survey_qr = self._qr_label("Start the server", size=154)
        self.direct_qr = self._qr_label("Start the server", size=154)

        # Root URLs remain available to the controller and diagnostics but are
        # not repeated as wide, full-row fields in the compact respondent card.
        self.direct_url = self._readonly_line()
        self.named_url = self._readonly_line()
        self.direct_url.hide()
        self.named_url.hide()
        self.access_url = self._readonly_line()
        self.direct_access_url = self._readonly_line()

        self.copy_access = TooltipIconButton("copy", "Copy verified configured-name address", button_size=36, icon_size=19)
        self.open_access = TooltipIconButton("open", "Open verified configured-name address", button_size=36, icon_size=19)
        self.copy_direct_access = TooltipIconButton("copy", "Copy primary direct-IP address", button_size=36, icon_size=19)
        self.open_direct_access = TooltipIconButton("open", "Open primary direct-IP address", button_size=36, icon_size=19)
        self.save_wifi = TooltipIconButton("save", "Save laptop-hotspot Wi-Fi QR as PNG", button_size=36, icon_size=19)
        self.save_survey = TooltipIconButton("save", "Save verified configured-name QR as PNG", button_size=36, icon_size=19)
        self.save_direct = TooltipIconButton("save", "Save primary direct-IP QR as PNG", button_size=36, icon_size=19)
        self.save_sheet = TooltipIconButton("qr", "Save the respondent access sheet", button_size=36, icon_size=19)
        self.regenerate_access = TooltipIconButton("key", "Regenerate the temporary Survey Form access key", button_size=36, icon_size=19)

        header_actions = QHBoxLayout()
        header_actions.setSpacing(8)
        header_actions.addStretch(1)
        header_actions.addWidget(self.regenerate_access)
        header_actions.addWidget(self.save_sheet)
        access.addLayout(header_actions, 1, 0, 1, 4)

        tiles = QWidget()
        tiles.setObjectName("server_tiles")
        tiles_layout = QGridLayout(tiles)
        tiles_layout.setContentsMargins(0, 2, 0, 0)
        tiles_layout.setHorizontalSpacing(14)
        tiles_layout.setVerticalSpacing(0)

        wifi_actions = QHBoxLayout()
        wifi_actions.setSpacing(7)
        wifi_actions.addStretch(1)
        wifi_actions.addWidget(self.save_wifi)
        wifi_actions.addStretch(1)
        wifi_tile = self._qr_tile(
            "Connect to respondent network",
            "Wi-Fi QR",
            self.wifi_qr,
            None,
            wifi_actions,
        )

        named_actions = QHBoxLayout()
        named_actions.setSpacing(7)
        named_actions.addStretch(1)
        named_actions.addWidget(self.copy_access)
        named_actions.addWidget(self.open_access)
        named_actions.addWidget(self.save_survey)
        named_actions.addStretch(1)
        named_tile = self._qr_tile(
            "Configured school name",
            "Shown only after local DNS verification",
            self.survey_qr,
            self.access_url,
            named_actions,
        )

        direct_actions = QHBoxLayout()
        direct_actions.setSpacing(7)
        direct_actions.addStretch(1)
        direct_actions.addWidget(self.copy_direct_access)
        direct_actions.addWidget(self.open_direct_access)
        direct_actions.addWidget(self.save_direct)
        direct_actions.addStretch(1)
        direct_tile = self._qr_tile(
            "Survey Form (direct IP)",
            "Primary verified manual address",
            self.direct_qr,
            self.direct_access_url,
            direct_actions,
        )

        tiles_layout.addWidget(wifi_tile, 0, 0)
        tiles_layout.addWidget(direct_tile, 0, 1)
        tiles_layout.addWidget(named_tile, 0, 2)
        tiles_layout.setColumnStretch(0, 1)
        tiles_layout.setColumnStretch(1, 1)
        tiles_layout.setColumnStretch(2, 1)
        access.addWidget(tiles, 2, 0, 1, 4)

        access_note = QLabel(
            "Scan the hotspot QR first. If the phone does not offer a network-login page, scan the direct-IP Survey Form QR. A configured school-name QR appears only while the app-owned local DNS responder is running and has passed its self-test."
        )
        access_note.setWordWrap(True)
        access_note.setObjectName("server_note")
        access.addWidget(access_note, 3, 0, 1, 4)
        layout.addWidget(access_card, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addStretch(1)
        scroll.setWidget(content)
        outer.addWidget(scroll)
        self.prompt = OverlayPrompt(self)

        self.start_button.clicked.connect(self.start_server)
        self.stop_button.clicked.connect(self.controller.stop_async)
        self.apply_button.clicked.connect(lambda: self.apply_settings())
        self.detect_network.clicked.connect(self._detect_network)
        self.open_hotspot_settings.clicked.connect(self._open_hotspot_settings)
        self.copy_access.clicked.connect(lambda: QApplication.clipboard().setText(self.access_url.text()))
        self.open_access.clicked.connect(self._open_configured_access)
        self.copy_direct_access.clicked.connect(lambda: QApplication.clipboard().setText(self.direct_access_url.text()))
        self.open_direct_access.clicked.connect(self._open_direct_access)
        self.save_wifi.clicked.connect(lambda: self._save_qr(self._wifi_payload, "school_csm_wifi_qr.png"))
        self.save_survey.clicked.connect(lambda: self._save_qr(self._named_access_url, "school_csm_configured_address_qr.png"))
        self.save_direct.clicked.connect(lambda: self._save_qr(self._direct_access_url, "school_csm_direct_ip_qr.png"))
        self.save_sheet.clicked.connect(self._save_access_sheet)
        self.regenerate_access.clicked.connect(self._regenerate_access_key)
        self.copy_scanner_endpoint.clicked.connect(lambda: QApplication.clipboard().setText(self.scanner_endpoint.text()))
        self.open_scanner_remote.clicked.connect(self._open_scanner_remote)
        self.scanner_processing_slider.valueChanged.connect(self._scanner_limit_changed)
        self.background_startup_switch.toggled.connect(self._background_startup_toggled)
        self.scanner_remote_enabled.toggled.connect(lambda _checked: self._sync_scanner_status())
        self.scanner_intake_enabled.toggled.connect(lambda _checked: self._sync_scanner_status())
        self.operator_combo.currentIndexChanged.connect(self._operator_selected)
        self.new_operator_button.clicked.connect(self._new_scanner_operator)
        self.save_operator_button.clicked.connect(self._save_scanner_operator)
        self.archive_operator_button.clicked.connect(self._archive_scanner_operator)
        self.controller.status_changed.connect(self._server_status_changed)
        self.controller.survey_status_changed.connect(self._survey_status_changed)
        self.controller.urls_changed.connect(self._urls_changed)
        self.controller.response_count_changed.connect(lambda count: self.response_count[1].setText(str(count)))
        self.controller.scanner_response_count_changed.connect(lambda count: self.scanner_status[1].setToolTip(f"{count} scanned hardcopy response(s) received this server session"))
        self.controller.active_sessions_changed.connect(lambda count: self.active_sessions[1].setText(str(count)))
        self.controller.portal_status_changed.connect(self._portal_status_changed)
        self.controller.address_status_changed.connect(self._address_status_changed)
        self._load_settings(self.controller.settings())
        self._urls_changed(*self.controller.urls())
        self._apply_styles()

    def start_server(self) -> None:
        self.apply_settings(on_success=self._start_server_after_settings)

    def _start_server_after_settings(self) -> None:
        background_enabled = bool(
            self.controller.settings().get("background_server_startup_enabled", False)
        )
        started = self.controller.start_async(
            self.port_spin.value(),
            8080,
            configure_firewall=not background_enabled,
        )
        if not started and not self.controller.running:
            self.status_detail.setText("A server start or stop operation is already in progress.")

    def apply_settings(
        self,
        *,
        high_limit_confirmed: bool = False,
        on_success=None,
    ) -> bool:
        requested_limit = self.scanner_processing_slider.value()
        current_limit = int(self.controller.settings().get("scanner_processing_limit") or 1)
        if requested_limit == 10 and current_limit != 10 and not high_limit_confirmed:
            self.prompt.show_prompt(
                "Very high scanner processing limit",
                (
                    "Processing 10 scanner jobs at once may overload this computer, "
                    "slow the Control Center, and delay Browser Survey submissions."
                ),
                detail="Use this setting only on a computer that has been tested under the expected workload.",
                accept_tooltip="Apply limit of 10",
                cancel_tooltip="Keep the current limit",
                marker="!",
                destructive=True,
                on_accept=lambda: self.apply_settings(
                    high_limit_confirmed=True,
                    on_success=on_success,
                ),
                on_reject=lambda: self.scanner_processing_slider.setValue(current_limit),
            )
            return False
        try:
            self.controller.set_active_mode(str(self.mode_combo.currentData() or "onsite"))
            self.controller.set_preferred_port(self.port_spin.value())
            self.controller.set_server_ip(str(self.server_ip_combo.currentData() or ""))
            self.controller.set_survey_date(self.date_edit.date().toString("yyyy-MM-dd"))
            normalized = self.controller.set_school_identifier(self.identifier.text())
            self.identifier.setText(normalized)
            self.controller.set_session_duration(self.session_minutes.value() * 60)
            self.controller.set_network_mode(str(self.network_mode.currentData() or "hotspot"))
            self.controller.set_access_mode(str(self.access_mode.currentData() or "captive_portal"))
            self.controller.set_hotspot_internet_mode(str(self.internet_mode.currentData() or "survey_only"))
            self.controller.set_network_profile(
                ssid=self.ssid.text(),
                security=str(self.security.currentData() or "WPA"),
                password=self.password.text(),
                hidden=self.hidden_network.isChecked(),
            )
            self.controller.set_scanner_remote_enabled(self.scanner_remote_enabled.isChecked())
            self.controller.set_scanner_intake_enabled(self.scanner_intake_enabled.isChecked())
            self.controller.set_scanner_processing_limit(requested_limit)
            self.controller.set_scanner_store_preview(self.scanner_store_preview.isChecked())
            self.controller.set_survey_status(str(self.status_combo.currentData() or "offline"), self.status_message.text())
        except ValueError as exc:
            self.status_detail.setText(str(exc))
            return False
        self._render_wifi_qr()
        self._sync_scanner_status()
        if on_success is not None:
            on_success()
        return True

    def _load_settings(self, settings: dict[str, Any]) -> None:
        self.mode_combo.setCurrentIndex(max(0, self.mode_combo.findData(settings.get("active_mode", "onsite"))))
        parsed = QDate.fromString(str(settings.get("survey_date") or ""), "yyyy-MM-dd")
        if parsed.isValid():
            self.date_edit.setDate(parsed)
        self.port_spin.setValue(int(settings.get("preferred_port") or 80))
        self._refresh_server_addresses(str(settings.get("server_ip") or settings.get("local_ip") or ""))
        self.status_combo.setCurrentIndex(max(0, self.status_combo.findData(settings.get("survey_status", "offline"))))
        self.status_message.setText(str(settings.get("status_message") or ""))
        self.identifier.setText(str(settings.get("school_identifier") or "school-csm"))
        self.session_minutes.setValue(max(1, int(settings.get("session_duration_seconds") or 300) // 60))
        self.network_mode.setCurrentIndex(max(0, self.network_mode.findData(settings.get("network_mode", "hotspot"))))
        self.access_mode.setCurrentIndex(max(0, self.access_mode.findData(settings.get("access_mode", "captive_portal"))))
        self.internet_mode.setCurrentIndex(max(0, self.internet_mode.findData(settings.get("hotspot_internet_mode", "survey_only"))))
        self.ssid.setText(str(settings.get("network_ssid") or "School-CSM"))
        self.security.setCurrentIndex(max(0, self.security.findData(settings.get("network_security", "WPA"))))
        self.password.setText(str(settings.get("network_password") or ""))
        self.hidden_network.setChecked(bool(settings.get("network_hidden")))
        self.scanner_remote_enabled.setChecked(bool(settings.get("scanner_remote_enabled", True)))
        self.scanner_intake_enabled.setChecked(bool(settings.get("scanner_intake_enabled", True)))
        self.scanner_store_preview.setChecked(bool(settings.get("scanner_store_preview", True)))
        self.scanner_processing_slider.setValue(max(1, min(10, int(settings.get("scanner_processing_limit") or 1))))
        self.sync_background_startup(
            bool(settings.get("background_server_startup_enabled", False))
        )
        self._scanner_limit_changed(self.scanner_processing_slider.value())
        self._load_scanner_operators()
        self._sync_scanner_status()
        self._survey_status_changed(str(settings.get("survey_status") or "offline"), "")
        self._portal_status_changed(dict(settings.get("captive_portal") or {}))
        self._address_status_changed(dict(settings.get("access_capability") or {}))
        self._render_wifi_qr()

    def sync_background_startup(self, enabled: bool) -> None:
        """Reflect persisted/actual startup state without re-requesting a change."""

        blocker = QSignalBlocker(self.background_startup_switch)
        self.background_startup_switch.setChecked(bool(enabled))
        del blocker
        self._render_background_startup(bool(enabled))

    def set_background_startup_enabled(
        self,
        enabled: bool,
        detail: str = "",
    ) -> None:
        """Public lifecycle hook for safely reconciling switch/tray state."""

        self.sync_background_startup(bool(enabled))
        message = str(detail or "").strip()
        self.background_startup_state.setToolTip(message)
        if message:
            self.status_detail.setText(message)

    def _background_startup_toggled(self, enabled: bool) -> None:
        self._render_background_startup(bool(enabled))
        self.background_startup_requested.emit(bool(enabled))

    def _render_background_startup(self, enabled: bool) -> None:
        self.background_startup_switch.setText("ON" if enabled else "OFF")
        self.background_startup_state.setText("Enabled" if enabled else "Disabled")
        self.background_startup_state.setProperty("enabledState", bool(enabled))
        self.background_startup_state.style().unpolish(self.background_startup_state)
        self.background_startup_state.style().polish(self.background_startup_state)

    def _server_status_changed(self, status: str, message: str) -> None:
        labels = {
            "online": "Running",
            "offline": "Stopped",
            "starting": "Starting",
            "stopping": "Stopping",
            "error": "Error",
        }
        self.server_status[1].setText(labels.get(status, status.title()))
        self.status_detail.setText(message)
        running = status == "online"
        busy = status in {"starting", "stopping"}
        self.start_button.setEnabled(not running and not busy)
        self.stop_button.setEnabled(running and not busy)
        self.apply_button.setEnabled(not busy)
        self.detect_network.setEnabled(not busy)
        network_editable = not running and not busy
        for widget in (
            self.port_spin,
            self.server_ip_combo,
            self.identifier,
            self.network_mode,
            self.access_mode,
            self.internet_mode,
        ):
            widget.setEnabled(network_editable)

    def _survey_status_changed(self, status: str, message: str) -> None:
        labels = {"online": "Online", "offline": "Offline", "maintenance": "Under Maintenance"}
        self.form_status[1].setText(labels.get(status, status.title()))
        self.form_status[1].setProperty("state", status)
        self.form_status[1].style().unpolish(self.form_status[1])
        self.form_status[1].style().polish(self.form_status[1])
        if message:
            self.status_detail.setText(message)

    def _urls_changed(self, direct: str, named: str, direct_access: str, named_access: str) -> None:
        self.direct_url.setText(direct)
        self.named_url.setText(named)
        self.access_url.setText(named_access)
        self.direct_access_url.setText(direct_access)
        self._named_access_url = named_access
        self._direct_access_url = direct_access
        self.scanner_endpoint.setText((direct.rstrip("/") + "/scanner") if direct else "")
        self._render_qr(self.survey_qr, named_access, "Local DNS not verified")
        self._render_qr(self.direct_qr, direct_access, "Start the server")
        named_enabled = bool(named_access)
        for button in (self.copy_access, self.open_access, self.save_survey):
            button.setEnabled(named_enabled)
        direct_enabled = bool(direct_access)
        for button in (self.copy_direct_access, self.open_direct_access, self.save_direct):
            button.setEnabled(direct_enabled)

    def _address_status_changed(self, capability: dict[str, Any]) -> None:
        self._access_capability = dict(capability or {})
        if not self.controller.running:
            return
        direct_reason = str(capability.get("direct_reason") or "")
        named_reason = str(capability.get("named_reason") or "")
        parts = [part for part in (direct_reason, named_reason) if part]
        if bool(capability.get("restart_required")):
            restart = self.controller.network_restart_state()
            detail = str(restart.get("detail") or "Restart the local survey server to apply pending network changes.")
            parts.append(detail)
        if parts:
            self.status_detail.setText(" ".join(parts))

    def _detect_network(self) -> None:
        self._refresh_server_addresses(str(self.server_ip_combo.currentData() or ""), refresh=True)
        hotspot = self.controller.hotspot_status()
        if hotspot.get("active"):
            self.status_detail.setText(str(hotspot.get("detail") or "Laptop hotspot detected."))
        else:
            self.status_detail.setText("Laptop hotspot was not detected. Open Windows Hotspot settings, turn it on, then refresh adapters.")

    def _open_hotspot_settings(self) -> None:
        QDesktopServices.openUrl(QUrl("ms-settings:network-mobilehotspot"))
        self.status_detail.setText("Windows Mobile Hotspot settings opened. Turn on the hotspot, then click Refresh adapters.")

    def _portal_status_changed(self, status: dict[str, Any]) -> None:
        label = str(status.get("status") or "Stopped")
        self.portal_status[1].setText(label)
        state = "online" if bool(status.get("automatic_open")) else (
            "maintenance"
            if label in {"Partial", "Hotspot required", "Hotspot IP mismatch"}
            else "offline"
        )
        self.portal_status[1].setProperty("state", state)
        self.portal_status[1].style().unpolish(self.portal_status[1])
        self.portal_status[1].style().polish(self.portal_status[1])
        detail = str(status.get("detail") or "")
        if detail and self.controller.running:
            self.status_detail.setText(detail)

    def _refresh_server_addresses(self, preferred: str = "", *, refresh: bool = False) -> None:
        current = str(preferred or self.server_ip_combo.currentData() or "")
        self.server_ip_combo.blockSignals(True)
        self.server_ip_combo.clear()
        candidates = self.controller.available_server_addresses(refresh=refresh)
        for label, address in candidates:
            self.server_ip_combo.addItem(label, address)
        if current and self.server_ip_combo.findData(current) < 0:
            self.server_ip_combo.addItem(
                f"{current} — Saved address (not currently detected)",
                current,
            )
        if not candidates:
            if self.server_ip_combo.findData("127.0.0.1") < 0:
                self.server_ip_combo.addItem("127.0.0.1 — This laptop only", "127.0.0.1")
        index = self.server_ip_combo.findData(current)
        self.server_ip_combo.setCurrentIndex(index if index >= 0 else 0)
        self.server_ip_combo.blockSignals(False)

    def _open_configured_access(self) -> None:
        value = self.access_url.text().strip()
        if not value:
            reason = str(self._access_capability.get("named_reason") or "Start the server before opening the Survey Form.")
            self.status_detail.setText(reason)
            return
        QDesktopServices.openUrl(QUrl(value))

    def _open_direct_access(self) -> None:
        value = self.direct_access_url.text().strip()
        if not value:
            self.status_detail.setText("Start the server and wait for its direct-IP health check before opening the Survey Form.")
            return
        QDesktopServices.openUrl(QUrl(value))

    def _regenerate_access_key(self) -> None:
        self.controller.regenerate_public_access_key()
        self.status_detail.setText("The access key was regenerated. Previously generated Survey Form QR codes are now outdated.")

    def _open_scanner_remote(self) -> None:
        value = self.scanner_endpoint.text().strip()
        if not value:
            self.status_detail.setText("Start the server before opening the CSM Sheet Scanner Remote.")
            return
        QDesktopServices.openUrl(QUrl(value))

    def _scanner_limit_changed(self, value: int) -> None:
        limit = max(1, min(10, int(value)))
        stops = {
            1: ("#3B82F6", "Cool · Recommended"),
            2: ("#22A6B3", "Cool · Low load"),
            3: ("#22C55E", "Moderate"),
            4: ("#84CC16", "Moderate · Increased load"),
            5: ("#EAB308", "Warm · Increased load"),
            6: ("#F59E0B", "Warm · Heavy load"),
            7: ("#F97316", "Hot · Heavy load"),
            8: ("#EF6C33", "Hot · Very heavy load"),
            9: ("#EF4444", "Very hot"),
            10: ("#DC2626", "Very hot · Not recommended"),
        }
        color, label = stops[limit]
        self.scanner_processing_value.setText(str(limit))
        self.scanner_load_label.setText(label)
        self.scanner_processing_value.setStyleSheet(
            f"background: {color}; color: white; border: 1px solid {color}; border-radius: 8px; font-weight: 800;"
        )
        self.scanner_load_label.setStyleSheet(f"color: {color}; font-weight: 750;")
        self.scanner_processing_slider.setStyleSheet(
            f"""
            QSlider::groove:horizontal {{ height: 8px; background: #D8E1EA; border-radius: 4px; }}
            QSlider::sub-page:horizontal {{ background: {color}; border-radius: 4px; }}
            QSlider::add-page:horizontal {{ background: #D8E1EA; border-radius: 4px; }}
            QSlider::handle:horizontal {{ background: {color}; border: 2px solid white; width: 20px; height: 20px; margin: -7px 0; border-radius: 10px; }}
            """
        )

    def _load_scanner_operators(self, selected_user_id: str = "") -> None:
        selected = str(selected_user_id or self.operator_combo.currentData() or "")
        accounts = self.controller.scanner_operator_accounts()
        self.operator_combo.blockSignals(True)
        self.operator_combo.clear()
        self.operator_combo.addItem("Create new Scanner Operator", "")
        for account in accounts:
            user_id = str(account.get("user_id") or "")
            name = str(account.get("display_name") or account.get("username") or user_id)
            username = str(account.get("username") or "")
            archived = bool(account.get("archived"))
            enabled = bool(account.get("enabled", True)) and not archived
            state = "Active" if enabled else ("Archived" if archived else "Disabled")
            self.operator_combo.addItem(f"{name} ({username}) — {state}", user_id)
        index = self.operator_combo.findData(selected)
        self.operator_combo.setCurrentIndex(index if index >= 0 else 0)
        self.operator_combo.blockSignals(False)
        self._operator_selected(self.operator_combo.currentIndex())

    def _operator_selected(self, _index: int) -> None:
        user_id = str(self.operator_combo.currentData() or "")
        account = next(
            (item for item in self.controller.scanner_operator_accounts() if str(item.get("user_id") or "") == user_id),
            None,
        )
        if account is None:
            self.operator_display_name.clear()
            self.operator_username.clear()
            self.operator_password.clear()
            self.operator_enabled.setChecked(True)
            self.archive_operator_button.setEnabled(False)
            return
        self.operator_display_name.setText(str(account.get("display_name") or ""))
        self.operator_username.setText(str(account.get("username") or ""))
        self.operator_password.clear()
        self.operator_enabled.setChecked(bool(account.get("enabled", True)) and not bool(account.get("archived")))
        self.archive_operator_button.setEnabled(not bool(account.get("archived")))

    def _new_scanner_operator(self) -> None:
        self.operator_combo.setCurrentIndex(0)
        self.operator_display_name.clear()
        self.operator_username.clear()
        self.operator_password.clear()
        self.operator_enabled.setChecked(True)
        self.operator_display_name.setFocus()
        self.status_detail.setText("Enter the new Scanner Operator details. New accounts require a password of at least 6 characters.")

    def _save_scanner_operator(self) -> None:
        try:
            account = self.controller.save_scanner_operator(
                user_id=str(self.operator_combo.currentData() or ""),
                username=self.operator_username.text(),
                display_name=self.operator_display_name.text(),
                password=self.operator_password.text(),
                enabled=self.operator_enabled.isChecked(),
            )
        except (ValueError, KeyError) as exc:
            self.status_detail.setText(str(exc))
            return
        self.operator_password.clear()
        self._load_scanner_operators(str(account.get("user_id") or ""))
        self.status_detail.setText(f"Scanner Operator saved: {account.get('display_name') or account.get('username')}")

    def _archive_scanner_operator(self) -> None:
        user_id = str(self.operator_combo.currentData() or "")
        if not user_id:
            self.status_detail.setText("Select a Scanner Operator account to archive.")
            return
        self.prompt.show_prompt(
            "Archive Scanner Operator",
            "Disable and archive this Scanner Operator account?",
            detail="Existing survey-record attribution will be preserved.",
            accept_tooltip="Archive Scanner Operator",
            cancel_tooltip="Keep Scanner Operator active",
            marker="!",
            destructive=True,
            on_accept=lambda: self._archive_scanner_operator_confirmed(user_id),
        )

    def _archive_scanner_operator_confirmed(self, user_id: str) -> None:
        try:
            account = self.controller.archive_scanner_operator(user_id)
        except KeyError as exc:
            self.status_detail.setText(str(exc))
            return
        self._load_scanner_operators(str(account.get("user_id") or ""))
        self.status_detail.setText(f"Scanner Operator archived: {account.get('display_name') or account.get('username')}")

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        if hasattr(self, "prompt"):
            self.prompt.setGeometry(self.rect())
            if self.prompt.isVisible():
                self.prompt.raise_()

    def _sync_scanner_status(self) -> None:
        remote = self.scanner_remote_enabled.isChecked()
        intake = self.scanner_intake_enabled.isChecked()
        if remote and intake:
            label, state = "Available", "online"
        elif remote:
            label, state = "Intake Off", "maintenance"
        else:
            label, state = "Remote Off", "offline"
        self.scanner_status[1].setText(label)
        self.scanner_status[1].setProperty("state", state)
        self.scanner_status[1].style().unpolish(self.scanner_status[1])
        self.scanner_status[1].style().polish(self.scanner_status[1])

    def _render_wifi_qr(self) -> None:
        self.controller.set_network_profile(
            ssid=self.ssid.text(),
            security=str(self.security.currentData() or "WPA"),
            password=self.password.text(),
            hidden=self.hidden_network.isChecked(),
        )
        self._wifi_payload = self.controller.wifi_qr_payload()
        fallback = "Enter hotspot name" if not self.ssid.text().strip() else "Enter hotspot password"
        if str(self.security.currentData() or "WPA") == "NOPASS":
            fallback = "Enter hotspot name"
        self._render_qr(self.wifi_qr, self._wifi_payload, fallback)

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
            target = max(96, min(label.width() - 12, label.height() - 12))
            label.setPixmap(pixmap.scaled(target, target, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        except Exception:
            label.setPixmap(QPixmap())
            label.setText("QR generation unavailable")

    def _save_qr(self, value: str, suggested_name: str) -> None:
        if not value:
            self.status_detail.setText("There is no QR value to save yet.")
            return
        filename, _ = QFileDialog.getSaveFileName(self, "Save QR code", str(Path.home() / suggested_name), "PNG image (*.png)")
        if not filename:
            return
        if not filename.casefold().endswith(".png"):
            filename += ".png"
        import qrcode

        qrcode.make(value).save(filename, format="PNG")
        self.status_detail.setText(f"QR image saved: {filename}")

    def _save_access_sheet(self) -> None:
        if not self._wifi_payload or not self._direct_access_url:
            self.status_detail.setText("Enter hotspot details and start the server before saving an access sheet.")
            return
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Save respondent access sheet",
            str(Path.home() / "school_csm_respondent_access.png"),
            "PNG image (*.png)",
        )
        if not filename:
            return
        if not filename.casefold().endswith(".png"):
            filename += ".png"
        import qrcode
        from PIL import Image, ImageDraw, ImageFont

        wifi = qrcode.make(self._wifi_payload).convert("RGB").resize((460, 460))
        named = (
            qrcode.make(self._named_access_url).convert("RGB").resize((460, 460))
            if self._named_access_url
            else None
        )
        direct = qrcode.make(self._direct_access_url).convert("RGB").resize((460, 460))
        canvas = Image.new("RGB", (1580, 760), "white")
        draw = ImageDraw.Draw(canvas)
        font = ImageFont.load_default()
        draw.text((50, 35), "SCHOOL CSM CONTROL CENTER — RESPONDENT ACCESS", fill="black", font=font)
        draw.text((95, 90), "STEP 1: CONNECT TO RESPONDENT NETWORK", fill="black", font=font)
        draw.text((590, 90), "OPTIONAL VERIFIED SCHOOL NAME", fill="black", font=font)
        draw.text((1110, 90), "STEP 2: PRIMARY DIRECT-IP ADDRESS", fill="black", font=font)
        canvas.paste(wifi, (50, 130))
        if named is not None:
            canvas.paste(named, (560, 130))
        else:
            draw.rounded_rectangle((560, 130, 1020, 590), radius=20, outline="#94A3B8", width=4)
            draw.multiline_text(
                (635, 330),
                "NOT ADVERTISED\nLOCAL DNS IS NOT VERIFIED",
                fill="#475569",
                font=font,
                spacing=10,
                align="center",
            )
        canvas.paste(direct, (1070, 130))
        draw.text((50, 625), f"Network: {self.ssid.text() or 'Selected network'}", fill="black", font=font)
        draw.text((560, 625), self._named_access_url or "Hidden until local DNS verification passes", fill="black", font=font)
        draw.text((1070, 625), self._direct_access_url, fill="black", font=font)
        draw.text((50, 680), "After joining, use the direct-IP QR if no network-login page appears.", fill="black", font=font)
        draw.text((50, 710), "Automatic captive-portal opening and configured school-name access are device-dependent.", fill="black", font=font)
        canvas.save(filename, "PNG")
        self.status_detail.setText(f"Respondent access sheet saved: {filename}")

    @staticmethod
    def _qr_tile(
        title: str,
        subtitle: str,
        qr_label: QLabel,
        address_line: QLineEdit | None,
        action_layout: QHBoxLayout,
    ) -> QFrame:
        tile = QFrame()
        tile.setObjectName("server_qr_tile")
        tile.setMinimumWidth(250)
        tile.setMaximumWidth(320)
        box = QVBoxLayout(tile)
        box.setContentsMargins(12, 11, 12, 11)
        box.setSpacing(7)

        title_label = QLabel(title)
        title_label.setObjectName("server_qr_title")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle_label = QLabel(subtitle)
        subtitle_label.setObjectName("server_qr_subtitle")
        subtitle_label.setWordWrap(True)
        subtitle_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        box.addWidget(title_label)
        box.addWidget(subtitle_label)
        box.addWidget(qr_label, 0, Qt.AlignmentFlag.AlignHCenter)
        if address_line is not None:
            address_line.setMinimumWidth(0)
            address_line.setMaximumWidth(292)
            address_line.setToolTip("The complete address is available here and through the copy button.")
            box.addWidget(address_line)
        box.addLayout(action_layout)
        return tile

    @staticmethod
    def _readonly_line() -> QLineEdit:
        line = QLineEdit()
        line.setReadOnly(True)
        line.setPlaceholderText("Server is stopped")
        return line

    @staticmethod
    def _qr_label(fallback: str, *, size: int = 170) -> QLabel:
        label = QLabel(fallback)
        label.setObjectName("server_qr")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setFixedSize(size, size)
        return label

    @staticmethod
    def _field_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("server_card_label")
        return label

    @staticmethod
    def _metric(label_text: str, value_text: str) -> tuple[QWidget, QLabel]:
        box = QWidget()
        box.setObjectName("server_metric_box")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        label = QLabel(label_text)
        label.setObjectName("server_card_label")
        value = QLabel(value_text)
        value.setObjectName("server_metric")
        layout.addWidget(label)
        layout.addWidget(value)
        return box, value

    @staticmethod
    def _card(heading: str) -> QFrame:
        card = QFrame()
        card.setObjectName("server_card")
        layout = QGridLayout(card)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setHorizontalSpacing(16)
        layout.setVerticalSpacing(10)
        title = QLabel(heading)
        title.setObjectName("server_section_title")
        layout.addWidget(title, 0, 0, 1, 4)
        return card

    def _apply_styles(self) -> None:
        self.setStyleSheet(
            f"""
            QWidget#survey_server_board {{ background: {theme.WINDOW_BG_ALT}; }}
            QScrollArea#server_scroll {{ background: transparent; border: none; }}
            QScrollArea#server_scroll > QWidget > QWidget {{ background: transparent; }}
            QWidget#server_content, QWidget#server_metric_box, QWidget#server_tiles {{ background: transparent; }}
            QLabel#server_title {{ color: {theme.TEXT_PRIMARY}; font-size: 28px; font-weight: 800; }}
            QLabel#server_subtitle {{ color: {theme.TEXT_SECONDARY}; font-size: 14px; }}
            QFrame#server_card {{ background: {theme.PANEL_BG}; border: 1px solid {theme.CARD_BORDER}; border-radius: 13px; }}
            QLabel#server_section_title {{ color: {theme.TEXT_PRIMARY}; font-size: 17px; font-weight: 750; padding-bottom: 4px; }}
            QLabel#server_card_label {{ color: {theme.TEXT_MUTED}; font-size: 12px; font-weight: 650; }}
            QLabel#server_metric {{ color: {theme.TEXT_PRIMARY}; font-size: 22px; font-weight: 800; }}
            QLabel#server_metric[state="online"] {{ color: {theme.SUCCESS}; }}
            QLabel#server_metric[state="offline"] {{ color: {theme.DANGER}; }}
            QLabel#server_metric[state="maintenance"] {{ color: {theme.WARNING}; }}
            QLabel#server_note {{ color: {theme.TEXT_SECONDARY}; font-size: 13px; }}
            QLabel#background_startup_title {{ color: {theme.TEXT_PRIMARY}; font-size: 14px; font-weight: 750; }}
            QLabel#background_startup_state {{ color: {theme.TEXT_MUTED}; font-size: 12px; font-weight: 750; }}
            QLabel#background_startup_state[enabledState="true"] {{ color: {theme.SUCCESS}; }}
            QCheckBox#background_startup_switch {{ color: {theme.TEXT_SECONDARY}; background: {theme.CARD_BG}; border: 1px solid {theme.CARD_BORDER}; border-radius: 18px; padding-left: 10px; spacing: 7px; font-size: 11px; font-weight: 900; }}
            QCheckBox#background_startup_switch:hover {{ border-color: {theme.ACCENT_CYAN}; }}
            QCheckBox#background_startup_switch:checked {{ color: #031B20; background: {theme.SUCCESS}; border-color: {theme.SUCCESS}; }}
            QCheckBox#background_startup_switch::indicator {{ width: 16px; height: 16px; border: 1px solid {theme.CARD_BORDER}; border-radius: 8px; background: {theme.TEXT_MUTED}; }}
            QCheckBox#background_startup_switch::indicator:checked {{ border-color: #FFFFFF; background: #FFFFFF; }}
            QLabel#server_qr {{ background: white; color: #425466; border: 8px solid white; border-radius: 12px; }}
            QLineEdit, QComboBox, QDateEdit, QSpinBox {{ min-height: 40px; color: {theme.TEXT_PRIMARY}; background: {theme.CARD_BG}; border: 1px solid {theme.CARD_BORDER}; border-radius: 8px; padding: 4px 10px; }}
            QCheckBox {{ color: {theme.TEXT_PRIMARY}; background: transparent; spacing: 8px; }}
            QFrame#server_qr_tile {{ background: {theme.CARD_BG}; border: 1px solid {theme.CARD_BORDER}; border-radius: 10px; }}
            QLabel#server_qr_title {{ color: {theme.TEXT_PRIMARY}; font-size: 14px; font-weight: 750; border: none; }}
            QLabel#server_qr_subtitle {{ color: {theme.TEXT_MUTED}; font-size: 11px; border: none; }}
            """
        )
