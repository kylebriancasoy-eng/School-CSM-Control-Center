from __future__ import annotations

import logging
import sys
from pathlib import Path

from PySide6.QtCore import QEventLoop, QTimer, qInstallMessageHandler
from PySide6.QtWidgets import QApplication

from school_csm_control_center.app_identity import (
    APPLICATION_SUBTITLE,
    FORMAL_APPLICATION_NAME,
    SHORT_APPLICATION_NAME,
    apply_application_identity,
    configure_windows_taskbar_identity,
)
from school_csm_control_center.mosslab_ui import MoSSLabStartupSplash
from school_csm_control_center.runtime_paths import (
    configure_runtime_paths,
    configured_runtime_paths,
)
from school_csm_control_center.ui import theme
from school_csm_control_center.ui.main_window import SchoolCSMControlCenterWindow


def _install_qt_message_logging() -> None:
    logger = logging.getLogger("school_csm_startup.qt")

    def handler(message_type, context, message) -> None:
        detail = str(message)
        if context is not None and getattr(context, "file", None):
            detail = f"{context.file}:{getattr(context, 'line', 0)} — {detail}"
        logger.warning("Qt message [%s]: %s", message_type, detail)

    qInstallMessageHandler(handler)


def run(project_root: str | Path, *, window_mode: str = "maximized") -> int:
    logger = logging.getLogger("school_csm_startup")
    project_root = Path(project_root).expanduser().resolve()
    runtime_paths = configured_runtime_paths()
    if runtime_paths is None or runtime_paths.install_root != project_root:
        runtime_paths = configure_runtime_paths(project_root)
    logger.info("Application run() entered with project root %s", project_root)
    logger.info("Application mutable data root: %s", runtime_paths.data_root)
    _install_qt_message_logging()
    logger.info("Configuring Windows taskbar identity.")
    configure_windows_taskbar_identity()
    logger.info("Creating QApplication.")
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName(SHORT_APPLICATION_NAME)
    app.setApplicationDisplayName(FORMAL_APPLICATION_NAME)
    app.setOrganizationName("Department of Education")
    logger.info("Applying application identity and theme.")
    apply_application_identity(app, project_root)
    theme.apply_theme(app)

    # Show the finished MoSSLab opening screen immediately and keep the main
    # window hidden until all boards, records, and styles have been prepared.
    # This avoids the blank/partially painted maximized frame seen at startup.
    logger.info("Creating MoSSLab startup splash.")
    splash = MoSSLabStartupSplash(
        FORMAL_APPLICATION_NAME,
        APPLICATION_SUBTITLE,
    )
    splash.set_progress(8, "Initializing application identity…")
    splash.show()
    app.processEvents()

    def report_progress(value: int, message: str) -> None:
        logger.info("Startup progress %s%% — %s", value, message)
        splash.set_progress(value, message)
        app.processEvents(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)

    report_progress(16, "Loading local records and settings…")
    logger.info("Constructing the main Control Center window.")
    window = SchoolCSMControlCenterWindow(project_root, startup_progress=report_progress)
    logger.info("Main Control Center window constructed.")
    report_progress(94, "Preparing the main workspace…")

    def reveal_window() -> None:
        # Do the first maximize/layout pass without repainting the unfinished
        # custom frame. The splash remains above the window until one complete
        # paint has landed, removing the dark/partial-frame startup flash.
        window.setUpdatesEnabled(False)
        if str(window_mode).strip().casefold() == "normal":
            window.show()
        else:
            window.showMaximized()
        app.processEvents(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
        window.setUpdatesEnabled(True)
        window.update()
        window.repaint()
        app.processEvents(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)

        report_progress(100, "Ready")

        def finish_startup() -> None:
            splash.finish(window)
            window.raise_()
            window.activateWindow()

        QTimer.singleShot(140, finish_startup)

    QTimer.singleShot(0, reveal_window)
    logger.info("Entering the Qt event loop.")
    return app.exec()
