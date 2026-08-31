from __future__ import annotations

import csv
from datetime import datetime
import getpass
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QEvent, QTimer, QUrl, Qt
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from school_csm_control_center.app_identity import (
    SHORT_APPLICATION_NAME,
    apply_windows_window_icon,
    load_application_icon,
    resolve_application_icon,
    resolve_deped_logo,
    resolve_mosslab_logo,
    resolve_mosslab_seal,
)
from school_csm_control_center.questionnaire import CC_QUESTIONS, RATING_LABELS, SQD_QUESTIONS
from school_csm_control_center.internet_gateway.client import GatewayProviderClient
from school_csm_control_center.internet_gateway.config import load_gateway_provider_config
from school_csm_control_center.internet_gateway.controller import InternetGatewayController
from school_csm_control_center.internet_gateway.tunnel import CloudflaredTunnel
from school_csm_control_center.runtime_paths import storage_root_for
from school_csm_control_center.school_services import service_display, service_value_from_record
from school_csm_control_center.services.analysis_service import AnalysisService
from school_csm_control_center.services.gateway_backup_adapter import GatewayBackupService
from school_csm_control_center.services.gateway_migration import GatewayMigrationService
from school_csm_control_center.storage.csv_import import parse_survey_history_csv
from school_csm_control_center.storage.dashboard_snapshot_store import DashboardSnapshotStore
from school_csm_control_center.storage.narrative_report_store import NarrativeReportStore
from school_csm_control_center.storage.print_history_store import PrintHistoryStore
from school_csm_control_center.storage.survey_store import SurveyStore
from school_csm_control_center.storage.gateway_credentials import GatewayCredentialStore
from school_csm_control_center.ui import theme
from school_csm_control_center.ui.dashboard_board import DashboardBoard
from school_csm_control_center.ui.dashboard_print_overlay import DashboardPrintOverlay
from school_csm_control_center.ui.dashboard_snapshot_codec import decode_dashboard_snapshot
from school_csm_control_center.ui.history_board import HistoryBoard
from school_csm_control_center.ui.history_clear_overlay import HistoryClearOverlay
from school_csm_control_center.ui.internet_gateway_overlays import (
    InternetGatewayDiagnosticsOverlay,
    InternetGatewaySetupOverlay,
    InternetServerTransferOverlay,
    MigrationPackageExportOverlay,
    RecoveryBackupExportOverlay,
    RegistrationDetailsOverlay,
)
from school_csm_control_center.ui.mrs_printing_board import MRSPrintingBoard
from school_csm_control_center.ui.narrative_report_overlay import NarrativeReportOverlay
from school_csm_control_center.ui.server_board import SurveyServerBoard
from school_csm_control_center.ui.school_information_board import SchoolInformationBoard
from school_csm_control_center.ui.icons import action_icon
from school_csm_control_center.ui.overlays import OverlayPrompt, Toast, WorkspaceOverlay
from school_csm_control_center.ui.survey_drawer import SurveyEntryOverlay
from school_csm_control_center.ui.system_tray import ControlCenterSystemTray
from school_csm_control_center.ui.retired_installation import (
    ForcedRetirementOverlay,
    launch_registered_uninstaller,
)
from school_csm_control_center.ui.title_bar import BrandedTitleBar, WindowResizeHandle
from school_csm_control_center.ui.widgets import TooltipIconButton
from school_csm_control_center.web_server import SurveyServerController


class SchoolCSMControlCenterWindow(QMainWindow):
    """School CSM Control Center with embedded local survey hosting."""

    def __init__(
        self,
        project_root: str | Path,
        parent: QWidget | None = None,
        *,
        startup_progress: Callable[[int, str], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self._startup_progress = startup_progress
        self._report_startup(22, "Opening the response database…")
        self.project_root = Path(project_root)
        self.data_root = storage_root_for(self.project_root)
        self.store = SurveyStore(self.project_root)
        self.print_store = PrintHistoryStore(self.project_root)
        self.snapshot_store = DashboardSnapshotStore(self.project_root)
        self.narrative_store = NarrativeReportStore(
            self.project_root,
            snapshot_store=self.snapshot_store,
        )
        try:
            self.print_store.interrupt_incomplete()
        except Exception:
            # A corrupt audit file is reported after the shell is ready; it is
            # never overwritten during startup recovery.
            pass
        self._report_startup(30, "Preparing the Control Center window…")
        self.setObjectName("school_csm_control_center_window")
        self.setWindowTitle(SHORT_APPLICATION_NAME)
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        application_icon_path = resolve_application_icon(self.project_root)
        application_icon = load_application_icon(self.project_root)
        if application_icon.isNull():
            application_icon = action_icon("dashboard", theme.ACCENT_TEAL, 64)
        self.setWindowIcon(application_icon)
        self.setProperty(
            "applicationIconPath",
            str(application_icon_path) if application_icon_path is not None else "",
        )
        mosslab_logo_path = resolve_mosslab_logo(self.project_root)
        self.setProperty(
            "mosslabLogoPath",
            str(mosslab_logo_path) if mosslab_logo_path is not None else "",
        )
        self.setProperty("nativeTaskbarIconApplied", False)
        self.setMinimumSize(760, 560)
        self.resize(1400, 860)

        self._report_startup(38, "Building the application shell…")
        self.window_frame = QFrame()
        self.window_frame.setObjectName("application_window_frame")
        window_layout = QVBoxLayout(self.window_frame)
        window_layout.setContentsMargins(1, 1, 1, 1)
        window_layout.setSpacing(0)
        self.title_bar = BrandedTitleBar(self, self.window_frame)
        window_layout.addWidget(self.title_bar)

        self.shell = QFrame()
        self.shell.setObjectName("application_shell")
        self.shell.installEventFilter(self)
        shell_layout = QHBoxLayout(self.shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)

        self._report_startup(46, "Loading navigation controls…")
        self.nav_rail = QFrame()
        self.nav_rail.setObjectName("navigation_rail")
        self.nav_rail.setFixedWidth(76)
        nav_layout = QVBoxLayout(self.nav_rail)
        nav_layout.setContentsMargins(12, 16, 12, 14)
        nav_layout.setSpacing(12)
        brand = QLabel("CSM")
        brand.setObjectName("navigation_brand")
        brand.setAlignment(Qt.AlignmentFlag.AlignCenter)
        brand.setFixedSize(52, 52)
        nav_layout.addWidget(brand, 0, Qt.AlignmentFlag.AlignHCenter)
        nav_layout.addSpacing(12)
        self.dashboard_nav = TooltipIconButton("dashboard", "Open Dashboard", button_size=48, icon_size=25)
        self.dashboard_nav.setCheckable(True)
        self.dashboard_nav.clicked.connect(lambda: self.show_board("dashboard"))
        nav_layout.addWidget(self.dashboard_nav, 0, Qt.AlignmentFlag.AlignHCenter)
        self.history_nav = TooltipIconButton("history", "Open survey and print History", button_size=48, icon_size=25)
        self.history_nav.setCheckable(True)
        self.history_nav.clicked.connect(lambda: self.show_board("history"))
        nav_layout.addWidget(self.history_nav, 0, Qt.AlignmentFlag.AlignHCenter)
        self.mrs_nav = TooltipIconButton("print", "Open official MRS Printing and Tracking", button_size=48, icon_size=25)
        self.mrs_nav.setCheckable(True)
        self.mrs_nav.clicked.connect(lambda: self.show_board("mrs"))
        nav_layout.addWidget(self.mrs_nav, 0, Qt.AlignmentFlag.AlignHCenter)
        self.server_nav = TooltipIconButton("server", "Open Survey Server and QR access controls", button_size=48, icon_size=25)
        self.server_nav.setCheckable(True)
        self.server_nav.clicked.connect(lambda: self.show_board("server"))
        nav_layout.addWidget(self.server_nav, 0, Qt.AlignmentFlag.AlignHCenter)
        self.school_info_nav = TooltipIconButton("school", "Open School Information", button_size=48, icon_size=25)
        self.school_info_nav.setCheckable(True)
        self.school_info_nav.clicked.connect(lambda: self.show_board("school_information"))
        nav_layout.addWidget(self.school_info_nav, 0, Qt.AlignmentFlag.AlignHCenter)
        nav_layout.addStretch(1)
        self.record_count = QLabel("0")
        self.record_count.setObjectName("navigation_record_count")
        self.record_count.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.record_count.setToolTip("Saved survey results")
        self.record_count.setFixedSize(42, 28)
        nav_layout.addWidget(self.record_count, 0, Qt.AlignmentFlag.AlignHCenter)
        shell_layout.addWidget(self.nav_rail)

        self._report_startup(54, "Loading dashboards and response history…")
        self.board_stack = QStackedWidget()
        self.board_stack.setObjectName("board_stack")
        self.dashboard = DashboardBoard()
        self.history = HistoryBoard()
        self._report_startup(66, "Preparing the local survey server…")
        self.server_controller = SurveyServerController(self.store, self.project_root, self)
        self._gateway_provider_error = ""
        try:
            self.gateway_provider_config = load_gateway_provider_config(
                self.project_root
            )
        except Exception as exc:
            self.gateway_provider_config = None
            self._gateway_provider_error = str(exc).strip()
        gateway_client = (
            GatewayProviderClient(self.gateway_provider_config)
            if self.gateway_provider_config is not None
            else None
        )
        gateway_migration = GatewayMigrationService(
            self.project_root,
            data_root=self.data_root,
        )
        gateway_backup = GatewayBackupService(
            self.project_root,
            data_root=self.data_root,
            migration_service=gateway_migration,
        )
        self.gateway_controller = InternetGatewayController(
            self.project_root,
            local_settings_provider=self.server_controller.settings,
            client=gateway_client,
            tunnel=CloudflaredTunnel(self.project_root),
            credentials=GatewayCredentialStore(),
            migration=gateway_migration,
            backup=gateway_backup,
            provider_config=self.gateway_provider_config,
            parent=self,
        )
        self.server_controller.set_runtime_settings_provider(
            self.gateway_controller.web_server_settings
        )
        self.server_board = SurveyServerBoard(
            self.server_controller,
            gateway_controller=self.gateway_controller,
        )
        self.mrs_printing = MRSPrintingBoard(
            self.project_root,
            settings_provider=self.server_controller.settings,
        )
        self.school_information = SchoolInformationBoard(self.project_root)
        self.school_information.set_registered_school_id(
            str(self.gateway_controller.state().get("school_id") or "")
        )
        self.board_stack.addWidget(self.dashboard)
        self.board_stack.addWidget(self.history)
        shell_layout.addWidget(self.board_stack, 1)
        window_layout.addWidget(self.shell, 1)
        self.branding_footer = self._build_branding_footer()
        window_layout.addWidget(self.branding_footer)
        self.setCentralWidget(self.window_frame)
        self.resize_handles = self._create_resize_handles()

        self._report_startup(74, "Preparing forms and overlays…")
        self.mrs_workspace_overlay = WorkspaceOverlay(
            self.shell,
            self.mrs_printing,
            title="MRS Printing and Tracking",
            subtitle="Generate, print, account for, and review machine-readable survey forms.",
        )
        self.server_workspace_overlay = WorkspaceOverlay(
            self.shell,
            self.server_board,
            title="Survey Server and Respondent Access",
            subtitle="Manage the local survey service, QR access, network status, and scanner operators.",
        )
        self.school_workspace_overlay = WorkspaceOverlay(
            self.shell,
            self.school_information,
            title="School Information",
            subtitle="Maintain the school identity used throughout the Control Center and survey form.",
        )
        self.mrs_workspace_overlay.closed.connect(
            lambda: self.mrs_nav.setChecked(False)
        )
        self.server_workspace_overlay.closed.connect(
            lambda: self.server_nav.setChecked(False)
        )
        self.school_workspace_overlay.closed.connect(
            lambda: self.school_info_nav.setChecked(False)
        )
        self.drawer = SurveyEntryOverlay(
            self.store,
            self.shell,
            settings_provider=self.server_controller.settings,
        )
        self.prompt = OverlayPrompt(self.shell)
        self.history_clear_overlay = HistoryClearOverlay(self.shell)
        self.print_overlay = DashboardPrintOverlay(
            self.dashboard,
            self.shell,
            project_root=self.project_root,
            print_store=self.print_store,
            snapshot_store=self.snapshot_store,
        )
        self.narrative_overlay = NarrativeReportOverlay(
            self.shell,
            project_root=self.project_root,
            snapshot_store=self.snapshot_store,
            report_store=self.narrative_store,
            print_store=self.print_store,
        )
        self.gateway_setup_overlay = InternetGatewaySetupOverlay(self.shell)
        self.gateway_transfer_overlay = InternetServerTransferOverlay(self.shell)
        self.gateway_package_export_overlay = MigrationPackageExportOverlay(self.shell)
        self.gateway_backup_export_overlay = RecoveryBackupExportOverlay(self.shell)
        self.gateway_diagnostics_overlay = InternetGatewayDiagnosticsOverlay(self.shell)
        self.gateway_details_overlay = RegistrationDetailsOverlay(self.shell)
        self.retirement_overlay = ForcedRetirementOverlay(self.window_frame)
        self.retirement_overlay.hide()
        self.toast = Toast(self.shell)
        self._explicit_exit_requested = False
        self._shutdown_started = False
        self._background_notice_shown = False
        self._gateway_connect_pending = False
        self._gateway_manual_connect_after_authorization = False
        self._gateway_listener_narrowing_pending = False
        self._gateway_auto_reconnect_pending = False
        self._gateway_authorization_verified_this_session = False
        self._gateway_auto_reconnect_suppressed = False
        self._pending_gateway_migration: dict[str, Any] | None = None
        self._pending_gateway_package_export: dict[str, Any] | None = None
        self._pending_gateway_backup_export: dict[str, Any] | None = None
        self._restart_server_after_gateway_export = False
        self._reconnect_gateway_after_export = False
        self._restart_server_after_gateway_backup = False
        self._reconnect_gateway_after_backup = False
        self._pending_gateway_transfer_mode = ""
        self._background_startup_enabled = bool(
            self.server_controller.settings().get(
                "background_server_startup_enabled", False
            )
        )
        self.system_tray = ControlCenterSystemTray(self.windowIcon(), self)
        self.system_tray.set_background_startup_checked(
            self._background_startup_enabled
        )
        self._report_startup(82, "Connecting Control Center actions…")
        self._connect_actions()
        self._apply_background_lifecycle()
        self._tray_urls_changed(*self.server_controller.urls())
        self._gateway_state_changed(self.gateway_controller.state())
        self._authorization_timer = QTimer(self)
        self._authorization_timer.setInterval(15 * 60 * 1000)
        self._authorization_timer.timeout.connect(
            self.gateway_controller.check_authorization_async
        )
        self._authorization_timer.start()
        if self.gateway_controller.configured:
            QTimer.singleShot(1600, self.gateway_controller.check_authorization_async)
        self._report_startup(87, "Applying interface styling…")
        self._apply_styles()
        self._report_startup(91, "Loading saved responses and live analysis…")
        self.show_board("dashboard")
        self.refresh_all()
        self._sync_window_chrome()
        if self.store.last_read_error:
            QTimer.singleShot(
                300,
                lambda: self.toast.show_message(
                    "The saved data file could not be read. It was left unchanged and the app opened with an empty view.",
                    kind="warning",
                    timeout_ms=6500,
                ),
            )
        if self.print_store.last_read_error:
            QTimer.singleShot(
                500,
                lambda: self.toast.show_message(
                    "The print-history file could not be read. It was left unchanged and no print entries were loaded.",
                    kind="warning",
                    timeout_ms=7000,
                ),
            )
        initial_settings = self.server_controller.settings_store.load()
        if not str(initial_settings.get("school_name") or "").strip() or not str(
            initial_settings.get("school_id") or ""
        ).strip():
            QTimer.singleShot(850, self._open_first_run_school_setup)

    def _report_startup(self, value: int, message: str) -> None:
        callback = getattr(self, "_startup_progress", None)
        if callback is not None:
            callback(value, message)

    def _connect_actions(self) -> None:
        self.dashboard.add_requested.connect(self.drawer.open_for_add)
        self.dashboard.print_requested.connect(self.print_overlay.open_overlay)
        self.dashboard.export_requested.connect(lambda: self.export_records(self.dashboard.current_filtered_records()))
        self.dashboard.methodology_requested.connect(self.show_methodology)
        self.history.view_requested.connect(self.view_record)
        self.history.print_view_requested.connect(self.view_print_record)
        self.history.narrative_requested.connect(self.open_narrative_report)
        self.history.reprint_requested.connect(self.open_dashboard_reprint)
        self.history.edit_requested.connect(self.drawer.open_for_edit)
        self.history.delete_requested.connect(self.confirm_delete)
        self.history.export_requested.connect(self.export_records)
        self.history.import_requested.connect(self.import_records_from_csv)
        self.history.clear_requested.connect(self.open_clear_history)
        self.history_clear_overlay.range_selected.connect(self._confirm_clear_history_first)
        self.mrs_printing.notice_requested.connect(
            lambda message, kind: self.toast.show_message(message, kind=kind, timeout_ms=7000)
        )
        self.mrs_printing.registry_changed.connect(self.refresh_all)
        self.drawer.survey_saved.connect(self._survey_saved)
        self.print_overlay.audit_history_changed.connect(
            lambda: self.history.refresh_prints(self.print_store.list())
        )
        self.print_overlay.print_job_completed.connect(self._record_completed_print)
        self.print_overlay.print_failed.connect(
            lambda message: self.toast.show_message(message, kind="error", timeout_ms=6500)
        )
        self.narrative_overlay.print_completed.connect(
            lambda _record, message: self.toast.show_message(
                message, kind="success", timeout_ms=7000
            )
        )
        self.narrative_overlay.print_failed.connect(
            lambda message: self.toast.show_message(
                message, kind="error", timeout_ms=7000
            )
        )
        self.server_controller.response_received.connect(self._browser_response_received)
        self.server_controller.status_changed.connect(self._server_status_toast)
        self.server_controller.status_changed.connect(
            lambda _status, _message: self.gateway_controller.refresh_local_settings()
        )
        self.server_controller.status_changed.connect(self._tray_server_status_changed)
        self.server_controller.urls_changed.connect(self._tray_urls_changed)
        self.server_board.background_startup_requested.connect(
            self._set_background_startup
        )
        self.system_tray.open_requested.connect(self.restore_from_tray)
        self.system_tray.start_server_requested.connect(
            self.start_saved_server_unattended
        )
        self.system_tray.stop_server_requested.connect(
            self.server_controller.stop_async
        )
        self.system_tray.open_survey_requested.connect(self._open_survey_url)
        self.system_tray.connect_gateway_requested.connect(
            self._request_gateway_connection
        )
        self.system_tray.disconnect_gateway_requested.connect(
            self._disconnect_gateway_manually
        )
        self.system_tray.open_internet_survey_requested.connect(
            self._open_survey_url
        )
        self.system_tray.open_internet_scanner_requested.connect(
            self._open_survey_url
        )
        self.system_tray.background_startup_toggled.connect(
            self._set_background_startup
        )
        self.system_tray.exit_requested.connect(self.exit_application)
        self.school_information.school_information_saved.connect(self._school_information_saved)
        self.school_information.notice_requested.connect(
            lambda message, kind: self.toast.show_message(message, kind=kind, timeout_ms=6500)
        )
        gateway_panel = self.server_board.internet_gateway
        gateway_panel.setup_requested.connect(self._open_gateway_setup)
        gateway_panel.connect_requested.connect(self._request_gateway_connection)
        gateway_panel.diagnostics_requested.connect(
            self._open_gateway_diagnostics
        )
        gateway_panel.transfer_requested.connect(self._open_gateway_transfer)
        gateway_panel.prepare_package_requested.connect(
            self._open_gateway_package_export
        )
        gateway_panel.prepare_backup_requested.connect(
            self._open_gateway_backup_export
        )
        gateway_panel.registration_details_requested.connect(
            self._open_gateway_details
        )
        self.gateway_setup_overlay.open_school_information_requested.connect(
            lambda: self.show_board("school_information")
        )
        self.gateway_setup_overlay.browser_authorization_requested.connect(
            self._open_gateway_authorization_page
        )
        self.gateway_setup_overlay.completion_code_requested.connect(
            self.gateway_controller.redeem_completion_code_async
        )
        self.gateway_setup_overlay.direct_configuration_requested.connect(
            self.gateway_controller.configure_direct_worker_vpc_async
        )
        self.gateway_transfer_overlay.browser_transfer_requested.connect(
            self._open_gateway_transfer_page
        )
        self.gateway_transfer_overlay.migration_import_requested.connect(
            self._prepare_gateway_migration
        )
        self.gateway_transfer_overlay.recovery_restore_requested.connect(
            self._prepare_gateway_backup_restore
        )
        self.gateway_package_export_overlay.package_requested.connect(
            self._prepare_gateway_package_export
        )
        self.gateway_backup_export_overlay.backup_requested.connect(
            self._prepare_gateway_backup_export
        )
        self.gateway_transfer_overlay.completion_code_requested.connect(
            self.gateway_controller.redeem_completion_code_async
        )
        self.gateway_controller.state_changed.connect(self._gateway_state_changed)
        self.gateway_controller.operation_progress.connect(
            self._gateway_operation_progress
        )
        self.gateway_controller.diagnostics_ready.connect(
            self.gateway_diagnostics_overlay.show_results
        )
        self.gateway_controller.notice_requested.connect(
            lambda message, kind: self.toast.show_message(
                message, kind=kind, timeout_ms=7500
            )
        )
        self.gateway_controller.retirement_confirmed.connect(
            self._enforce_runtime_retirement
        )
        self.gateway_details_overlay.manage_passkeys_requested.connect(
            self._open_gateway_passkey_management
        )
        self.retirement_overlay.uninstall_requested.connect(
            self._launch_retired_uninstaller
        )
        self.retirement_overlay.exit_requested.connect(self.exit_application)

    def show_board(self, board: str) -> None:
        target = str(board or "dashboard").casefold()
        utility_target = None
        utility_button = None
        if target in {"mrs", "mrs_printing", "mrs-printing"}:
            utility_target = self.mrs_workspace_overlay
            utility_button = self.mrs_nav
        elif target == "server":
            utility_target = self.server_workspace_overlay
            utility_button = self.server_nav
        elif target in {"school", "school_information", "school-info"}:
            utility_target = self.school_workspace_overlay
            utility_button = self.school_info_nav

        utility_overlays = (
            self.mrs_workspace_overlay,
            self.server_workspace_overlay,
            self.school_workspace_overlay,
        )
        if utility_target is not None and utility_button is not None:
            for overlay in utility_overlays:
                if overlay is not utility_target:
                    overlay.close_overlay()
            self.mrs_nav.setChecked(utility_target is self.mrs_workspace_overlay)
            self.server_nav.setChecked(
                utility_target is self.server_workspace_overlay
            )
            self.school_info_nav.setChecked(
                utility_target is self.school_workspace_overlay
            )
            utility_target.open_overlay(utility_button)
            return

        for overlay in utility_overlays:
            overlay.close_overlay()
        if target == "history":
            widget = self.history
            focused = self.history_nav
        else:
            widget = self.dashboard
            focused = self.dashboard_nav
        self.board_stack.setCurrentWidget(widget)
        self.dashboard_nav.setChecked(widget is self.dashboard)
        self.history_nav.setChecked(widget is self.history)
        self.mrs_nav.setChecked(False)
        self.server_nav.setChecked(False)
        self.school_info_nav.setChecked(False)
        focused.setFocus(Qt.FocusReason.ShortcutFocusReason)


    def _build_branding_footer(self) -> QFrame:
        footer = QFrame()
        footer.setObjectName("branding_footer")
        footer.setFixedHeight(62)
        layout = QHBoxLayout(footer)
        layout.setContentsMargins(16, 7, 16, 7)
        layout.setSpacing(12)

        self.footer_school_logo = QLabel()
        self.footer_school_logo.setObjectName("footer_school_logo")
        self.footer_school_logo.setFixedSize(44, 44)
        self.footer_school_logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.footer_school_logo)

        self.footer_school_name = QLabel("School")
        self.footer_school_name.setObjectName("footer_school_name")
        self.footer_school_name.setToolTip("School identity configured in School Information")
        layout.addWidget(self.footer_school_name)
        layout.addStretch(1)

        self.footer_deped_logo = QLabel()
        self.footer_deped_logo.setObjectName("footer_deped_logo")
        self.footer_deped_logo.setFixedSize(112, 44)
        self.footer_deped_logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.footer_deped_logo.setToolTip("Department of Education")
        layout.addWidget(self.footer_deped_logo)

        self.footer_mosslab_logo = QLabel()
        self.footer_mosslab_logo.setObjectName("footer_mosslab_logo")
        self.footer_mosslab_logo.setFixedSize(118, 40)
        self.footer_mosslab_logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.footer_mosslab_logo.setToolTip("MoSSLab · Motiong Schools Systems Laboratory")
        layout.addWidget(self.footer_mosslab_logo)

        self.footer_mosslab_seal = QLabel()
        self.footer_mosslab_seal.setObjectName("footer_mosslab_seal")
        self.footer_mosslab_seal.setFixedSize(44, 44)
        self.footer_mosslab_seal.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.footer_mosslab_seal.setToolTip("MoSSLab seal")
        layout.addWidget(self.footer_mosslab_seal)

        self._refresh_branding_footer()
        return footer

    @staticmethod
    def _set_scaled_pixmap(label: QLabel, path: Path | None, width: int, height: int) -> bool:
        pixmap = QPixmap(str(path)) if path is not None and path.is_file() else QPixmap()
        if pixmap.isNull():
            label.clear()
            return False
        label.setPixmap(
            pixmap.scaled(
                width,
                height,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        return True

    def _refresh_branding_footer(self, settings: dict[str, Any] | None = None) -> None:
        settings = settings or self.server_controller.settings_store.load()
        school_name = str(settings.get("school_name") or "School")
        self.footer_school_name.setText(school_name)
        self.footer_school_name.setToolTip(
            " · ".join(
                part for part in (
                    school_name,
                    str(settings.get("school_id") or ""),
                    str(settings.get("school_district") or ""),
                ) if part
            )
        )
        logo_relative = str(settings.get("school_logo_path") or "").strip()
        logo_path = self.data_root / logo_relative if logo_relative else self.data_root / "data" / "csm_survey" / "school_logo.png"
        if not self._set_scaled_pixmap(self.footer_school_logo, logo_path, 40, 40):
            self.footer_school_logo.setPixmap(action_icon("school", theme.ACCENT_CYAN, 28).pixmap(28, 28))
        self._set_scaled_pixmap(self.footer_deped_logo, resolve_deped_logo(self.project_root), 106, 40)
        self._set_scaled_pixmap(self.footer_mosslab_logo, resolve_mosslab_logo(self.project_root), 112, 36)
        self._set_scaled_pixmap(self.footer_mosslab_seal, resolve_mosslab_seal(self.project_root), 40, 40)

    def _school_information_saved(self, settings: object) -> None:
        saved = dict(settings) if isinstance(settings, dict) else self.server_controller.settings_store.load()
        self.server_controller.reload_persistent_settings()
        self.gateway_controller.refresh_local_settings()
        self._refresh_branding_footer(saved)
        self.mrs_printing.refresh()
        self.toast.show_message("School information saved. Control Center and Survey Form branding updated.", kind="success")

    def _open_gateway_setup(self) -> None:
        local = self.server_controller.settings()
        state = self.gateway_controller.state()
        detail = self._gateway_provider_error or str(
            state.get("detail") or ""
        )
        self.gateway_setup_overlay.configure(
            school_id=str(local.get("school_id") or ""),
            school_name=str(local.get("school_name") or ""),
            provider_available=self.gateway_controller.provider_available,
            detail=detail,
            deployment_mode=str(state.get("deployment_mode") or ""),
            public_host=str(state.get("public_host") or ""),
            tunnel_id=str(state.get("tunnel_id") or ""),
        )
        self.gateway_setup_overlay.open_overlay(
            self.server_board.internet_gateway.setup_button
        )

    def _open_gateway_authorization_page(self, mode: str = "registration") -> None:
        try:
            url = self.gateway_controller.management_url(mode=mode)
        except Exception as exc:
            self.gateway_setup_overlay.set_status(str(exc))
            return
        if not QDesktopServices.openUrl(QUrl(url)):
            self.gateway_setup_overlay.set_status(
                "Windows could not open the secure authorization page. Check the default browser setting."
            )
            return
        self.gateway_setup_overlay.set_status(
            "The secure authorization page opened in your browser. Return here with its one-time completion code."
        )

    def _open_gateway_transfer(self) -> None:
        local = self.server_controller.settings()
        state = self.gateway_controller.state()
        try:
            installation_id = self.gateway_controller.ensure_installation_id()
        except Exception as exc:
            self.toast.show_message(str(exc), kind="error", timeout_ms=7000)
            return
        self.gateway_transfer_overlay.configure(
            school_id=str(state.get("school_id") or local.get("school_id") or ""),
            school_name=str(local.get("school_name") or ""),
            installation_id=installation_id,
        )
        self.gateway_transfer_overlay.open_overlay(
            self.server_board.internet_gateway.transfer_button
        )

    def _open_gateway_package_export(self) -> None:
        local = self.server_controller.settings()
        state = self.gateway_controller.state()
        try:
            source_installation_id = self.gateway_controller.ensure_installation_id()
        except Exception as exc:
            self.toast.show_message(str(exc), kind="error", timeout_ms=7000)
            return
        self.gateway_package_export_overlay.configure(
            school_id=str(state.get("school_id") or local.get("school_id") or ""),
            school_name=str(local.get("school_name") or ""),
            source_installation_id=source_installation_id,
        )
        self.gateway_package_export_overlay.open_overlay(
            self.server_board.internet_gateway.prepare_package_button
        )

    def _prepare_gateway_package_export(self, request: object) -> None:
        payload = dict(request) if isinstance(request, dict) else {}
        self._restart_server_after_gateway_export = self.server_controller.running
        self._reconnect_gateway_after_export = self.gateway_controller.connected
        if self._reconnect_gateway_after_export:
            self.gateway_controller.disconnect()
        if self.server_controller.running:
            self._pending_gateway_package_export = payload
            self.gateway_package_export_overlay.set_progress(
                "Stopping the local Survey Server before creating a consistent encrypted snapshot…",
                busy=True,
            )
            if not self.server_controller.stop_async():
                self._pending_gateway_package_export = None
                self.gateway_package_export_overlay.set_progress(
                    "The Survey Server is busy. Wait for its current operation to finish, then try again.",
                    busy=False,
                )
                self._restore_server_after_gateway_export()
            return
        self._start_gateway_package_export(payload)

    def _start_gateway_package_export(self, payload: dict[str, Any]) -> None:
        self.gateway_package_export_overlay.set_progress(
            "Creating the encrypted destination-bound package…", busy=True
        )
        if not self.gateway_controller.create_migration_package_async(payload):
            self.gateway_package_export_overlay.set_progress(
                "Another Internet Gateway operation is still in progress. Try again when it finishes.",
                busy=False,
            )
            self._restore_server_after_gateway_export()

    def _restore_server_after_gateway_export(self) -> None:
        restart = self._restart_server_after_gateway_export
        reconnect = self._reconnect_gateway_after_export
        self._restart_server_after_gateway_export = False
        self._reconnect_gateway_after_export = False
        if not restart:
            return
        if reconnect:
            if self.server_controller.running and self.server_controller.tunnel_origin_url():
                QTimer.singleShot(
                    0, self.gateway_controller.connect_or_reconnect_async
                )
            else:
                self._gateway_connect_pending = True
        if not self.server_controller.running:
            QTimer.singleShot(0, self.start_saved_server_unattended)

    def _open_gateway_backup_export(self) -> None:
        local = self.server_controller.settings()
        state = self.gateway_controller.state()
        try:
            source_installation_id = self.gateway_controller.ensure_installation_id()
        except Exception as exc:
            self.toast.show_message(str(exc), kind="error", timeout_ms=7000)
            return
        self.gateway_backup_export_overlay.configure(
            school_id=str(state.get("school_id") or local.get("school_id") or ""),
            school_name=str(local.get("school_name") or ""),
            source_installation_id=source_installation_id,
        )
        self.gateway_backup_export_overlay.open_overlay(
            self.server_board.internet_gateway.prepare_backup_button
        )

    def _prepare_gateway_backup_export(self, request: object) -> None:
        payload = dict(request) if isinstance(request, dict) else {}
        self._restart_server_after_gateway_backup = self.server_controller.running
        self._reconnect_gateway_after_backup = self.gateway_controller.connected
        if self._reconnect_gateway_after_backup:
            self.gateway_controller.disconnect()
        if self.server_controller.running:
            self._pending_gateway_backup_export = payload
            self.gateway_backup_export_overlay.set_progress(
                "Stopping the local Survey Server before creating a consistent encrypted recovery backup...",
                busy=True,
            )
            if not self.server_controller.stop_async():
                self._pending_gateway_backup_export = None
                self.gateway_backup_export_overlay.set_progress(
                    "The Survey Server is busy. Wait for its current operation to finish, then try again.",
                    busy=False,
                )
                self._restore_server_after_gateway_backup()
            return
        self._start_gateway_backup_export(payload)

    def _start_gateway_backup_export(self, payload: dict[str, Any]) -> None:
        self.gateway_backup_export_overlay.set_progress(
            "Creating the encrypted portable recovery backup...", busy=True
        )
        if not self.gateway_controller.create_recovery_backup_async(payload):
            self.gateway_backup_export_overlay.set_progress(
                "Another Internet Gateway operation is still in progress. Try again when it finishes.",
                busy=False,
            )
            self._restore_server_after_gateway_backup()

    def _restore_server_after_gateway_backup(self) -> None:
        restart = self._restart_server_after_gateway_backup
        reconnect = self._reconnect_gateway_after_backup
        self._restart_server_after_gateway_backup = False
        self._reconnect_gateway_after_backup = False
        if not restart:
            return
        if reconnect:
            if self.server_controller.running and self.server_controller.tunnel_origin_url():
                QTimer.singleShot(
                    0, self.gateway_controller.connect_or_reconnect_async
                )
            else:
                self._gateway_connect_pending = True
        if not self.server_controller.running:
            QTimer.singleShot(0, self.start_saved_server_unattended)

    def _open_gateway_transfer_page(self, request: object) -> None:
        payload = dict(request) if isinstance(request, dict) else {}
        if not self.gateway_controller.create_transfer_intent_async(payload):
            self.gateway_transfer_overlay.set_progress(
                "Another Internet Gateway operation is still in progress. Try again when it finishes."
            )
            return
        self.gateway_transfer_overlay.set_progress(
            "Creating a short-lived Server Transfer authorization request…",
            cutover_locked=True,
        )

    def _prepare_gateway_migration(self, request: object) -> None:
        payload = dict(request) if isinstance(request, dict) else {}
        transfer_mode = str(payload.pop("transfer_mode", "") or "").casefold()
        if transfer_mode != "server_and_data":
            self.gateway_transfer_overlay.set_progress(
                "A .mossmig package can be used only with Server and Data transfer."
            )
            return
        self._prepare_gateway_data_activation(payload, transfer_mode)

    def _prepare_gateway_backup_restore(self, request: object) -> None:
        payload = dict(request) if isinstance(request, dict) else {}
        transfer_mode = str(payload.pop("transfer_mode", "") or "").casefold()
        if transfer_mode != "backup_assisted":
            self.gateway_transfer_overlay.set_progress(
                "A .mossbak file can be used only with Recovery Backup transfer."
            )
            return
        self._prepare_gateway_data_activation(payload, transfer_mode)

    def _prepare_gateway_data_activation(
        self, payload: dict[str, Any], transfer_mode: str
    ) -> None:
        self._pending_gateway_transfer_mode = transfer_mode
        if self.server_controller.running:
            self._pending_gateway_migration = payload
            self.gateway_transfer_overlay.set_progress(
                "Stopping the local Survey Server before validating and activating transferred data…",
                cutover_locked=True,
            )
            if not self.server_controller.stop_async():
                self._pending_gateway_migration = None
                self._pending_gateway_transfer_mode = ""
                self.gateway_transfer_overlay.set_progress(
                    "The Survey Server is busy. Wait for its current operation to finish, then try again."
                )
            return
        self._start_gateway_data_activation(payload)

    def _start_gateway_data_activation(self, payload: dict[str, Any]) -> None:
        backup_mode = self._pending_gateway_transfer_mode == "backup_assisted"
        self.gateway_transfer_overlay.set_progress(
            (
                "Authenticating and restoring the encrypted recovery backup before any Internet authority is transferred..."
                if backup_mode
                else "Validating the encrypted migration package before any Internet authority is transferred..."
            ),
            cutover_locked=True,
        )
        started = (
            self.gateway_controller.restore_recovery_backup_async(payload)
            if backup_mode
            else self.gateway_controller.prepare_migration_import_async(payload)
        )
        if not started:
            self._pending_gateway_transfer_mode = ""
            self.gateway_transfer_overlay.set_progress(
                "Another Internet Gateway operation is still in progress. Try again when it finishes."
            )

    def _open_gateway_diagnostics(self) -> None:
        self.gateway_diagnostics_overlay.show_results(
            {"detail": "Running Internet Gateway diagnostics..."}
        )
        self.gateway_diagnostics_overlay.open_overlay(
            self.server_board.internet_gateway.diagnostics_button
        )
        self.gateway_controller.run_diagnostics_async()

    def _open_gateway_details(self) -> None:
        self.gateway_details_overlay.show_state(self.gateway_controller.state())
        self.gateway_details_overlay.open_overlay(
            self.server_board.internet_gateway.details_button
        )

    def _open_gateway_passkey_management(self) -> None:
        try:
            url = self.gateway_controller.passkey_management_url()
        except Exception as exc:
            self.toast.show_message(str(exc), kind="error", timeout_ms=7000)
            return
        if not QDesktopServices.openUrl(QUrl(url)):
            self.toast.show_message(
                "Windows could not open Administrator Passkey management in the secure browser.",
                kind="error",
                timeout_ms=7000,
            )

    def _gateway_state_changed(self, state: object) -> None:
        gateway = dict(state) if isinstance(state, dict) else {}
        registered_school_id = (
            str(gateway.get("school_id") or "")
            if gateway.get("registration_state") == "registered"
            else ""
        )
        if hasattr(self, "school_information"):
            self.school_information.set_registered_school_id(registered_school_id)
        if hasattr(self, "system_tray"):
            self.system_tray.set_gateway_state(
                str(gateway.get("gateway_state") or "not_configured"),
                str(gateway.get("detail") or ""),
                configured=bool(
                    gateway.get("registration_state") == "registered"
                    and gateway.get("public_host")
                ),
                authorized=bool(gateway.get("authorized_this_device")),
                survey_url=str(gateway.get("survey_url") or ""),
                scanner_url=str(gateway.get("scanner_url") or ""),
                scanner_enabled=bool(
                    gateway.get("scanner_remote_enabled", True)
                ),
            )
        if gateway.get("retirement") and hasattr(self, "retirement_overlay"):
            self._enforce_runtime_retirement(gateway.get("retirement"))

    def _gateway_operation_progress(self, progress: object) -> None:
        document = dict(progress) if isinstance(progress, dict) else {}
        message = str(document.get("detail") or "")
        busy = bool(document.get("busy"))
        operation = str(document.get("operation") or "")
        if operation == "direct_setup":
            self.gateway_setup_overlay.set_status(message, busy=busy)
            if not busy and not document.get("error"):
                self._gateway_authorization_verified_this_session = False
                self.gateway_setup_overlay.clear_sensitive_fields()
        elif operation in {"migration_import", "backup_restore"}:
            self.gateway_transfer_overlay.set_progress(
                message, cutover_locked=busy
            )
            if not busy and not document.get("error"):
                result = (
                    dict(document.get("result"))
                    if isinstance(document.get("result"), dict)
                    else {}
                )
                validation = result.get("data_validation")
                transfer_mode = self._pending_gateway_transfer_mode
                expected_operation = (
                    "backup_restore"
                    if transfer_mode == "backup_assisted"
                    else "migration_import"
                )
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
                    backup_fields if transfer_mode == "backup_assisted" else set()
                )
                valid_receipt = bool(
                    operation == expected_operation
                    and isinstance(validation, dict)
                    and set(validation) == expected_fields
                    and validation.get("verified") is True
                    and (
                        transfer_mode != "backup_assisted"
                        or validation.get("source_kind") == "portable_backup"
                    )
                )
                if not valid_receipt:
                    self._pending_gateway_transfer_mode = ""
                    self.gateway_transfer_overlay.set_progress(
                        "The completed data operation did not return the exact receipt for the selected transfer mode. Internet authority was not transferred."
                    )
                else:
                    self.gateway_transfer_overlay.set_data_validation_result(validation)
                    self.server_controller.reload_persistent_settings()
                    self.school_information.load_settings()
                    self.gateway_controller.refresh_local_settings()
                    self._refresh_branding_footer()
                    self.refresh_all()
                    if transfer_mode in {"server_and_data", "backup_assisted"}:
                        request = {
                            "transfer_mode": transfer_mode,
                            "data_validation": dict(validation),
                        }
                        QTimer.singleShot(
                            0,
                            lambda payload=request: self._open_gateway_transfer_page(payload),
                        )
                    else:
                        self.gateway_transfer_overlay.set_progress(
                            "The transfer option was lost after migration. The validated data remains active; choose the transfer option again to continue."
                        )
            elif not busy and document.get("error"):
                self._pending_gateway_transfer_mode = ""
        elif operation == "migration_export":
            self.gateway_package_export_overlay.set_progress(message, busy=busy)
            if not busy:
                if not document.get("error") and isinstance(
                    document.get("result"), dict
                ):
                    self.gateway_package_export_overlay.show_result(
                        dict(document["result"])
                    )
                self._restore_server_after_gateway_export()
        elif operation == "backup_export":
            self.gateway_backup_export_overlay.set_progress(message, busy=busy)
            if not busy:
                if not document.get("error") and isinstance(
                    document.get("result"), dict
                ):
                    self.gateway_backup_export_overlay.show_result(
                        dict(document["result"])
                    )
                self._restore_server_after_gateway_backup()
        elif operation == "transfer_intent":
            self.gateway_transfer_overlay.set_progress(message, cutover_locked=busy)
            if not busy and not document.get("error"):
                result = (
                    dict(document.get("result"))
                    if isinstance(document.get("result"), dict)
                    else {}
                )
                url = str(result.get("management_url") or "").strip()
                if not url.casefold().startswith("https://") or not QDesktopServices.openUrl(
                    QUrl(url)
                ):
                    self.gateway_transfer_overlay.set_progress(
                        "Windows could not open the secure Server Transfer page. No Internet authority was transferred."
                    )
                else:
                    expiry = str(result.get("expires_at") or "").strip()
                    suffix = f" The request expires at {expiry}." if expiry else ""
                    self.gateway_transfer_overlay.set_progress(
                        "The secure Server Transfer page opened in your browser. Complete the passkey check there, then return with its one-time completion code."
                        + suffix
                    )
                self._pending_gateway_transfer_mode = ""
        elif operation == "authorization_check" and not busy:
            result = (
                dict(document.get("result"))
                if isinstance(document.get("result"), dict)
                else self.gateway_controller.state()
            )
            self._gateway_authorization_verified_this_session = bool(
                not document.get("error")
                and result.get("authorization_state") == "active"
                and result.get("authorized_this_device")
                and not result.get("retirement")
            )
            if self._gateway_authorization_verified_this_session:
                if self._gateway_manual_connect_after_authorization:
                    self._gateway_manual_connect_after_authorization = False
                    QTimer.singleShot(0, self._request_gateway_connection)
                else:
                    QTimer.singleShot(0, self._request_background_gateway_reconnect)
            elif not self._gateway_authorization_verified_this_session:
                self._gateway_manual_connect_after_authorization = False
                self._gateway_auto_reconnect_pending = False
                QTimer.singleShot(0, self._narrow_gateway_listener_after_authorization_loss)
        elif operation == "connect" and not busy:
            self._gateway_auto_reconnect_pending = False
        if operation == "completion_code":
            if self.gateway_transfer_overlay.isVisible():
                self.gateway_transfer_overlay.set_progress(
                    message, cutover_locked=busy
                )
            else:
                self.gateway_setup_overlay.set_status(message, busy=busy)
            if (
                not busy
                and not document.get("error")
                and self.server_controller.running
                and not self.server_controller.tunnel_origin_url()
            ):
                self.toast.show_message(
                    "Internet authorization is saved. Stop and start the local Survey Server once before connecting the Internet Gateway; local LAN access will remain available.",
                    kind="info",
                    timeout_ms=8500,
                )
        if document.get("error"):
            self.toast.show_message(message, kind="error", timeout_ms=7500)

    def _enforce_runtime_retirement(self, record: object) -> None:
        retirement = dict(record) if isinstance(record, dict) else {}
        self.server_controller.request_shutdown()
        try:
            self.gateway_controller.disconnect()
        except Exception:
            pass
        self.system_tray.hide()
        self.retirement_overlay.show_for_record(retirement)
        self.retirement_overlay.raise_()
        self.retirement_overlay.setFocus()

    def _launch_retired_uninstaller(self) -> None:
        try:
            import os

            launch_registered_uninstaller(wait_for_pid=os.getpid())
        except Exception as exc:
            self.retirement_overlay.set_status(str(exc), error=True)
            return
        self.exit_application()

    def _open_first_run_school_setup(self) -> None:
        settings = self.server_controller.settings_store.load()
        if str(settings.get("school_name") or "").strip() and str(
            settings.get("school_id") or ""
        ).strip():
            return
        self.show_board("school_information")
        self.toast.show_message(
            "Complete School Information before recording responses or starting the Survey Server.",
            kind="info",
            timeout_ms=7500,
        )

    def _browser_response_received(self, record: object) -> None:
        self.refresh_all()
        control = record.get("control_number") if isinstance(record, dict) else ""
        suffix = f" Reference: {control}." if control else ""
        self.toast.show_message(
            f"Mobile survey response received.{suffix} Live analysis refreshed.",
            kind="success",
            timeout_ms=6000,
        )

    def _server_status_toast(self, status: str, message: str) -> None:
        if status == "online":
            self.toast.show_message(message, kind="success", timeout_ms=4500)
        elif status == "error":
            self.toast.show_message(message, kind="error", timeout_ms=7000)

    @property
    def background_startup_enabled(self) -> bool:
        return bool(self._background_startup_enabled)

    def background_startup_readiness(self) -> tuple[bool, str]:
        """Return whether a Windows sign-in launch may remain hidden."""

        if not self._background_startup_enabled:
            return False, "Background startup is disabled in Server Settings."
        if not self.system_tray.available:
            return False, "The Windows notification area is not available."
        settings = self.server_controller.settings()
        if not str(settings.get("school_name") or "").strip() or not str(
            settings.get("school_id") or ""
        ).strip():
            return False, "School Information must be completed before background startup."
        return True, "Ready for background startup."

    def _set_background_startup(self, enabled: bool) -> None:
        requested = bool(enabled)
        previous = self._background_startup_enabled
        try:
            actual = bool(
                self.server_controller.set_background_server_startup(requested)
            )
        except Exception as exc:
            self._background_startup_enabled = previous
            detail = str(exc).strip() or "Windows could not change the startup setting."
            self.server_board.set_background_startup_enabled(previous, detail)
            self.system_tray.set_background_startup_checked(previous)
            self._apply_background_lifecycle()
            self.toast.show_message(detail, kind="error", timeout_ms=7000)
            self.system_tray.show_status_message(
                "Startup setting was not changed",
                detail,
                warning=True,
            )
            return

        self._background_startup_enabled = actual
        if actual:
            self._gateway_auto_reconnect_suppressed = False
            QTimer.singleShot(0, self._request_background_gateway_reconnect)
        else:
            self._gateway_auto_reconnect_pending = False
        detail = (
            "The Control Center and Survey Server will start in the notification area when this Windows account signs in. A registered Internet Gateway reconnects only after the local server is healthy and device authorization is verified."
            if actual
            else "Automatic background startup is disabled."
        )
        self.server_board.set_background_startup_enabled(actual, detail)
        self.system_tray.set_background_startup_checked(actual)
        self._apply_background_lifecycle()
        self.toast.show_message(detail, kind="success")

    def _apply_background_lifecycle(self) -> None:
        keep_alive = self._background_startup_enabled and self.system_tray.available
        app = QApplication.instance()
        if app is not None:
            app.setQuitOnLastWindowClosed(not keep_alive)
        close_description = (
            "Hide to the Windows notification area"
            if keep_alive
            else f"Close {SHORT_APPLICATION_NAME}"
        )
        self.title_bar.close_button.set_action(
            "close",
            close_description,
            icon_color=theme.DANGER,
        )

    def restore_from_tray(self) -> None:
        if self.isMinimized():
            self.showNormal()
        else:
            self.show()
        self.raise_()
        self.activateWindow()
        self._background_notice_shown = False

    def start_saved_server_unattended(self) -> bool:
        settings = self.server_controller.settings()
        if not str(settings.get("school_name") or "").strip() or not str(
            settings.get("school_id") or ""
        ).strip():
            detail = "Complete School Information before starting the Survey Server."
            self.restore_from_tray()
            self.show_board("school_information")
            self.toast.show_message(detail, kind="warning", timeout_ms=6500)
            self.system_tray.show_status_message(
                "Survey Server needs setup", detail, warning=True
            )
            return False
        if self.server_controller.running:
            return True
        preferred_port = int(settings.get("preferred_port") or 8080)
        started = self.server_controller.start_async(
            preferred_port,
            8080,
            configure_firewall=False,
        )
        if not started and not self.server_controller.running:
            detail = "A Survey Server start or stop operation is already in progress."
            self.system_tray.show_status_message(
                "Survey Server", detail, warning=True
            )
        return bool(started or self.server_controller.running)

    def _request_gateway_connection(self) -> None:
        """Ensure the private loopback origin exists, then start the tunnel."""

        self._gateway_auto_reconnect_suppressed = False
        if self.gateway_controller.operation_in_progress:
            return
        if not self.gateway_controller.authorization_verified_this_session:
            self._gateway_manual_connect_after_authorization = True
            self.gateway_controller.check_authorization_async()
            return
        if self.server_controller.running and self.server_controller.tunnel_origin_url():
            self.gateway_controller.connect_or_reconnect_async()
            return
        self._gateway_connect_pending = True
        if self.server_controller.running:
            self.toast.show_message(
                "Restarting the local Survey Server once to enable its private Internet Gateway origin. Local LAN access resumes automatically.",
                kind="info",
                timeout_ms=6500,
            )
            if not self.server_controller.stop_async():
                self._gateway_connect_pending = False
            return
        if not self.start_saved_server_unattended():
            self._gateway_connect_pending = False

    def _disconnect_gateway_manually(self) -> None:
        self._gateway_auto_reconnect_suppressed = True
        self._gateway_auto_reconnect_pending = False
        self.gateway_controller.disconnect()

    def _background_gateway_reconnect_candidate(self) -> bool:
        state = self.gateway_controller.state()
        return bool(
            self._background_startup_enabled
            and not self._gateway_auto_reconnect_suppressed
            and self.server_controller.running
            and (
                self.gateway_controller.provider_available
                or bool(getattr(self.gateway_controller, "direct_mode", False))
            )
            and self.gateway_controller.configured
            and state.get("authorization_state") == "active"
            and state.get("authorized_this_device")
            and not state.get("retirement")
            and state.get("gateway_state") != "connected"
        )

    def _request_background_gateway_reconnect(self) -> None:
        """Sequence background reconnect after local health and fresh authorization."""

        if not self._background_gateway_reconnect_candidate():
            self._gateway_auto_reconnect_pending = False
            return
        self._gateway_auto_reconnect_pending = True
        if self.gateway_controller.operation_in_progress:
            return
        if not self._gateway_authorization_verified_this_session:
            self.gateway_controller.check_authorization_async()
            return
        if not self.server_controller.tunnel_origin_url():
            self._gateway_auto_reconnect_pending = True
            self._request_gateway_connection()
            return
        self._gateway_auto_reconnect_pending = False
        self.gateway_controller.connect_or_reconnect_async()

    def _narrow_gateway_listener_after_authorization_loss(self) -> None:
        """Stop public routing and restore the selected-interface local bind."""

        try:
            self.gateway_controller.disconnect()
        except Exception:
            pass
        if not self.server_controller.wildcard_listener_active():
            self._gateway_listener_narrowing_pending = False
            return
        self._gateway_listener_narrowing_pending = True
        if not self.server_controller.stop_async():
            self._gateway_listener_narrowing_pending = False
            self.toast.show_message(
                "Internet authorization could not be verified and the Survey Server could not be narrowed automatically. Stop and restart it before continuing Local-Only use.",
                kind="error",
                timeout_ms=8500,
            )

    def _tray_server_status_changed(self, status: str, message: str) -> None:
        self.system_tray.set_server_state(status, message)
        normalized = str(status).casefold()
        manual_gateway_connect_ready = False
        listener_narrowed_ready = False
        if self._pending_gateway_migration is not None and normalized == "offline":
            payload = self._pending_gateway_migration
            self._pending_gateway_migration = None
            QTimer.singleShot(
                0,
                lambda request=payload: self._start_gateway_data_activation(request),
            )
        elif self._pending_gateway_migration is not None and normalized == "error":
            self._pending_gateway_migration = None
            self._pending_gateway_transfer_mode = ""
            self.gateway_transfer_overlay.set_progress(
                "The local Survey Server could not be stopped safely. Transferred data was not activated."
            )
        elif self._pending_gateway_package_export is not None and normalized == "offline":
            payload = self._pending_gateway_package_export
            self._pending_gateway_package_export = None
            QTimer.singleShot(
                0,
                lambda request=payload: self._start_gateway_package_export(request),
            )
        elif self._pending_gateway_package_export is not None and normalized == "error":
            self._pending_gateway_package_export = None
            self.gateway_package_export_overlay.set_progress(
                "The local Survey Server could not be stopped safely. No migration package was created.",
                busy=False,
            )
            self._restore_server_after_gateway_export()
        elif self._pending_gateway_backup_export is not None and normalized == "offline":
            payload = self._pending_gateway_backup_export
            self._pending_gateway_backup_export = None
            QTimer.singleShot(
                0,
                lambda request=payload: self._start_gateway_backup_export(request),
            )
        elif self._pending_gateway_backup_export is not None and normalized == "error":
            self._pending_gateway_backup_export = None
            self.gateway_backup_export_overlay.set_progress(
                "The local Survey Server could not be stopped safely. No recovery backup was created.",
                busy=False,
            )
            self._restore_server_after_gateway_backup()
        elif self._gateway_listener_narrowing_pending and normalized == "offline":
            QTimer.singleShot(0, self.start_saved_server_unattended)
        elif self._gateway_listener_narrowing_pending and normalized == "online":
            self._gateway_listener_narrowing_pending = False
            listener_narrowed_ready = True
        elif self._gateway_listener_narrowing_pending and normalized == "error":
            self._gateway_listener_narrowing_pending = False
        elif self._gateway_connect_pending and normalized == "offline":
            QTimer.singleShot(0, self.start_saved_server_unattended)
        elif self._gateway_connect_pending and normalized == "online":
            self._gateway_connect_pending = False
            manual_gateway_connect_ready = True
            QTimer.singleShot(0, self.gateway_controller.connect_or_reconnect_async)
        elif self._gateway_connect_pending and normalized == "error":
            self._gateway_connect_pending = False
        if str(status).casefold() == "error":
            self.system_tray.show_status_message(
                "Survey Server could not start",
                message,
                warning=True,
                timeout_ms=7000,
            )
        if normalized == "offline":
            # A later local-server start is a new lifecycle attempt and may
            # honor the persisted background reconnect preference again.
            self._gateway_auto_reconnect_suppressed = False
        elif (
            normalized == "online"
            and not manual_gateway_connect_ready
            and not listener_narrowed_ready
            and not self._gateway_listener_narrowing_pending
        ):
            QTimer.singleShot(0, self._request_background_gateway_reconnect)

    def _tray_urls_changed(
        self,
        direct_root: str,
        _named_root: str,
        direct_access_url: str,
        _named_access_url: str,
    ) -> None:
        self.system_tray.set_direct_survey_url(
            str(direct_access_url or direct_root or "")
        )

    @staticmethod
    def _open_survey_url(url: str) -> None:
        value = str(url or "").strip()
        if value.casefold().startswith(("http://", "https://")):
            QDesktopServices.openUrl(QUrl(value))

    def prepare_for_application_quit(self) -> None:
        if self._shutdown_started:
            return
        self._shutdown_started = True
        self._pending_gateway_migration = None
        self._pending_gateway_package_export = None
        self._pending_gateway_backup_export = None
        self._pending_gateway_transfer_mode = ""
        self.gateway_package_export_overlay.migration_key.clear()
        self.gateway_backup_export_overlay.backup_key.clear()
        self.system_tray.hide()
        try:
            self.gateway_controller.disconnect()
        except Exception:
            pass
        self.server_controller.request_shutdown()

    def exit_application(self) -> None:
        self._explicit_exit_requested = True
        app = QApplication.instance()
        if app is not None:
            app.setQuitOnLastWindowClosed(True)
        self.prepare_for_application_quit()
        self.close()
        if app is not None:
            app.quit()

    def refresh_all(self) -> None:
        records = self.store.list()
        self.dashboard.refresh(records)
        self.history.refresh(records)
        self.history.refresh_prints(self.print_store.list())
        self.mrs_printing.refresh()
        self.record_count.setText(str(len(records)))
        self.record_count.setToolTip(f"{len(records)} saved survey result(s)")

    def _survey_saved(self, record: dict[str, Any], editing: bool) -> None:
        self.refresh_all()
        action = "updated" if editing else "added"
        self.toast.show_message(f"Survey result {action}. Live analysis refreshed.")

    def _record_completed_print(self, record: object, message: str) -> None:
        saved = dict(record) if isinstance(record, dict) else {}
        self.history.refresh_prints(self.print_store.list())
        control_number = saved.get("control_number") or "Dashboard print"
        self.toast.show_message(
            f"{message} Recorded in History as {control_number}.",
            kind="success",
            timeout_ms=6500,
        )

    def open_dashboard_reprint(self, record: object) -> None:
        try:
            source = self._current_print_record(record)
            eligible, reason = PrintHistoryStore.narrative_eligibility(source)
            if not eligible:
                raise ValueError(reason)
            reference = source.get("dashboard_snapshot")
            if not isinstance(reference, dict):
                raise ValueError("The immutable Dashboard snapshot reference is missing.")
            snapshot, manifest = decode_dashboard_snapshot(self.snapshot_store, reference)
            snapshot_source = (
                manifest.get("source")
                if isinstance(manifest.get("source"), dict)
                else {}
            )
            if str(snapshot_source.get("print_record_id") or "") != str(
                source.get("id") or ""
            ):
                raise ValueError(
                    "The stored Dashboard belongs to a different print-history record."
                )
            self.narrative_overlay.close_overlay()
            self.print_overlay.open_reprint(source, snapshot)
        except Exception as exc:
            self.toast.show_message(
                f"The stored Dashboard could not be opened for reprinting: {exc}",
                kind="error",
                timeout_ms=8000,
            )

    def open_narrative_report(self, record: object) -> None:
        try:
            source = self._current_print_record(record)
            self.print_overlay.close_overlay()
            self.narrative_overlay.open_for_print_record(
                source,
                operator=getpass.getuser(),
            )
        except Exception as exc:
            self.toast.show_message(
                f"The Narrative Report could not be opened: {exc}",
                kind="error",
                timeout_ms=8000,
            )

    def _current_print_record(self, record: object) -> dict[str, Any]:
        """Resolve a History action against the durable audit, not a stale row."""

        supplied = dict(record) if isinstance(record, dict) else {}
        identity = str(
            supplied.get("id") or supplied.get("control_number") or ""
        ).strip()
        if not identity:
            raise ValueError("The Dashboard print-history identity is missing.")
        current = self.print_store.get(identity)
        if current is None:
            raise ValueError(
                "The Dashboard print-history record no longer exists. Refresh History and try again."
            )
        return current

    def open_clear_history(self) -> None:
        records = self.store.list()
        if not records:
            self.toast.show_message("There is no survey history to clear.", kind="warning")
            return
        self.history_clear_overlay.open_overlay(records)

    def _confirm_clear_history_first(
        self, date_from: object, date_to: object, count: int, label: str
    ) -> None:
        detail = (
            f"Selected period: {label}\n"
            f"Survey responses selected: {count}\n\n"
            "Dashboard print history and MRS printing records will remain unchanged."
        )
        self.prompt.show_prompt(
            "Clear selected survey history?",
            "First confirmation: review the selected date range and record count.",
            detail=detail,
            accept_tooltip="Continue to final confirmation",
            cancel_tooltip="Cancel history clearing",
            marker="1",
            destructive=True,
            on_accept=lambda: self._confirm_clear_history_final(
                date_from, date_to, count, label
            ),
        )

    def _confirm_clear_history_final(
        self, date_from: object, date_to: object, count: int, label: str
    ) -> None:
        self.prompt.show_prompt(
            "Final confirmation",
            f"Permanently delete {count} survey response(s) from {label}?",
            detail=(
                "This is the second and final confirmation. The operation cannot be undone "
                "from the Control Center. Dashboard analysis will be recalculated immediately."
            ),
            accept_tooltip="Permanently clear selected survey history",
            cancel_tooltip="Keep survey history",
            marker="2",
            destructive=True,
            on_accept=lambda: self._clear_history_range(date_from, date_to),
        )

    def _clear_history_range(self, date_from: object, date_to: object) -> None:
        try:
            result = self.store.delete_date_range(date_from, date_to)
        except (OSError, ValueError) as exc:
            self.toast.show_message(f"History clearing failed: {exc}", kind="error", timeout_ms=7000)
            return
        removed = int(result.get("removed_count") or 0)
        self.refresh_all()
        self.toast.show_message(
            f"Cleared {removed} survey response(s). Dashboard analysis refreshed.",
            kind="warning" if removed else "warning",
            timeout_ms=7000,
        )

    def confirm_delete(self, record: dict[str, Any]) -> None:
        control = record.get("control_number") or "No control number"
        service = service_display(service_value_from_record(record))
        self.prompt.show_prompt(
            "Delete this survey result?",
            "This permanently removes the selected response from History and recalculates the Dashboard.",
            detail=f"{control}\n{service}",
            accept_tooltip="Delete survey result",
            cancel_tooltip="Keep survey result",
            marker="!",
            destructive=True,
            on_accept=lambda: self._delete_record(str(record.get("id") or "")),
        )

    def _delete_record(self, record_id: str) -> None:
        try:
            removed = self.store.delete(record_id)
        except OSError as exc:
            self.toast.show_message(str(exc), kind="error", timeout_ms=6000)
            return
        self.refresh_all()
        if removed:
            self.toast.show_message("Survey result deleted. Live analysis refreshed.", kind="warning")
        else:
            self.toast.show_message("The selected survey result was no longer available.", kind="warning")

    def show_methodology(self) -> None:
        detail = (
            "Per dimension = (Agree + Strongly Agree) / valid non-N/A answers × 100\n"
            "Overall = the same positive-response rate across SQD1-SQD8\n"
            "SQD0 = reported separately for onsite responses\n"
            "N/A and unanswered/invalid answers are counted separately and excluded from valid denominators\n\n"
            "Interpretation bands\n"
            "Below 60% Poor · 60-79.99% Fair · 80-89.99% Satisfactory\n"
            "90-94.99% Very Satisfactory · 95-100% Outstanding\n\n"
            "Citizen's Charter results are mode-aware because the onsite and online CC questions use different meanings."
        )
        self.prompt.show_information(
            "ARTA-compatible scoring methodology",
            "The Dashboard uses a top-two-box positive-response method and preserves every denominator.",
            detail=detail,
        )

    def view_record(self, record: dict[str, Any]) -> None:
        mode = str(record.get("mode") or "onsite")
        meta = record.get("meta") if isinstance(record.get("meta"), dict) else {}
        cc = record.get("cc") if isinstance(record.get("cc"), dict) else {}
        sqd = record.get("sqd") if isinstance(record.get("sqd"), dict) else {}
        feedback = record.get("feedback")
        if isinstance(feedback, dict):
            comments = feedback.get("comments") or feedback.get("text") or ""
            email = feedback.get("email") or ""
        else:
            comments = str(feedback or "")
            email = ""
        analysis = AnalysisService.analyze([record])
        overview = analysis.get("overview", {})
        rate = overview.get("positive_rate")
        overall_text = "No Data" if rate is None else f"{rate:.2f}% {overview.get('rating', '')}"
        age = meta.get("age")
        age_text = "Not provided" if age in (None, "") else str(age)
        source_code = str(record.get("source_code") or "SCC").upper()
        source_label = {"WBS": "Browser Survey", "MRS": "Scanned Hardcopy", "SCC": "Manual Entry"}.get(source_code, source_code)
        lines = [
            f"Control No.: {record.get('control_number') or 'Not provided'}",
            f"Source: {source_label}",
            f"Mode: {mode.title()}",
            f"Date: {record.get('survey_date') or str(record.get('created_at') or '')[:10] or 'Not provided'}",
            f"Service: {service_display(service_value_from_record(record))}",
            f"Client: {meta.get('client_type') or 'Not provided'} · {meta.get('sex') or 'Not provided'} · Age {age_text}",
            f"Region: {meta.get('region') or 'Not provided'}",
            f"Overall SQD1-SQD8: {overall_text}",
            "",
            "Citizen's Charter",
        ]
        for key in ("cc1", "cc2", "cc3"):
            value = cc.get(key)
            label = self._cc_answer_label(mode, key, value)
            lines.append(f"{key.upper()}: {label}")
        if cc.get("cc3_reason"):
            lines.append(f"CC3 reason: {cc['cc3_reason']}")
        lines.extend(["", "Service Quality Dimensions"])
        rating_parts = []
        for key in SQD_QUESTIONS.get(mode, {}):
            value = sqd.get(key)
            rating = "N/A" if value == 0 else RATING_LABELS.get(value, "Unanswered")
            rating_parts.append(f"{key.upper()} {rating}")
        lines.append(" · ".join(rating_parts))
        if source_code == "MRS" or str(meta.get("submission_source") or "").casefold() == "scanned_hardcopy":
            lines.extend(
                [
                    "",
                    "Scanner Information",
                    f"Scanned by: {meta.get('scanner_operator_display_name') or 'Not recorded'}",
                    f"Username: {meta.get('scanner_operator_username') or 'Not recorded'}",
                    f"Operator User ID: {meta.get('scanner_operator_user_id') or 'Not recorded'}",
                    f"Scanner Session ID: {meta.get('scanner_session_id') or 'Not recorded'}",
                    f"Scanner Job ID: {meta.get('scanner_job_id') or 'Not recorded'}",
                    f"Original form control no.: {meta.get('source_form_control_number') or 'Not provided'}",
                    f"Image uploaded: {meta.get('scanner_image_uploaded_at') or 'Not recorded'}",
                    f"Processing started: {meta.get('scanner_processing_started_at') or 'Not recorded'}",
                    f"Processing completed: {meta.get('scanner_processing_completed_at') or 'Not recorded'}",
                    f"Review completed: {meta.get('scanner_review_completed_at') or 'Not recorded'}",
                    f"Finalized: {meta.get('scanner_finalized_at') or meta.get('scanner_verified_at') or 'Not recorded'}",
                    f"Template: {meta.get('scanner_template_id') or 'Not recorded'}",
                    f"Language: {meta.get('scanner_language') or 'Not recorded'}",
                    f"Recognition engine: {meta.get('scanner_processing_engine_version') or 'Not recorded'}",
                    f"Overall confidence: {meta.get('scanner_overall_confidence') if meta.get('scanner_overall_confidence') not in (None, '') else 'Not recorded'}",
                ]
            )
        if comments:
            lines.extend(["", f"Feedback: {comments}"])
        if email:
            lines.append(f"Email: {email}")
        self.prompt.show_information(
            "Survey result details",
            f"{service_display(service_value_from_record(record))} · {mode.title()}",
            detail="\n".join(lines),
        )

    def view_print_record(self, record: dict[str, Any]) -> None:
        settings = record.get("settings") if isinstance(record.get("settings"), dict) else {}
        generated_value = str(record.get("generated_at") or "")
        printed_value = str(record.get("printed_at") or generated_value)

        def display_timestamp(value: str) -> str:
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone().strftime(
                    "%d %B %Y, %I:%M:%S %p %Z"
                )
            except ValueError:
                return value or "Not available"

        pages = record.get("selected_pages")
        page_text = (
            ", ".join(str(page) for page in pages)
            if isinstance(pages, list) and pages
            else "All report pages"
        )
        margins = settings.get("margins_mm") if isinstance(settings.get("margins_mm"), dict) else {}
        margin_text = ", ".join(
            f"{side.title()} {value} mm" for side, value in margins.items()
        ) or "Printer default"
        lines = [
            f"Audit status: {str(record.get('status') or 'confirmed').title()}",
            f"Status detail: {record.get('status_message') or 'No additional detail'}",
            f"Generated: {display_timestamp(generated_value)}",
            f"Printed: {display_timestamp(printed_value)}",
            f"Scope: {record.get('scope') or 'Complete current Dashboard'}",
            f"Survey responses represented: {record.get('survey_count', 'Not recorded')}",
            f"Report pages: {record.get('page_count') or 'Not recorded'}",
            f"Pages sent: {page_text}",
            f"Copies: {record.get('copies') or 1}",
            f"Printer: {record.get('printer_name') or 'Not recorded'}",
            "",
            "Printer preferences",
            f"Paper: {settings.get('paper_size') or settings.get('paper') or 'Default'}",
            f"Orientation: {str(settings.get('orientation') or 'portrait').title()}",
            f"Color: {settings.get('color_mode') or settings.get('color') or 'Default'}",
            f"Duplex: {settings.get('duplex') or 'Off'}",
            f"Quality: {settings.get('resolution_dpi') or 'Default'} dpi",
            f"Collated: {'Yes' if settings.get('collated') else 'No'}",
            f"Margins: {margin_text}",
            "",
            f"Barcode value: {record.get('barcode_value') or record.get('control_number') or 'Not recorded'}",
            "The barcode identifies this system-generated Dashboard print record.",
        ]
        self.prompt.show_information(
            "Dashboard print record",
            str(record.get("control_number") or "Print control number unavailable"),
            detail="\n".join(lines),
        )

    @staticmethod
    def _cc_answer_label(mode: str, key: str, value: Any) -> str:
        for option in CC_QUESTIONS.get(mode, {}).get(key, {}).get("options", []):
            if option.get("value") == value:
                return str(option.get("label") or value)
        return "Not answered"

    def import_records_from_csv(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Import CSM survey history",
            str(Path.home()),
            "CSM survey history (*.csv);;CSV files (*.csv)",
        )
        if not filename:
            return
        try:
            parsed = parse_survey_history_csv(filename)
        except ValueError as exc:
            self.toast.show_message(str(exc), kind="error", timeout_ms=7000)
            return
        if not parsed.records:
            detail = "\n".join(parsed.errors[:12])
            self.prompt.show_information(
                "Import CSM survey history",
                "No valid survey rows were found in the selected CSV.",
                detail=detail,
            )
            return

        existing = self.store.list()
        existing_ids = {str(item.get("id") or "") for item in existing}
        existing_controls = {
            str(item.get("control_number") or "").strip().casefold()
            for item in existing
            if str(item.get("control_number") or "").strip()
        }
        duplicate_candidates = sum(
            1
            for item in parsed.records
            if (str(item.get("id") or "") in existing_ids)
            or (
                str(item.get("control_number") or "").strip()
                and str(item.get("control_number") or "").strip().casefold() in existing_controls
            )
        )
        details = [
            f"File: {parsed.path.name}",
            f"Valid rows: {parsed.valid_count}",
            f"Rows with parsing errors: {parsed.error_count}",
            f"Possible existing duplicates: {duplicate_candidates}",
            "",
            "Duplicate IDs and control numbers will be skipped. Dashboard ratings and performance bands will be recalculated from the imported answers.",
        ]
        if parsed.errors:
            details.extend(["", "First parsing warnings:", *parsed.errors[:8]])

        def perform_import() -> None:
            result = self.store.import_records(parsed.records)
            self.refresh_all()
            imported = int(result.get("imported_count") or 0)
            skipped = int(result.get("skipped_count") or 0)
            errors = int(result.get("error_count") or 0)
            kind = "success" if imported else "warning"
            self.toast.show_message(
                f"Imported {imported} response(s); skipped {skipped}; errors {errors}.",
                kind=kind,
                timeout_ms=7500,
            )

        self.prompt.show_prompt(
            "Import CSM survey history",
            f"Import {parsed.valid_count} recognized response row(s)?",
            detail="\n".join(details),
            accept_tooltip="Import recognized survey responses",
            cancel_tooltip="Cancel import",
            marker="⇧",
            on_accept=perform_import,
        )

    def export_records(self, records: object = None) -> None:
        selected = list(records) if isinstance(records, list) else self.store.list()
        if not selected:
            self.toast.show_message("There are no survey results to export for the current filter.", kind="warning")
            return
        export_dir = self.data_root / "exports"
        export_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = export_dir / f"DepEd_CSMS_Survey_History_{timestamp}.csv"
        fields = [
            "id", "control_number", "source_code", "source_label", "mode", "survey_date", "created_at", "client_type", "sex", "age",
            "region", "agency_visited", "service_availed", "scanned_by", "scanner_username", "scanner_user_id", "scanner_session_id", "scanner_job_id", "source_form_control_number", "cc1", "cc2", "cc3", "cc3_reason",
            *[f"sqd{number}" for number in range(9)], "overall_positive_rate", "performance_band", "feedback", "email",
        ]
        try:
            with path.open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=fields)
                writer.writeheader()
                for record in selected:
                    writer.writerow(self._export_row(record))
        except OSError as exc:
            self.toast.show_message(f"Export failed: {exc}", kind="error", timeout_ms=6500)
            return
        self.toast.show_message(f"Exported {len(selected)} result(s) to {path.name}.", kind="success", timeout_ms=5200)

    @staticmethod
    def _export_row(record: dict[str, Any]) -> dict[str, Any]:
        meta = record.get("meta") if isinstance(record.get("meta"), dict) else {}
        cc = record.get("cc") if isinstance(record.get("cc"), dict) else {}
        sqd = record.get("sqd") if isinstance(record.get("sqd"), dict) else {}
        feedback = record.get("feedback")
        if isinstance(feedback, dict):
            comments = feedback.get("comments") or feedback.get("text") or ""
            email = feedback.get("email") or ""
        else:
            comments = feedback or ""
            email = ""
        overview = AnalysisService.analyze([record]).get("overview", {})
        row = {
            "id": record.get("id", ""), "control_number": record.get("control_number", ""),
            "source_code": record.get("source_code", ""), "source_label": meta.get("source_label", ""),
            "mode": record.get("mode", ""), "survey_date": record.get("survey_date", ""),
            "created_at": record.get("created_at", ""), "client_type": meta.get("client_type", ""),
            "sex": meta.get("sex", ""), "age": meta.get("age", ""), "region": meta.get("region", ""),
            "agency_visited": meta.get("agency_visited", ""), "service_availed": service_display(service_value_from_record(record), fallback=""),
            "scanned_by": meta.get("scanner_operator_display_name", ""), "scanner_username": meta.get("scanner_operator_username", ""),
            "scanner_user_id": meta.get("scanner_operator_user_id", ""), "scanner_session_id": meta.get("scanner_session_id", ""),
            "scanner_job_id": meta.get("scanner_job_id", ""), "source_form_control_number": meta.get("source_form_control_number", ""),
            "cc1": cc.get("cc1", ""), "cc2": cc.get("cc2", ""), "cc3": cc.get("cc3", ""),
            "cc3_reason": cc.get("cc3_reason", ""), "overall_positive_rate": overview.get("positive_rate", ""),
            "performance_band": overview.get("rating", ""), "feedback": comments, "email": email,
        }
        for number in range(9):
            value = sqd.get(f"sqd{number}", "")
            row[f"sqd{number}"] = "N/A" if value == 0 else value
        return row

    def toggle_max_restore(self) -> None:
        self.title_bar.toggle_max_restore()

    def _create_resize_handles(self) -> dict[str, WindowResizeHandle]:
        specifications = {
            "left": (Qt.Edge.LeftEdge, Qt.CursorShape.SizeHorCursor),
            "right": (Qt.Edge.RightEdge, Qt.CursorShape.SizeHorCursor),
            "top": (Qt.Edge.TopEdge, Qt.CursorShape.SizeVerCursor),
            "bottom": (Qt.Edge.BottomEdge, Qt.CursorShape.SizeVerCursor),
            "top_left": (
                Qt.Edge.TopEdge | Qt.Edge.LeftEdge,
                Qt.CursorShape.SizeFDiagCursor,
            ),
            "top_right": (
                Qt.Edge.TopEdge | Qt.Edge.RightEdge,
                Qt.CursorShape.SizeBDiagCursor,
            ),
            "bottom_left": (
                Qt.Edge.BottomEdge | Qt.Edge.LeftEdge,
                Qt.CursorShape.SizeBDiagCursor,
            ),
            "bottom_right": (
                Qt.Edge.BottomEdge | Qt.Edge.RightEdge,
                Qt.CursorShape.SizeFDiagCursor,
            ),
        }
        return {
            name: WindowResizeHandle(
                self,
                edges,
                cursor,
                name,
                self.window_frame,
            )
            for name, (edges, cursor) in specifications.items()
        }

    def _layout_resize_handles(self) -> None:
        if not hasattr(self, "resize_handles"):
            return
        rect = self.window_frame.rect()
        width = rect.width()
        height = rect.height()
        edge = 6
        corner = 12
        geometries = {
            "left": (0, corner, edge, max(1, height - (corner * 2))),
            "right": (max(0, width - edge), corner, edge, max(1, height - (corner * 2))),
            "top": (corner, 0, max(1, width - (corner * 2)), edge),
            "bottom": (corner, max(0, height - edge), max(1, width - (corner * 2)), edge),
            "top_left": (0, 0, corner, corner),
            "top_right": (max(0, width - corner), 0, corner, corner),
            "bottom_left": (0, max(0, height - corner), corner, corner),
            "bottom_right": (
                max(0, width - corner),
                max(0, height - corner),
                corner,
                corner,
            ),
        }
        resizable = not self.isMaximized() and not self.isFullScreen()
        for name, handle in self.resize_handles.items():
            handle.setGeometry(*geometries[name])
            handle.setVisible(resizable)
            if resizable:
                handle.raise_()

    def _layout_content_overlays(self) -> None:
        if not hasattr(self, "drawer"):
            return
        self.drawer.setGeometry(self.shell.rect())
        self.prompt.setGeometry(self.shell.rect())
        self.history_clear_overlay.setGeometry(self.shell.rect())
        self.print_overlay.setGeometry(self.shell.rect())
        self.narrative_overlay.setGeometry(self.shell.rect())
        if hasattr(self, "gateway_setup_overlay"):
            for overlay in (
                self.gateway_setup_overlay,
                self.gateway_transfer_overlay,
                self.gateway_package_export_overlay,
                self.gateway_backup_export_overlay,
                self.gateway_diagnostics_overlay,
                self.gateway_details_overlay,
            ):
                overlay.setGeometry(self.shell.rect())
        if hasattr(self, "retirement_overlay"):
            self.retirement_overlay.setGeometry(self.window_frame.rect())
        for overlay in (
            self.mrs_workspace_overlay,
            self.server_workspace_overlay,
            self.school_workspace_overlay,
        ):
            overlay.setGeometry(self.shell.rect())
        if self.drawer.is_open:
            self.drawer._layout_overlay(open_state=True)
        for overlay in (
            self.mrs_workspace_overlay,
            self.server_workspace_overlay,
            self.school_workspace_overlay,
        ):
            if overlay.isVisible():
                overlay.raise_()
        if self.history_clear_overlay.isVisible():
            self.history_clear_overlay.raise_()
        if self.print_overlay.isVisible():
            self.print_overlay.raise_()
        if self.narrative_overlay.isVisible():
            self.narrative_overlay.raise_()

    def _sync_window_chrome(self) -> None:
        if not hasattr(self, "title_bar"):
            return
        maximized = self.isMaximized() or self.isFullScreen()
        self.title_bar.sync_window_state()
        self.window_frame.layout().setContentsMargins(
            0 if maximized else 1,
            0 if maximized else 1,
            0 if maximized else 1,
            0 if maximized else 1,
        )
        self.window_frame.setProperty("windowMaximized", maximized)
        style = self.window_frame.style()
        style.unpolish(self.window_frame)
        style.polish(self.window_frame)
        self.window_frame.update()
        self._layout_resize_handles()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        if (
            not self._explicit_exit_requested
            and self._background_startup_enabled
            and self.system_tray.available
        ):
            event.ignore()
            self.hide()
            if not self._background_notice_shown:
                self.system_tray.show_background_notice()
                self._background_notice_shown = True
            return
        self.prepare_for_application_quit()
        super().closeEvent(event)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt API
        if watched is self.shell and event.type() == QEvent.Type.Resize:
            self._layout_content_overlays()
        return super().eventFilter(watched, event)

    def changeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange and hasattr(self, "title_bar"):
            QTimer.singleShot(0, self._sync_window_chrome)

    def showEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().showEvent(event)
        QTimer.singleShot(0, self._apply_native_taskbar_icon)
        QTimer.singleShot(350, self._apply_native_taskbar_icon)

    def _apply_native_taskbar_icon(self) -> None:
        if bool(self.property("nativeTaskbarIconApplied")):
            return
        applied = apply_windows_window_icon(self, self.project_root)
        self.setProperty("nativeTaskbarIconApplied", bool(applied))

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        if hasattr(self, "title_bar"):
            self.title_bar.set_compact(self.width())
        self._layout_content_overlays()
        self._layout_resize_handles()

    def _apply_styles(self) -> None:
        self.window_frame.setStyleSheet(
            f"""
            QFrame#application_window_frame {{
                background: {theme.WINDOW_BG};
                border: 1px solid rgba(43, 217, 197, 0.58);
            }}
            QFrame#application_window_frame[windowMaximized="true"] {{
                border: none;
            }}
            """
        )
        self.shell.setStyleSheet(
            f"""
            QFrame#application_shell {{ background: {theme.WINDOW_BG}; }}
            QFrame#navigation_rail {{ background: {theme.TITLE_BG}; border-right: 1px solid {theme.DIVIDER}; }}
            QLabel#navigation_brand {{ color: {theme.ACCENT_TEAL}; background: rgba(43,217,197,0.08); border: 1px solid rgba(43,217,197,0.42); border-radius: 15px; font-size: 13px; font-weight: 900; }}
            QLabel#navigation_record_count {{ color: {theme.TEXT_SECONDARY}; background: rgba(98,219,255,0.08); border: 1px solid rgba(98,219,255,0.28); border-radius: 10px; font-size: 10px; font-weight: 800; }}
            QPushButton#csm_icon_button:checked {{ background: rgba(22,142,136,0.78); border-color: {theme.ACCENT_CYAN}; }}
            QStackedWidget#board_stack {{ background: {theme.WINDOW_BG}; }}
            QFrame#branding_footer {{ background: {theme.TITLE_BG}; border-top: 1px solid {theme.DIVIDER}; }}
            QLabel#footer_school_logo, QLabel#footer_mosslab_seal, QLabel#footer_deped_logo, QLabel#footer_mosslab_logo {{ background: transparent; border: none; }}
            QLabel#footer_school_name {{ color: {theme.TEXT_SECONDARY}; font-size: 11px; font-weight: 800; }}
                        """
        )
