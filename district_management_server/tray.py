"""Non-technical status window, setup dialog, and notification-area menu."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from threading import Thread
import webbrowser

from PySide6.QtCore import QObject, QTimer, Qt, Signal
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QInputDialog,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from .paths import DistrictRuntimePaths
from .recovery import create_recovery_backup, restore_recovery_backup
from .credentials import DistrictCredentialStore
from .server_controller import DistrictServerController
from .setup import DistrictSetupResult, configure_district_server
from .windows_startup import is_enabled, set_enabled


class _AsyncSignals(QObject):
    succeeded = Signal(object)
    failed = Signal(str)


class OneTimeAdministratorTokenDialog(QDialog):
    """Force acknowledgement before a newly generated token can disappear."""

    def __init__(self, token_value: str, parent=None) -> None:
        super().__init__(parent)
        self._acknowledged = False
        self.setWindowTitle("Save District Administrator Token")
        self.setMinimumWidth(650)
        heading = QLabel("New district administrator token")
        heading.setObjectName("title")
        explanation = QLabel(
            "Save this token now in the approved password manager. Only its "
            "SHA-256 digest is stored by the district server, so the token cannot "
            "be displayed again. Create a new encrypted recovery backup afterward."
        )
        explanation.setWordWrap(True)
        token = QLineEdit(str(token_value))
        token.setReadOnly(True)
        token.setEchoMode(QLineEdit.EchoMode.Password)
        show = QCheckBox("Show token")
        show.toggled.connect(
            lambda checked: token.setEchoMode(
                QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
            )
        )
        copy_button = QPushButton("Copy")
        copy_button.clicked.connect(
            lambda: QApplication.clipboard().setText(str(token_value))
        )
        row = QHBoxLayout()
        row.addWidget(token, 1)
        row.addWidget(copy_button)
        saved = QCheckBox("I saved this token in the approved password manager")
        finish = QPushButton("Finish")
        finish.setEnabled(False)
        saved.toggled.connect(finish.setEnabled)
        finish.clicked.connect(self._finish)
        layout = QVBoxLayout(self)
        layout.addWidget(heading)
        layout.addWidget(explanation)
        layout.addLayout(row)
        layout.addWidget(show)
        layout.addWidget(saved)
        layout.addWidget(finish)

    def _finish(self) -> None:
        self._acknowledged = True
        QApplication.clipboard().clear()
        self.accept()

    def reject(self) -> None:
        if not self._acknowledged:
            QMessageBox.information(
                self,
                "Save the district administrator token",
                "Save the one-time token, tick the confirmation box, and choose Finish.",
            )
            return
        QApplication.clipboard().clear()
        super().reject()

    def closeEvent(self, event) -> None:
        if not self._acknowledged:
            self.reject()
            event.ignore()
            return
        QApplication.clipboard().clear()
        event.accept()


class SetupDialog(QDialog):
    configured = Signal(object)

    def __init__(self, paths: DistrictRuntimePaths, install_root: Path, parent=None) -> None:
        super().__init__(parent)
        self.paths = paths
        self.install_root = install_root
        self._provisioning = False
        self._awaiting_token_acknowledgement = False
        self.signals = _AsyncSignals(self)
        self.signals.succeeded.connect(self._complete)
        self.signals.failed.connect(self._failed)
        self.setWindowTitle("Set Up District Management Server")
        self.setMinimumWidth(650)

        title = QLabel("Set Up the Motiong District Gateway")
        title.setObjectName("title")
        note = QLabel(
            "This creates the district management address and stores the scoped "
            "Cloudflare credentials in Windows Credential Manager. It does not "
            "collect or copy any school's survey responses."
        )
        note.setWordWrap(True)
        self.account = QLineEdit()
        self.account.setPlaceholderText("32-character Cloudflare Account ID")
        self.token = QLineEdit()
        self.token.setEchoMode(QLineEdit.EchoMode.Password)
        self.token.setPlaceholderText("Scoped Cloudflare API token")
        self.origin = QLineEdit(
            "https://management.motiong-district-csm-survey.workers.dev"
        )
        self.domain = QLineEdit("motiong-district-csm-survey.workers.dev")
        self.origin.setReadOnly(True)
        self.domain.setReadOnly(True)
        form = QFormLayout()
        form.addRow("Cloudflare Account ID", self.account)
        form.addRow("Scoped API token", self.token)
        form.addRow("District portal", self.origin)
        form.addRow("School address family", self.domain)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.create_button = QPushButton("Create District Server")
        self.cancel_button = QPushButton("Cancel")
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.create_button)
        layout = QVBoxLayout(self)
        layout.addWidget(title)
        layout.addWidget(note)
        layout.addLayout(form)
        layout.addWidget(self.status)
        layout.addLayout(buttons)
        self.create_button.clicked.connect(self._begin)
        self.cancel_button.clicked.connect(self.reject)

    def _begin(self) -> None:
        account = self.account.text().strip()
        token = self.token.text().strip()
        public_origin = self.origin.text().strip()
        managed_domain = self.domain.text().strip()
        if len(account) != 32 or not all(c in "0123456789abcdefABCDEF" for c in account):
            self.status.setText("Enter the 32-character Cloudflare Account ID.")
            return
        if len(token) < 20:
            self.status.setText("Enter the scoped Cloudflare API token.")
            return
        answer = QMessageBox.question(
            self,
            "Create Cloudflare resources?",
            "This will create one named tunnel, one narrow VPC Service, and the "
            "management Worker in the selected Cloudflare account. Continue?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.create_button.setEnabled(False)
        self.cancel_button.setEnabled(False)
        self.account.setEnabled(False)
        self.token.setEnabled(False)
        self._provisioning = True
        self.status.setText("Creating the secure district route…")

        def work() -> None:
            try:
                result = configure_district_server(
                    paths=self.paths,
                    install_root=self.install_root,
                    account_id=account,
                    cloudflare_api_token=token,
                    public_origin=public_origin,
                    managed_domain=managed_domain,
                )
            except Exception as exc:
                self.signals.failed.emit(str(exc).strip() or type(exc).__name__)
            else:
                self.signals.succeeded.emit(result)

        Thread(target=work, name="DistrictFirstRunSetup", daemon=True).start()

    def _failed(self, detail: str) -> None:
        self._provisioning = False
        self.token.clear()
        self.account.setEnabled(True)
        self.token.setEnabled(True)
        self.cancel_button.setEnabled(True)
        self.create_button.setEnabled(True)
        self.status.setText(f"Setup did not complete: {detail}")

    def _complete(self, result: DistrictSetupResult) -> None:
        self._provisioning = False
        self._awaiting_token_acknowledgement = True
        self.token.clear()
        token = QLineEdit(result.administrator_token)
        token.setReadOnly(True)
        token.setEchoMode(QLineEdit.EchoMode.Password)
        show = QCheckBox("Show token")
        show.toggled.connect(
            lambda checked: token.setEchoMode(
                QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
            )
        )
        copy_button = QPushButton("Copy")
        copy_button.clicked.connect(
            lambda: QApplication.clipboard().setText(result.administrator_token)
        )
        saved = QCheckBox("I saved this token in the approved password manager")
        finish = QPushButton("Finish")
        finish.setEnabled(False)
        saved.toggled.connect(finish.setEnabled)
        finish.clicked.connect(lambda: self._finish(result))

        while self.layout().count():
            item = self.layout().takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        heading = QLabel("District server created")
        heading.setObjectName("title")
        explanation = QLabel(
            "Save this district administrator token now. It is not stored and "
            "cannot be shown again. The school deployment profile was also created at:\n"
            f"{result.provider_file}"
        )
        explanation.setWordWrap(True)
        token_row = QHBoxLayout()
        token_row.addWidget(token, 1)
        token_row.addWidget(copy_button)
        self.layout().addWidget(heading)
        self.layout().addWidget(explanation)
        self.layout().addLayout(token_row)
        self.layout().addWidget(show)
        self.layout().addWidget(saved)
        self.layout().addWidget(finish)

    def _finish(self, result: DistrictSetupResult) -> None:
        self._awaiting_token_acknowledgement = False
        QApplication.clipboard().clear()
        self.configured.emit(result)
        self.accept()

    def reject(self) -> None:
        if self._provisioning:
            self.status.setText(
                "Setup is still creating the district route. Keep this window open."
            )
            return
        if self._awaiting_token_acknowledgement:
            QMessageBox.information(
                self,
                "Save the district administrator token",
                "Save the one-time district administrator token in the approved "
                "password manager, tick the confirmation box, and choose Finish.",
            )
            return
        QApplication.clipboard().clear()
        super().reject()

    def closeEvent(self, event) -> None:
        if self._provisioning or self._awaiting_token_acknowledgement:
            self.reject()
            event.ignore()
            return
        QApplication.clipboard().clear()
        event.accept()


class DistrictManagementWindow(QMainWindow):
    def __init__(
        self,
        paths: DistrictRuntimePaths,
        install_root: Path,
        *,
        start_hidden: bool = False,
    ) -> None:
        super().__init__()
        self.paths = paths
        self.install_root = install_root
        self._working = False
        self._exiting = False
        self._success_message = ""
        self._result_handler = None
        self.operation_signals = _AsyncSignals(self)
        self.operation_signals.succeeded.connect(self._finished)
        self.operation_signals.failed.connect(self._operation_failed)
        self.controller = DistrictServerController(paths, install_root)
        self.setWindowTitle("School CSM District Management Server")
        self.setMinimumSize(700, 430)
        icon_path = install_root / "School CSM Control Center Icon.ico"
        self.setWindowIcon(QIcon(str(icon_path)) if icon_path.is_file() else QIcon())

        title = QLabel("School CSM District Management Server")
        title.setObjectName("title")
        subtitle = QLabel(
            "Motiong district roster, activation, gateway provisioning, and safe infrastructure status"
        )
        subtitle.setWordWrap(True)
        self.state = QLabel("Stopped")
        self.state.setObjectName("state")
        self.detail = QLabel("Starting…")
        self.detail.setWordWrap(True)
        self.open_button = QPushButton("Open District Portal")
        self.restart_button = QPushButton("Start / Restart")
        self.stop_button = QPushButton("Stop")
        self.setup_button = QPushButton("Set Up District Server")
        self.backup_button = QPushButton("Create Encrypted Recovery Backup")
        self.restore_button = QPushButton("Restore Encrypted Recovery Backup")
        self.reset_token_button = QPushButton("Reset District Administrator Token")
        self.startup = QCheckBox("Start in the background when this operator signs in")
        self.startup.setChecked(is_enabled())
        self.provider_note = QLabel(
            f"School deployment profile: {paths.provider_export_file}"
        )
        self.provider_note.setWordWrap(True)

        row = QHBoxLayout()
        row.addWidget(self.open_button)
        row.addWidget(self.restart_button)
        row.addWidget(self.stop_button)
        layout = QVBoxLayout()
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addSpacing(18)
        layout.addWidget(self.state)
        layout.addWidget(self.detail)
        layout.addSpacing(18)
        layout.addLayout(row)
        layout.addWidget(self.setup_button)
        layout.addWidget(self.backup_button)
        layout.addWidget(self.restore_button)
        layout.addWidget(self.reset_token_button)
        layout.addWidget(self.startup)
        layout.addWidget(self.provider_note)
        layout.addStretch(1)
        holder = QWidget()
        holder.setLayout(layout)
        self.setCentralWidget(holder)
        self.setStyleSheet(_STYLE)

        self.tray = QSystemTrayIcon(self.windowIcon(), self)
        menu = QMenu()
        show_action = QAction("Open Status", self)
        portal_action = QAction("Open District Portal", self)
        restart_action = QAction("Start / Restart", self)
        stop_action = QAction("Stop", self)
        exit_action = QAction("Exit", self)
        menu.addAction(show_action)
        menu.addAction(portal_action)
        menu.addSeparator()
        menu.addAction(restart_action)
        menu.addAction(stop_action)
        menu.addSeparator()
        menu.addAction(exit_action)
        self.tray.setContextMenu(menu)
        self.tray.setToolTip("School CSM District Management Server")
        self.tray.show()

        show_action.triggered.connect(self._show)
        portal_action.triggered.connect(self._open_portal)
        restart_action.triggered.connect(self._restart)
        stop_action.triggered.connect(self._stop)
        exit_action.triggered.connect(self.exit_application)
        self.tray.activated.connect(
            lambda reason: self._show()
            if reason in (
                QSystemTrayIcon.ActivationReason.Trigger,
                QSystemTrayIcon.ActivationReason.DoubleClick,
            )
            else None
        )
        self.open_button.clicked.connect(self._open_portal)
        self.restart_button.clicked.connect(self._restart)
        self.stop_button.clicked.connect(self._stop)
        self.setup_button.clicked.connect(self._setup)
        self.backup_button.clicked.connect(self._create_backup)
        self.restore_button.clicked.connect(self._restore_backup)
        self.reset_token_button.clicked.connect(self._reset_administrator_token)
        self.startup.toggled.connect(self._toggle_startup)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh)
        self.timer.start(1000)
        self._refresh()
        if start_hidden:
            self.hide()
        else:
            self.show()
        if paths.configuration_file.is_file():
            QTimer.singleShot(200, self._restart)

    def _show(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()

    def _refresh(self) -> None:
        snapshot = self.controller.snapshot()
        configured = self.paths.configuration_file.is_file()
        self.state.setText(snapshot.state.replace("_", " ").title())
        self.detail.setText(snapshot.detail)
        self.open_button.setEnabled(bool(snapshot.portal_url))
        self.restart_button.setEnabled(configured and not self._working)
        self.stop_button.setEnabled(not self._working and snapshot.state != "stopped")
        self.setup_button.setVisible(not configured)
        self.setup_button.setEnabled(not self._working)
        self.backup_button.setVisible(configured)
        self.backup_button.setEnabled(not self._working)
        self.restore_button.setVisible(not configured)
        self.restore_button.setEnabled(not self._working)
        self.reset_token_button.setVisible(configured)
        self.reset_token_button.setEnabled(not self._working)

    def _run(
        self,
        operation,
        failure_prefix: str,
        success_message: str = "",
        result_handler=None,
    ) -> None:
        if self._working:
            return
        self._working = True
        self._success_message = str(success_message)
        self._result_handler = result_handler
        self._refresh()

        def work() -> None:
            try:
                result = operation()
            except Exception as exc:
                message = str(exc).strip() or type(exc).__name__
                self.operation_signals.failed.emit(f"{failure_prefix}\n{message}")
            else:
                self.operation_signals.succeeded.emit(result)

        Thread(target=work, name="DistrictHostOperation", daemon=True).start()

    def _finished(self, result=None) -> None:
        self._working = False
        message = self._success_message
        handler = self._result_handler
        self._success_message = ""
        self._result_handler = None
        self._refresh()
        if handler is not None:
            handler(result)
        elif message:
            QMessageBox.information(self, "District recovery", message)

    def _operation_failed(self, message: str) -> None:
        self._working = False
        self._success_message = ""
        self._result_handler = None
        title, _, detail = str(message).partition("\n")
        QMessageBox.warning(self, title or "District server", detail or title)
        self._refresh()

    def _restart(self) -> None:
        self._run(self.controller.restart, "District server could not start")

    def _stop(self) -> None:
        self._run(self.controller.stop, "District server could not stop")

    def _open_portal(self) -> None:
        url = self.controller.snapshot().portal_url
        if url:
            webbrowser.open(url)

    def _setup(self) -> None:
        dialog = SetupDialog(self.paths, self.install_root, self)
        dialog.configured.connect(lambda _result: self._restart())
        dialog.exec()
        self._refresh()

    def _create_backup(self) -> None:
        default_name = (
            self.paths.backups_dir
            / f"School-CSM-District-Recovery-{datetime.now():%Y%m%d-%H%M}.mossdistrict"
        )
        destination, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "Create Encrypted District Recovery Backup",
            str(default_name),
            "School CSM District Recovery (*.mossdistrict)",
        )
        if not destination:
            return
        password, accepted = QInputDialog.getText(
            self,
            "Recovery password",
            "Enter a new recovery password (at least 12 characters):",
            QLineEdit.EchoMode.Password,
        )
        if not accepted:
            return
        confirmation, accepted = QInputDialog.getText(
            self,
            "Confirm recovery password",
            "Enter the same recovery password again:",
            QLineEdit.EchoMode.Password,
        )
        if not accepted:
            return
        if password != confirmation:
            QMessageBox.warning(self, "Recovery password", "The passwords do not match.")
            return
        self._run(
            lambda: create_recovery_backup(self.paths, destination, password),
            "District recovery backup could not be created",
            "The encrypted district recovery backup was created. Store the file "
            "and its password separately in approved secure locations.",
        )

    def _restore_backup(self) -> None:
        source, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "Restore Encrypted District Recovery Backup",
            str(self.paths.backups_dir),
            "School CSM District Recovery (*.mossdistrict)",
        )
        if not source:
            return
        password, accepted = QInputDialog.getText(
            self,
            "Recovery password",
            "Enter the recovery backup password:",
            QLineEdit.EchoMode.Password,
        )
        if not accepted:
            return
        answer = QMessageBox.question(
            self,
            "Restore district server?",
            "Recovery is allowed only on an empty district host. It restores the "
            "matched database, configuration, deployment profile, and protected "
            "credentials. Continue?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        def restore_and_start() -> None:
            restore_recovery_backup(self.paths, source, password)
            self.controller.start()

        self._run(
            restore_and_start,
            "District recovery backup could not be restored",
            "The district recovery backup was restored and the service was started.",
        )

    def _reset_administrator_token(self) -> None:
        answer = QMessageBox.question(
            self,
            "Reset district administrator token?",
            "This signs out existing browser sessions and replaces the token used "
            "to enter the district portal. The new token will be shown once. Continue?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        def rotate() -> str:
            self.controller.stop()
            return DistrictCredentialStore().rotate_administrator_token()

        def show_token(value) -> None:
            dialog = OneTimeAdministratorTokenDialog(str(value), self)
            dialog.exec()
            self._restart()

        self._run(
            rotate,
            "District administrator token could not be reset",
            result_handler=show_token,
        )

    def _toggle_startup(self, enabled: bool) -> None:
        try:
            actual = set_enabled(bool(enabled))
        except Exception as exc:
            blocker = self.startup.blockSignals(True)
            self.startup.setChecked(not enabled)
            self.startup.blockSignals(blocker)
            QMessageBox.warning(self, "Startup setting", str(exc))
        else:
            blocker = self.startup.blockSignals(True)
            self.startup.setChecked(actual)
            self.startup.blockSignals(blocker)

    def closeEvent(self, event) -> None:
        if self._exiting:
            event.accept()
        else:
            event.ignore()
            self.hide()
            self.tray.showMessage(
                "District Management Server",
                "The district service continues in the notification area.",
                QSystemTrayIcon.MessageIcon.Information,
                2500,
            )

    def exit_application(self) -> None:
        self._exiting = True
        self.controller.stop()
        self.tray.hide()
        QApplication.instance().quit()


_STYLE = """
QWidget { background: #061625; color: #dcefff; font-family: 'Segoe UI'; font-size: 14px; }
QLabel#title { color: white; font-size: 24px; font-weight: 800; }
QLabel#state { color: #41e0ca; font-size: 28px; font-weight: 800; }
QPushButton { background: #123049; border: 1px solid #238ba0; border-radius: 8px; padding: 10px 15px; font-weight: 700; }
QPushButton:hover { background: #17405f; }
QPushButton:disabled { color: #668094; border-color: #29475b; }
QLineEdit { background: #071f31; border: 1px solid #28768a; border-radius: 6px; padding: 8px; }
QCheckBox { spacing: 8px; padding: 6px 0; }
"""
