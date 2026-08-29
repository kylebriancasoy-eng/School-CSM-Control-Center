from __future__ import annotations

import argparse
from datetime import datetime
import faulthandler
import importlib.metadata
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import platform
import sys
import threading
import traceback


_LOG_FILE_HANDLE = None


def parse_startup_arguments(argv: list[str] | None = None) -> tuple[bool, bool]:
    """Return ``(start_hidden, auto_start_server)`` for supported launch flags."""

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--background", action="store_true")
    parser.add_argument("--auto-start-server", action="store_true")
    parser.add_argument("--show", action="store_true")
    options, _unknown = parser.parse_known_args(
        list(sys.argv[1:] if argv is None else argv)
    )
    start_hidden = bool(options.background and not options.show)
    return start_hidden, bool(options.auto_start_server and start_hidden)


def _safe_write(path: Path, text: str) -> None:
    try:
        with path.open("a", encoding="utf-8", errors="replace") as handle:
            handle.write(text)
            if not text.endswith("\n"):
                handle.write("\n")
    except Exception:
        pass


def _configure_debug_logging(log_path: Path) -> tuple[logging.Logger, Path]:
    """Create an always-on startup log before importing application modules."""
    global _LOG_FILE_HANDLE

    log_path.parent.mkdir(parents=True, exist_ok=True)
    separator = "=" * 88
    _safe_write(
        log_path,
        f"\n{separator}\n"
        f"School CSM Control Center launch attempt\n"
        f"Started: {datetime.now().astimezone().isoformat(timespec='seconds')}\n"
        f"{separator}\n",
    )

    logger = logging.getLogger("school_csm_startup")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    logger.propagate = False

    try:
        handler = RotatingFileHandler(
            log_path,
            mode="a",
            maxBytes=2 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
            delay=False,
        )
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s | %(levelname)-8s | %(threadName)s | %(name)s | %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        logger.addHandler(handler)
    except Exception:
        # The raw writer above remains the last-resort logger.
        pass

    try:
        _LOG_FILE_HANDLE = log_path.open("a", encoding="utf-8", errors="replace")
        faulthandler.enable(file=_LOG_FILE_HANDLE, all_threads=True)
    except Exception:
        _LOG_FILE_HANDLE = None

    def unhandled_exception(exc_type, exc_value, exc_traceback) -> None:
        if exc_type is KeyboardInterrupt:
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        logger.critical(
            "Unhandled exception",
            exc_info=(exc_type, exc_value, exc_traceback),
        )

    sys.excepthook = unhandled_exception

    if hasattr(threading, "excepthook"):
        def thread_exception(args) -> None:
            logger.critical(
                "Unhandled exception in thread %s",
                getattr(args.thread, "name", "unknown"),
                exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
            )
        threading.excepthook = thread_exception

    return logger, log_path


def _show_fatal_message(message: str, log_path: Path) -> None:
    """Show a useful error even when launched through pythonw.exe."""
    if os.name != "nt":
        return
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(
            None,
            f"{message}\n\nDiagnostic details were written to:\n{log_path}",
            "School CSM Control Center — Startup Error",
            0x10,
        )
    except Exception:
        pass


def _log_environment(
    logger: logging.Logger,
    project_root: Path,
    data_root: Path,
) -> None:
    logger.info("Installation root: %s", project_root)
    logger.info("Mutable data root: %s", data_root)
    logger.info("Current working directory: %s", Path.cwd())
    logger.info("Python executable: %s", sys.executable)
    logger.info("Python version: %s", sys.version.replace("\n", " "))
    logger.info("Platform: %s", platform.platform())
    logger.info("Process ID: %s", os.getpid())
    logger.info("Arguments: %r", sys.argv)
    logger.info("Frozen executable: %s", bool(getattr(sys, "frozen", False)))

    required_paths = [
        project_root
        / "school_csm_control_center"
        / "mosslab_ui"
        / "splash_theme.json",
        project_root
        / "school_csm_control_center"
        / "mosslab_ui"
        / "assets"
        / "mosslab-logo.png",
        project_root / "assets" / "mosslab_logo_ui.png",
        data_root / "data" / "csm_survey",
    ]
    if not getattr(sys, "frozen", False):
        required_paths[:0] = [
            project_root / "school_csm_control_center",
            project_root / "school_csm_control_center" / "app.py",
            project_root / "school_csm_control_center" / "mosslab_ui" / "splash.py",
        ]
    for path in required_paths:
        logger.info("Required path %s: %s", path.name, "FOUND" if path.exists() else "MISSING")

    for distribution in (
        "PySide6",
        "qrcode",
        "Pillow",
        "numpy",
        "opencv-contrib-python-headless",
    ):
        try:
            logger.info("Dependency %s version: %s", distribution, importlib.metadata.version(distribution))
        except importlib.metadata.PackageNotFoundError:
            logger.error("Dependency %s: NOT INSTALLED", distribution)
        except Exception as exc:
            logger.warning("Could not inspect dependency %s: %s", distribution, exc)


def main() -> int:
    start_hidden, auto_start_server = parse_startup_arguments()
    project_root = Path(__file__).resolve().parent
    try:
        from school_csm_control_center.runtime_paths import (
            configure_runtime_paths,
            migrate_legacy_data,
        )
        runtime_paths = configure_runtime_paths(project_root)
    except Exception:
        fallback_log = project_root / "DEBUG_LOG.txt"
        logger, log_path = _configure_debug_logging(fallback_log)
        logger.exception("Unable to initialize the stable MoSSLab Data directory.")
        _show_fatal_message(
            "The application could not prepare its MoSSLab Data folder.",
            log_path,
        )
        return 4

    logger, log_path = _configure_debug_logging(runtime_paths.log_file)
    _log_environment(logger, project_root, runtime_paths.data_root)

    try:
        from school_csm_control_center.runtime_instance import (
            AlreadyRunningError,
            SingleInstanceGuard,
            activate_existing_window,
        )
        instance_guard = SingleInstanceGuard(runtime_paths.single_instance_lock)
        instance_guard.acquire()
    except AlreadyRunningError as exc:
        logger.info("%s", exc)
        from school_csm_control_center.app_identity import SHORT_APPLICATION_NAME

        if activate_existing_window(SHORT_APPLICATION_NAME):
            logger.info("Activated the existing Control Center window.")
            return 0
        _show_fatal_message(
            str(exc)
            + "\n\nUse the School CSM icon in the Windows notification area to open it.",
            log_path,
        )
        return 5
    except Exception as exc:
        logger.exception("Unable to establish single-instance protection.")
        _show_fatal_message(
            f"The application could not secure its data workspace: {exc}",
            log_path,
        )
        return 6

    # A signed retirement marker is an authorization boundary, not ordinary
    # application data. Check it before copying or opening any legacy records
    # and never construct the normal Control Center when it is present.
    try:
        from school_csm_control_center.ui.retired_installation import (
            load_verified_retirement_record,
        )

        retirement_record = load_verified_retirement_record(project_root)
    except Exception as exc:
        logger.exception("The Internet Server retirement marker could not be verified.")
        instance_guard.release()
        _show_fatal_message(
            "This installation has an Internet Server retirement marker that could not be verified. Normal startup remains blocked. Run Setup and choose Repair or contact the gateway administrator.",
            log_path,
        )
        return 8
    if retirement_record is not None:
        logger.warning(
            "Verified Internet Server retirement marker found for School ID %s; normal startup is blocked.",
            retirement_record.get("school_id", ""),
        )
        try:
            from school_csm_control_center.app import run_retired_installation

            return int(run_retired_installation(project_root, retirement_record))
        finally:
            instance_guard.release()

    try:
        migration = migrate_legacy_data(runtime_paths)
        logger.info(
            "Legacy-data migration: copied=%s existing=%s conflicts=%s already_completed=%s manifest=%s",
            migration.copied_files,
            migration.existing_files,
            len(migration.conflicts),
            migration.already_completed,
            migration.manifest_path,
        )
        if migration.conflicts:
            logger.warning(
                "Legacy-data conflicts were preserved for review: %s",
                ", ".join(migration.conflicts),
            )
    except Exception as exc:
        logger.exception("Legacy-data migration failed before application startup.")
        instance_guard.release()
        _show_fatal_message(
            f"The application could not safely migrate its existing records: {exc}",
            log_path,
        )
        return 7

    try:
        logger.info("Importing the PySide6 application module.")
        from school_csm_control_center.app import run
        logger.info("Application module imported successfully.")

        result = int(
            run(
                project_root,
                start_hidden=start_hidden,
                auto_start_server=auto_start_server,
            )
        )
        logger.info("Qt event loop ended with return code %s.", result)
        return result
    except ModuleNotFoundError as exc:
        logger.exception("A required Python module is missing.")
        guidance = (
            "Open School CSM Control Center Setup and choose Repair."
            if getattr(sys, "frozen", False)
            else "Run INSTALL_REQUIREMENTS.cmd, then start the application again."
        )
        _show_fatal_message(
            f"A required Python module is missing: {exc.name}.\n"
            + guidance,
            log_path,
        )
        return 10
    except BaseException as exc:
        logger.critical("Fatal application startup failure: %s", exc)
        logger.debug("Full traceback:\n%s", traceback.format_exc())
        _show_fatal_message(f"The application could not start: {exc}", log_path)
        return 11
    finally:
        instance_guard.release()


if __name__ == "__main__":
    raise SystemExit(main())
