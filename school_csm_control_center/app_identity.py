from __future__ import annotations

from pathlib import Path
import sys

from PySide6.QtGui import QGuiApplication, QIcon
from PySide6.QtWidgets import QApplication, QWidget


APP_ICON_CANDIDATES = (
    "School CSM Control Center Icon.ico",
    "School CSM Control Center Icon.png",
)

APP_LOGO_CANDIDATES = (
    Path("assets") / "CSM App Logo.png",
    Path("School CSM Control Center Icon.png"),
)
FORMAL_APPLICATION_NAME = "DepEd Client Satisfaction Measurement System"
SHORT_APPLICATION_NAME = "DepEd Client Satisfaction Measurement System"
APPLICATION_SUBTITLE = "Local Survey Management and Analysis System"
WINDOWS_APP_USER_MODEL_ID = "DepEd.SchoolCSMControlCenter"


def resolve_application_icon(project_root: str | Path) -> Path | None:
    root = Path(project_root).expanduser().resolve()
    for filename in APP_ICON_CANDIDATES:
        candidate = root / filename
        if candidate.is_file():
            return candidate
    return None


def resolve_application_logo(project_root: str | Path) -> Path | None:
    """Return the printable PNG application logo, when packaged."""
    root = Path(project_root).expanduser().resolve()
    for relative in APP_LOGO_CANDIDATES:
        candidate = root / relative
        if candidate.is_file():
            return candidate
    return None


def load_application_icon(project_root: str | Path) -> QIcon:
    path = resolve_application_icon(project_root)
    return QIcon(str(path)) if path is not None else QIcon()


def apply_application_identity(app: QApplication, project_root: str | Path) -> QIcon:
    path = resolve_application_icon(project_root)
    icon = QIcon(str(path)) if path is not None else QIcon()
    app.setProperty("applicationIconPath", str(path) if path is not None else "")
    if not icon.isNull():
        app.setWindowIcon(icon)
    return icon


def configure_windows_taskbar_identity() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        set_app_id = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID
        set_app_id.argtypes = [ctypes.c_wchar_p]
        set_app_id.restype = ctypes.c_long
        result = set_app_id(WINDOWS_APP_USER_MODEL_ID)
    except (AttributeError, OSError):
        return False
    return int(result) == 0


def apply_windows_window_icon(window: QWidget, project_root: str | Path) -> bool:
    icon_path = resolve_application_icon(project_root)
    if (
        icon_path is None
        or sys.platform != "win32"
        or QGuiApplication.platformName().casefold() != "windows"
    ):
        return False
    try:
        import ctypes
        from ctypes import wintypes

        window.setWindowIcon(QIcon(str(icon_path)))
        window_handle = wintypes.HWND(int(window.winId()))
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        load_image = user32.LoadImageW
        load_image.argtypes = [
            wintypes.HINSTANCE,
            wintypes.LPCWSTR,
            wintypes.UINT,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.UINT,
        ]
        load_image.restype = wintypes.HANDLE
        send_message = user32.SendMessageW
        send_message.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            ctypes.c_size_t,
            ctypes.c_ssize_t,
        ]
        send_message.restype = ctypes.c_ssize_t
        image_icon = 1
        load_from_file = 0x0010
        default_size = 0x0040
        big_icon = load_image(None, str(icon_path), image_icon, 0, 0, load_from_file | default_size)
        small_icon = load_image(None, str(icon_path), image_icon, 16, 16, load_from_file)
        if not big_icon and not small_icon:
            return False
        big_handle = int(big_icon or small_icon)
        small_handle = int(small_icon or big_icon)
        wm_set_icon = 0x0080
        send_message(window_handle, wm_set_icon, 1, big_handle)
        send_message(window_handle, wm_set_icon, 0, small_handle)
        set_class_icon = getattr(user32, "SetClassLongPtrW", None)
        if set_class_icon is None:
            set_class_icon = user32.SetClassLongW
        set_class_icon.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        set_class_icon.restype = ctypes.c_ssize_t
        set_class_icon(window_handle, -14, big_handle)
        set_class_icon(window_handle, -34, small_handle)
        setattr(window, "_native_taskbar_icon_handles", (big_handle, small_handle))
        window.setProperty("nativeTaskbarIconApplied", True)
        return True
    except Exception:
        return False

MOSS_LAB_LOGO_CANDIDATES = (
    Path("assets") / "mosslab_logo_ui.png",
    Path("assets") / "MoSSLab Logo.png",
)
MOSS_LAB_SEAL_CANDIDATES = (
    Path("assets") / "mosslab_seal_ui.png",
    Path("assets") / "MoSSLab Seal.png",
)

DEPED_LOGO_CANDIDATES = (
    Path("assets") / "deped_logo_ui.png",
    Path("assets") / "DepEd Logo.png",
)

def _resolve_brand_asset(project_root: str | Path, candidates: tuple[Path, ...]) -> Path | None:
    root = Path(project_root).expanduser().resolve()
    for relative in candidates:
        candidate = root / relative
        if candidate.is_file():
            return candidate
    return None


def resolve_mosslab_logo(project_root: str | Path) -> Path | None:
    """Return the fixed-area MoSSLab logo asset, when packaged."""
    return _resolve_brand_asset(project_root, MOSS_LAB_LOGO_CANDIDATES)


def resolve_mosslab_seal(project_root: str | Path) -> Path | None:
    """Return the MoSSLab seal asset, when packaged."""
    return _resolve_brand_asset(project_root, MOSS_LAB_SEAL_CANDIDATES)


def resolve_deped_logo(project_root: str | Path) -> Path | None:
    """Return the official DepEd logo asset, when packaged."""
    return _resolve_brand_asset(project_root, DEPED_LOGO_CANDIDATES)
