"""Windows notification-area controls for the Control Center."""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon, QWidget

from school_csm_control_center.app_identity import SHORT_APPLICATION_NAME


class ControlCenterSystemTray(QObject):
    """Own the notification-area icon and its small lifecycle menu.

    The class emits intent signals instead of operating the main window or
    server directly.  This keeps all exit and server lifecycle decisions in
    the main window, where close-event handling can be made race-safe.
    """

    open_requested = Signal()
    start_server_requested = Signal()
    stop_server_requested = Signal()
    open_survey_requested = Signal(str)
    connect_gateway_requested = Signal()
    disconnect_gateway_requested = Signal()
    open_internet_survey_requested = Signal(str)
    open_internet_scanner_requested = Signal(str)
    background_startup_toggled = Signal(bool)
    exit_requested = Signal()

    def __init__(
        self,
        icon: QIcon,
        parent: QWidget,
        *,
        available_override: bool | None = None,
        show_immediately: bool = True,
    ) -> None:
        super().__init__(parent)
        self._available = (
            QSystemTrayIcon.isSystemTrayAvailable()
            if available_override is None
            else bool(available_override)
        )
        self._direct_survey_url = ""
        self._internet_survey_url = ""
        self._internet_scanner_url = ""
        self._status = "offline"
        self._gateway_status = "not_configured"
        self._server_label = "Stopped"
        self._gateway_label = "Not Configured"

        self.icon = QSystemTrayIcon(icon, parent)
        self.icon.setObjectName("control_center_system_tray")
        self.menu = QMenu(parent)
        self.menu.setObjectName("control_center_tray_menu")

        self.open_action = QAction("Open Control Center", self.menu)
        self.open_action.setObjectName("tray_open_control_center")
        self.open_action.setStatusTip("Show and activate the Control Center window")
        self.open_action.triggered.connect(self.open_requested.emit)
        self.menu.addAction(self.open_action)
        self.menu.setDefaultAction(self.open_action)

        self.status_action = QAction("Survey Server: Stopped", self.menu)
        self.status_action.setObjectName("tray_server_status")
        self.status_action.setEnabled(False)
        self.menu.addAction(self.status_action)
        self.menu.addSeparator()

        self.start_action = QAction("Start Survey Server", self.menu)
        self.start_action.setObjectName("tray_start_server")
        self.start_action.setStatusTip("Start the saved Survey Server configuration")
        self.start_action.triggered.connect(self.start_server_requested.emit)
        self.menu.addAction(self.start_action)

        self.stop_action = QAction("Stop Survey Server", self.menu)
        self.stop_action.setObjectName("tray_stop_server")
        self.stop_action.setStatusTip("Stop the local Survey Server")
        self.stop_action.triggered.connect(self.stop_server_requested.emit)
        self.menu.addAction(self.stop_action)

        self.open_survey_action = QAction("Open Survey Form", self.menu)
        self.open_survey_action.setObjectName("tray_open_survey")
        self.open_survey_action.setStatusTip("Open the verified direct-IP Survey Form")
        self.open_survey_action.triggered.connect(self._emit_open_survey)
        self.menu.addAction(self.open_survey_action)
        self.menu.addSeparator()

        self.gateway_status_action = QAction("Internet Gateway: Not Configured", self.menu)
        self.gateway_status_action.setObjectName("tray_gateway_status")
        self.gateway_status_action.setEnabled(False)
        self.menu.addAction(self.gateway_status_action)
        self.connect_gateway_action = QAction("Connect Internet Gateway", self.menu)
        self.connect_gateway_action.setObjectName("tray_connect_gateway")
        self.connect_gateway_action.triggered.connect(self.connect_gateway_requested.emit)
        self.menu.addAction(self.connect_gateway_action)
        self.disconnect_gateway_action = QAction("Disconnect Internet Gateway", self.menu)
        self.disconnect_gateway_action.setObjectName("tray_disconnect_gateway")
        self.disconnect_gateway_action.triggered.connect(self.disconnect_gateway_requested.emit)
        self.menu.addAction(self.disconnect_gateway_action)
        self.open_internet_survey_action = QAction("Open Internet Survey", self.menu)
        self.open_internet_survey_action.setObjectName("tray_open_internet_survey")
        self.open_internet_survey_action.triggered.connect(self._emit_open_internet_survey)
        self.menu.addAction(self.open_internet_survey_action)
        self.open_internet_scanner_action = QAction("Open Internet Scanner", self.menu)
        self.open_internet_scanner_action.setObjectName("tray_open_internet_scanner")
        self.open_internet_scanner_action.triggered.connect(
            self._emit_open_internet_scanner
        )
        self.menu.addAction(self.open_internet_scanner_action)
        self.menu.addSeparator()

        self.background_action = QAction(
            "Start with Windows and run Survey Server in background",
            self.menu,
        )
        self.background_action.setObjectName("tray_background_startup")
        self.background_action.setStatusTip(
            "Start locally at Windows sign-in, then reconnect an authorized Internet Gateway after health verification"
        )
        self.background_action.setCheckable(True)
        self.background_action.toggled.connect(self.background_startup_toggled.emit)
        self.menu.addAction(self.background_action)
        self.menu.addSeparator()

        self.exit_action = QAction("Exit", self.menu)
        self.exit_action.setObjectName("tray_exit_control_center")
        self.exit_action.setStatusTip(
            "Stop the Survey Server and exit the Control Center"
        )
        self.exit_action.triggered.connect(self.exit_requested.emit)
        self.menu.addAction(self.exit_action)

        self.icon.setContextMenu(self.menu)
        self.icon.activated.connect(self._activated)
        self.set_server_state("offline", "Local survey server is stopped.")
        self.set_gateway_state("not_configured")
        if self._available and show_immediately:
            self.icon.show()

    @property
    def available(self) -> bool:
        return self._available

    @property
    def visible(self) -> bool:
        return bool(self.icon.isVisible())

    def show(self) -> None:
        if self._available:
            self.icon.show()

    def hide(self) -> None:
        self.icon.hide()

    def set_background_startup_checked(self, enabled: bool) -> None:
        previous = self.background_action.blockSignals(True)
        try:
            self.background_action.setChecked(bool(enabled))
        finally:
            self.background_action.blockSignals(previous)

    def set_direct_survey_url(self, url: str) -> None:
        self._direct_survey_url = str(url or "").strip()
        self.open_survey_action.setEnabled(
            self._status == "online" and bool(self._direct_survey_url)
        )

    def set_server_state(self, status: str, detail: str = "") -> None:
        state = str(status or "offline").strip().casefold()
        labels = {
            "online": "Running",
            "offline": "Stopped",
            "starting": "Starting",
            "stopping": "Stopping",
            "error": "Error",
        }
        label = labels.get(state, state.title() or "Stopped")
        busy = state in {"starting", "stopping"}
        self._status = state
        self._server_label = label
        self.status_action.setText(f"Survey Server: {label}")
        self.status_action.setStatusTip(str(detail or "").strip())
        self.start_action.setEnabled(not busy and state != "online")
        self.stop_action.setEnabled(not busy and state == "online")
        self.open_survey_action.setEnabled(
            state == "online" and bool(self._direct_survey_url)
        )
        self._refresh_tooltip()

    def set_gateway_state(
        self,
        status: str,
        detail: str = "",
        *,
        configured: bool = False,
        authorized: bool = False,
        survey_url: str = "",
        scanner_url: str = "",
        scanner_enabled: bool = True,
    ) -> None:
        state = str(status or "not_configured").strip().casefold()
        labels = {
            "not_configured": "Not Configured",
            "stopped": "Stopped",
            "connecting": "Connecting",
            "connected": "Connected",
            "disconnected": "Disconnected",
            "error": "Error",
            "revoked": "Revoked",
            "authorization_transferred": "Transferred",
        }
        self._gateway_status = state
        self._internet_survey_url = str(survey_url or "").strip()
        self._internet_scanner_url = str(scanner_url or "").strip()
        self._gateway_label = labels.get(state, state.title())
        self.gateway_status_action.setText(
            f"Internet Gateway: {self._gateway_label}"
        )
        self.gateway_status_action.setStatusTip(str(detail or ""))
        busy = state == "connecting"
        retired = state in {"revoked", "authorization_transferred"}
        self.connect_gateway_action.setEnabled(
            bool(configured and authorized and not busy and state != "connected" and not retired)
        )
        self.disconnect_gateway_action.setEnabled(state == "connected")
        self.open_internet_survey_action.setEnabled(
            state == "connected" and bool(self._internet_survey_url)
        )
        self.open_internet_scanner_action.setEnabled(
            state == "connected"
            and bool(scanner_enabled)
            and bool(self._internet_scanner_url)
        )
        self._refresh_tooltip()

    def _refresh_tooltip(self) -> None:
        tooltip = (
            f"{SHORT_APPLICATION_NAME} | Local: {self._server_label} | "
            f"Gateway: {self._gateway_label}"
        )
        # Windows truncates notification-area tooltips. Keep both lifecycle
        # states deterministic and within the platform limit.
        self.icon.setToolTip(tooltip[:127])

    def show_background_notice(self) -> None:
        if not self._available:
            return
        self.icon.showMessage(
            SHORT_APPLICATION_NAME,
            "The Control Center is still running in the notification area.",
            QSystemTrayIcon.MessageIcon.Information,
            4000,
        )

    def show_status_message(
        self,
        title: str,
        message: str,
        *,
        warning: bool = False,
        timeout_ms: int = 5000,
    ) -> None:
        if not self._available:
            return
        icon = (
            QSystemTrayIcon.MessageIcon.Warning
            if warning
            else QSystemTrayIcon.MessageIcon.Information
        )
        self.icon.showMessage(str(title), str(message), icon, int(timeout_ms))

    def _emit_open_survey(self) -> None:
        if self._direct_survey_url:
            self.open_survey_requested.emit(self._direct_survey_url)

    def _emit_open_internet_survey(self) -> None:
        if self._internet_survey_url:
            self.open_internet_survey_requested.emit(self._internet_survey_url)

    def _emit_open_internet_scanner(self) -> None:
        if self._internet_scanner_url:
            self.open_internet_scanner_requested.emit(self._internet_scanner_url)

    def _activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in {
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        }:
            self.open_requested.emit()
