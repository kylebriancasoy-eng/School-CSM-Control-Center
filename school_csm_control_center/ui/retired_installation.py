"""Fail-closed UI for an installation whose Internet authority was revoked."""

from __future__ import annotations

import os
from pathlib import Path
import shlex
from typing import Any, Mapping

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCloseEvent, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QLabel,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from school_csm_control_center.app_identity import SHORT_APPLICATION_NAME
from school_csm_control_center.ui import theme


UNINSTALL_REGISTRY_PATH = (
    r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
    "\\MoSSLab.SchoolCSMControlCenter"
)


class RetirementMarkerError(RuntimeError):
    """Raised when a retirement marker exists but cannot be authenticated."""


def load_verified_retirement_record(project_root: str | Path) -> dict[str, Any] | None:
    """Read the signed marker before normal data migration or main-window startup."""

    try:
        from school_csm_control_center.storage.gateway_state import (
            GatewayStateStore,
            validate_retirement_record,
        )
    except ImportError as exc:
        raise RetirementMarkerError(
            "Internet Gateway retirement verification is unavailable."
        ) from exc

    store = GatewayStateStore(project_root)
    record = store.retirement_record()
    if record is None:
        return None
    if not isinstance(record, Mapping):
        raise RetirementMarkerError("The local retirement marker is malformed.")
    try:
        validation = validate_retirement_record(record)
    except Exception as exc:
        raise RetirementMarkerError(
            "The local retirement marker could not be authenticated."
        ) from exc
    if validation is False:
        raise RetirementMarkerError(
            "The local retirement marker could not be authenticated."
        )
    return dict(validation)


def _windows_command_line_to_argv(command: str) -> list[str]:
    value = str(command or "").strip()
    if not value:
        return []
    if os.name != "nt":
        return shlex.split(value, posix=False)
    import ctypes
    from ctypes import wintypes

    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    count = ctypes.c_int()
    shell32.CommandLineToArgvW.argtypes = (
        wintypes.LPCWSTR,
        ctypes.POINTER(ctypes.c_int),
    )
    shell32.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
    kernel32.LocalFree.argtypes = (wintypes.HLOCAL,)
    kernel32.LocalFree.restype = wintypes.HLOCAL
    pointer = shell32.CommandLineToArgvW(value, ctypes.byref(count))
    if not pointer:
        raise OSError(ctypes.get_last_error(), "Windows could not parse the uninstall command.")
    try:
        return [pointer[index] for index in range(count.value)]
    finally:
        kernel32.LocalFree(pointer)


def registered_uninstall_command() -> list[str]:
    if os.name != "nt":
        raise OSError("The registered Windows uninstaller is available only on Windows.")
    import winreg

    flags = [winreg.KEY_READ]
    if hasattr(winreg, "KEY_WOW64_32KEY"):
        flags.insert(0, winreg.KEY_READ | winreg.KEY_WOW64_32KEY)
    if hasattr(winreg, "KEY_WOW64_64KEY"):
        flags.append(winreg.KEY_READ | winreg.KEY_WOW64_64KEY)
    for access in flags:
        try:
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                UNINSTALL_REGISTRY_PATH,
                0,
                access,
            ) as key:
                value, _kind = winreg.QueryValueEx(key, "UninstallString")
        except FileNotFoundError:
            continue
        command = _windows_command_line_to_argv(str(value or ""))
        if command:
            return command
    raise FileNotFoundError(
        "The registered School CSM Control Center uninstaller could not be found."
    )


def launch_registered_uninstaller(*, wait_for_pid: int | None = None) -> None:
    command = registered_uninstall_command()
    executable = command[0]
    arguments = command[1:]
    if "--uninstall" not in {item.casefold() for item in arguments}:
        arguments.append("--uninstall")
    if wait_for_pid is not None:
        arguments.extend(("--wait-for-pid", str(int(wait_for_pid))))
    if os.name != "nt":
        raise OSError("The Windows uninstaller cannot be launched on this platform.")
    import ctypes
    import subprocess

    argument_line = subprocess.list2cmdline(arguments)
    result = ctypes.windll.shell32.ShellExecuteW(
        None,
        "runas",
        executable,
        argument_line,
        str(Path(executable).parent),
        1,
    )
    if int(result) <= 32:
        raise OSError(
            int(result), "Windows could not launch the registered uninstaller."
        )


class ForcedRetirementOverlay(QWidget):
    """Non-dismissible overlay shown immediately after verified revocation."""

    uninstall_requested = Signal()
    exit_requested = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("forced_retirement_overlay")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 28, 28, 28)
        outer.addStretch(1)
        self.panel = QFrame()
        self.panel.setObjectName("forced_retirement_panel")
        self.panel.setMaximumWidth(720)
        panel = QVBoxLayout(self.panel)
        panel.setContentsMargins(34, 32, 34, 30)
        panel.setSpacing(17)
        marker = QLabel("!")
        marker.setObjectName("forced_retirement_marker")
        marker.setAlignment(Qt.AlignmentFlag.AlignCenter)
        marker.setFixedSize(54, 54)
        panel.addWidget(marker, 0, Qt.AlignmentFlag.AlignHCenter)
        heading = QLabel("INTERNET SERVER TRANSFERRED")
        heading.setObjectName("forced_retirement_title")
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        panel.addWidget(heading)
        self.identity_label = QLabel()
        self.identity_label.setObjectName("forced_retirement_identity")
        self.identity_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.identity_label.setWordWrap(True)
        panel.addWidget(self.identity_label)
        message = QLabel(
            "This installation is no longer authorized to operate the School CSM Control Center for this School ID. The local server, Scanner Remote, Internet Gateway, and ordinary Control Center functions are disabled."
        )
        message.setObjectName("forced_retirement_message")
        message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        message.setWordWrap(True)
        panel.addWidget(message)
        preserved = QLabel(
            "Uninstalling preserves the school's saved CSM records by default. Application removal and local data removal remain separate operations."
        )
        preserved.setObjectName("forced_retirement_note")
        preserved.setAlignment(Qt.AlignmentFlag.AlignCenter)
        preserved.setWordWrap(True)
        panel.addWidget(preserved)
        self.status_label = QLabel()
        self.status_label.setObjectName("forced_retirement_status")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setWordWrap(True)
        self.status_label.hide()
        panel.addWidget(self.status_label)
        self.uninstall_button = QPushButton("UNINSTALL APPLICATION")
        self.uninstall_button.setObjectName("forced_retirement_uninstall")
        self.uninstall_button.setAccessibleName("Uninstall application")
        self.exit_button = QPushButton("EXIT")
        self.exit_button.setObjectName("forced_retirement_exit")
        self.exit_button.setAccessibleName("Exit application")
        panel.addWidget(self.uninstall_button)
        panel.addWidget(self.exit_button)
        outer.addWidget(self.panel, 0, Qt.AlignmentFlag.AlignHCenter)
        outer.addStretch(1)

        self.uninstall_button.clicked.connect(self.uninstall_requested.emit)
        self.exit_button.clicked.connect(self.exit_requested.emit)
        self._apply_styles()

    def apply_record(self, record: Mapping[str, Any]) -> None:
        school_name = " ".join(
            str(
                record.get("school_name")
                or record.get("registered_school_name")
                or "School"
            ).split()
        )
        school_id = str(record.get("school_id") or "Not available").strip()
        self.identity_label.setText(f"{school_name}\nSchool ID {school_id}")

    def set_status(self, message: str, *, error: bool = False) -> None:
        self.status_label.setText(str(message or ""))
        self.status_label.setProperty("errorState", bool(error))
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)
        self.status_label.setVisible(bool(message))

    def show_for_record(self, record: Mapping[str, Any]) -> None:
        self.apply_record(record)
        self.setGeometry(self.parentWidget().rect())
        self.show()
        self.raise_()
        self.exit_button.setFocus(Qt.FocusReason.ActiveWindowFocusReason)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            event.accept()
            return
        super().keyPressEvent(event)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self.parentWidget() is not None:
            self.setGeometry(self.parentWidget().rect())

    def _apply_styles(self) -> None:
        self.setStyleSheet(
            f"""
            QWidget#forced_retirement_overlay {{ background: rgba(1, 8, 18, 0.98); }}
            QFrame#forced_retirement_panel {{
                background: {theme.PANEL_BG}; border: 2px solid {theme.DANGER};
                border-radius: 20px;
            }}
            QLabel#forced_retirement_marker {{
                color: white; background: {theme.DANGER}; border-radius: 27px;
                font-size: 28px; font-weight: 900;
            }}
            QLabel#forced_retirement_title {{
                color: {theme.DANGER}; background: transparent;
                font-size: 22px; font-weight: 900;
            }}
            QLabel#forced_retirement_identity {{
                color: {theme.TEXT_PRIMARY}; background: transparent;
                font-size: 17px; font-weight: 800;
            }}
            QLabel#forced_retirement_message {{
                color: {theme.TEXT_SECONDARY}; background: transparent; font-size: 13px;
            }}
            QLabel#forced_retirement_note {{
                color: {theme.TEXT_PRIMARY}; background: rgba(245, 158, 11, 0.12);
                border: 1px solid rgba(245, 158, 11, 0.38);
                border-radius: 10px; padding: 10px; font-size: 12px;
            }}
            QLabel#forced_retirement_status {{ color: {theme.TEXT_SECONDARY}; background: transparent; }}
            QLabel#forced_retirement_status[errorState="true"] {{ color: {theme.DANGER}; }}
            QPushButton#forced_retirement_uninstall, QPushButton#forced_retirement_exit {{
                min-height: 44px; border-radius: 10px; font-size: 13px; font-weight: 850;
            }}
            QPushButton#forced_retirement_uninstall {{
                color: white; background: {theme.DANGER}; border: 1px solid {theme.DANGER};
            }}
            QPushButton#forced_retirement_exit {{
                color: {theme.TEXT_PRIMARY}; background: rgba(112, 139, 164, 0.12);
                border: 1px solid {theme.DIVIDER};
            }}
            """
        )


class RetiredInstallationWindow(QMainWindow):
    """Minimal startup window that never constructs the ordinary Control Center."""

    def __init__(
        self,
        retirement_record: Mapping[str, Any],
        *,
        icon: QIcon | None = None,
    ) -> None:
        super().__init__()
        self.setObjectName("retired_installation_window")
        self.setWindowTitle(f"{SHORT_APPLICATION_NAME} - Internet Server Transferred")
        if icon is not None:
            self.setWindowIcon(icon)
        self.setMinimumSize(680, 560)
        self.resize(820, 680)
        host = QWidget()
        self.setCentralWidget(host)
        self.overlay = ForcedRetirementOverlay(host)
        self.overlay.uninstall_requested.connect(self._launch_uninstaller)
        self.overlay.exit_requested.connect(self._exit)
        self.overlay.show_for_record(retirement_record)

    def _launch_uninstaller(self) -> None:
        try:
            launch_registered_uninstaller(wait_for_pid=os.getpid())
        except Exception as exc:
            self.overlay.set_status(str(exc), error=True)
            return
        self.overlay.set_status("Windows Setup is opening. This retired installation will now close.")
        self._exit()

    def _exit(self) -> None:
        app = QApplication.instance()
        self.close()
        if app is not None:
            app.quit()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        event.accept()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.overlay.setGeometry(self.centralWidget().rect())
