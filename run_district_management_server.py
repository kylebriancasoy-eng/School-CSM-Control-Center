"""Compiled entry point for the separate School CSM district host."""

from __future__ import annotations

import argparse
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys

from PySide6.QtWidgets import QApplication, QMessageBox

from district_management_server import APPLICATION_ID, APPLICATION_NAME
from district_management_server.instance import AlreadyRunningError, SingleInstanceGuard
from district_management_server.paths import resolve_runtime_paths
from district_management_server.tray import DistrictManagementWindow


def _install_root() -> Path:
    if bool(getattr(sys, "frozen", False)):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _logger(path: Path) -> logging.Logger:
    logger = logging.getLogger("school_csm_district_host")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = RotatingFileHandler(
            path,
            maxBytes=2 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(message)s")
        )
        logger.addHandler(handler)
    return logger


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--background", action="store_true")
    parser.add_argument("--runtime-self-test", action="store_true")
    arguments, _unknown = parser.parse_known_args(argv)
    if arguments.runtime_self_test:
        try:
            from district_management_server.runtime_self_test import run_runtime_self_test

            run_runtime_self_test(_install_root())
        except Exception:
            logging.exception("District frozen-runtime self-test failed")
            return 2
        return 0
    paths = resolve_runtime_paths()
    paths.ensure_directories()
    logger = _logger(paths.log_file)
    application = QApplication.instance() or QApplication(sys.argv[:1])
    application.setApplicationName(APPLICATION_NAME)
    application.setApplicationDisplayName(APPLICATION_NAME)
    application.setOrganizationName("MoSSLab")
    application.setQuitOnLastWindowClosed(False)
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APPLICATION_ID)
    except Exception:
        pass

    guard = SingleInstanceGuard(paths.single_instance_lock)
    try:
        guard.acquire()
    except AlreadyRunningError as exc:
        QMessageBox.information(None, APPLICATION_NAME, str(exc))
        return 0
    try:
        window = DistrictManagementWindow(
            paths,
            _install_root(),
            start_hidden=bool(arguments.background),
        )
        application.aboutToQuit.connect(window.controller.stop)
        return int(application.exec())
    except Exception as exc:
        logger.exception("District host startup failed")
        QMessageBox.critical(
            None,
            APPLICATION_NAME,
            str(exc).strip() or "District Management Server could not start.",
        )
        return 1
    finally:
        guard.release()


if __name__ == "__main__":
    raise SystemExit(main())
