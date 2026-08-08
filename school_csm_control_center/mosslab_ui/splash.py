from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from PySide6.QtCore import QEventLoop, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QFontMetrics,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QApplication, QLabel, QProgressBar, QSplashScreen


STANDARD_ID = "mosslab-startup-splash/v1"


@dataclass(frozen=True)
class SplashTheme:
    width: int = 760
    height: int = 440
    brand_rail_width: int = 248
    corner_radius: int = 18
    background_top: str = "#071B2C"
    background_bottom: str = "#061724"
    brand_surface_top: str = "#0B2A40"
    brand_surface_bottom: str = "#092235"
    outline: str = "#194660"
    title: str = "#F4F8FC"
    secondary: str = "#B8C7D8"
    muted: str = "#8FA8BC"
    teal: str = "#26D0C5"
    blue: str = "#2A9CE6"
    progress_track: str = "#15354A"
    font_family: str = "Segoe UI Variable"
    fallback_font_family: str = "Segoe UI"
    eyebrow_pt: float = 8.5
    title_pt: float = 23.0
    subtitle_pt: float = 10.5
    status_pt: float = 9.5
    percent_pt: float = 9.5
    version_pt: float = 8.5
    eyebrow: str = "MoSSLab Application Suite"
    default_status: str = "Preparing application…"


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def _bounded_float(
    value: Any,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def _color(value: Any, default: str) -> str:
    candidate = str(value or "").strip()
    return candidate if QColor(candidate).isValid() else default


def load_splash_theme(path: str | Path | None = None) -> SplashTheme:
    """Load editable design tokens, falling back safely when they are invalid."""

    defaults = SplashTheme()
    source = (
        Path(path).expanduser()
        if path is not None
        else Path(__file__).with_name("splash_theme.json")
    )
    try:
        payload = json.loads(source.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return defaults

    canvas = _mapping(payload.get("canvas"))
    colors = _mapping(payload.get("colors"))
    typography = _mapping(payload.get("typography"))
    strings = _mapping(payload.get("strings"))
    width = _bounded_int(canvas.get("width"), defaults.width, 640, 1200)
    height = _bounded_int(canvas.get("height"), defaults.height, 360, 760)
    rail = _bounded_int(
        canvas.get("brand_rail_width"),
        defaults.brand_rail_width,
        190,
        max(191, width - 360),
    )

    return SplashTheme(
        width=width,
        height=height,
        brand_rail_width=rail,
        corner_radius=_bounded_int(
            canvas.get("corner_radius"), defaults.corner_radius, 0, 48
        ),
        background_top=_color(
            colors.get("background_top"), defaults.background_top
        ),
        background_bottom=_color(
            colors.get("background_bottom"), defaults.background_bottom
        ),
        brand_surface_top=_color(
            colors.get("brand_surface_top"), defaults.brand_surface_top
        ),
        brand_surface_bottom=_color(
            colors.get("brand_surface_bottom"), defaults.brand_surface_bottom
        ),
        outline=_color(colors.get("outline"), defaults.outline),
        title=_color(colors.get("title"), defaults.title),
        secondary=_color(colors.get("secondary"), defaults.secondary),
        muted=_color(colors.get("muted"), defaults.muted),
        teal=_color(colors.get("teal"), defaults.teal),
        blue=_color(colors.get("blue"), defaults.blue),
        progress_track=_color(
            colors.get("progress_track"), defaults.progress_track
        ),
        font_family=str(
            typography.get("family") or defaults.font_family
        ).strip(),
        fallback_font_family=str(
            typography.get("fallback_family") or defaults.fallback_font_family
        ).strip(),
        eyebrow_pt=_bounded_float(
            typography.get("eyebrow_pt"), defaults.eyebrow_pt, 6.0, 16.0
        ),
        title_pt=_bounded_float(
            typography.get("title_pt"), defaults.title_pt, 16.0, 34.0
        ),
        subtitle_pt=_bounded_float(
            typography.get("subtitle_pt"), defaults.subtitle_pt, 8.0, 18.0
        ),
        status_pt=_bounded_float(
            typography.get("status_pt"), defaults.status_pt, 8.0, 16.0
        ),
        percent_pt=_bounded_float(
            typography.get("percent_pt"), defaults.percent_pt, 8.0, 16.0
        ),
        version_pt=_bounded_float(
            typography.get("version_pt"), defaults.version_pt, 7.0, 14.0
        ),
        eyebrow=str(strings.get("eyebrow") or defaults.eyebrow).strip(),
        default_status=str(
            strings.get("default_status") or defaults.default_status
        ).strip(),
    )


_LOADED_FONT_FAMILY = ""


def _font_family(theme: SplashTheme) -> str:
    global _LOADED_FONT_FAMILY
    if _LOADED_FONT_FAMILY:
        return _LOADED_FONT_FAMILY

    available = set(QFontDatabase.families())
    for candidate in (theme.font_family, theme.fallback_font_family):
        if candidate in available:
            _LOADED_FONT_FAMILY = candidate
            return candidate

    # Qt's offscreen Windows backend may not enumerate system fonts even
    # though the normal desktop backend does. Loading the operating system's
    # own UI font explicitly keeps previews/tests accurate without bundling or
    # redistributing a font file.
    if sys.platform.startswith("win"):
        windows_root = Path(os.environ.get("WINDIR") or r"C:\Windows")
        for filename in ("segoeui.ttf", "segoeuivariable.ttf"):
            font_path = windows_root / "Fonts" / filename
            if not font_path.is_file():
                continue
            font_id = QFontDatabase.addApplicationFont(str(font_path))
            if font_id < 0:
                continue
            families = QFontDatabase.applicationFontFamilies(font_id)
            if families:
                _LOADED_FONT_FAMILY = families[0]
                return _LOADED_FONT_FAMILY

    family = QFontDatabase.systemFont(
        QFontDatabase.SystemFont.GeneralFont
    ).family()
    _LOADED_FONT_FAMILY = family or "Sans Serif"
    return _LOADED_FONT_FAMILY


def _font(
    theme: SplashTheme,
    point_size: float,
    weight: QFont.Weight = QFont.Weight.Normal,
) -> QFont:
    font = QFont(_font_family(theme))
    font.setPointSizeF(point_size)
    font.setWeight(weight)
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    return font


def _build_background(theme: SplashTheme) -> QPixmap:
    pixmap = QPixmap(theme.width, theme.height)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    bounds = QRectF(0.75, 0.75, theme.width - 1.5, theme.height - 1.5)
    shell = QPainterPath()
    shell.addRoundedRect(bounds, theme.corner_radius, theme.corner_radius)

    painter.save()
    painter.setClipPath(shell)
    background = QLinearGradient(0, 0, theme.width, theme.height)
    background.setColorAt(0.0, QColor(theme.background_top))
    background.setColorAt(1.0, QColor(theme.background_bottom))
    painter.fillRect(pixmap.rect(), background)

    brand = QLinearGradient(0, 0, theme.brand_rail_width, theme.height)
    brand.setColorAt(0.0, QColor(theme.brand_surface_top))
    brand.setColorAt(1.0, QColor(theme.brand_surface_bottom))
    painter.fillRect(0, 0, theme.brand_rail_width, theme.height, brand)

    painter.setPen(QPen(QColor(theme.outline), 1.0))
    painter.drawLine(
        theme.brand_rail_width,
        26,
        theme.brand_rail_width,
        theme.height - 26,
    )

    accent = QColor(theme.teal)
    accent.setAlpha(26)
    painter.setPen(QPen(accent, 1.0))
    painter.drawEllipse(
        QRectF(
            theme.brand_rail_width - 66,
            -54,
            150,
            150,
        )
    )
    blue = QColor(theme.blue)
    blue.setAlpha(22)
    painter.setPen(QPen(blue, 1.0))
    painter.drawEllipse(
        QRectF(
            -64,
            theme.height - 108,
            154,
            154,
        )
    )
    painter.restore()

    painter.setPen(QPen(QColor(theme.outline), 1.2))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(shell)
    painter.end()
    return pixmap


class _ElidedLabel(QLabel):
    def __init__(self, text: str, parent: QSplashScreen) -> None:
        super().__init__(parent)
        self._full_text = ""
        self.setText(text)

    def setText(self, text: str) -> None:  # noqa: N802 - Qt API spelling
        self._full_text = str(text)
        self.setAccessibleDescription(self._full_text)
        self._refresh()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API spelling
        super().resizeEvent(event)
        self._refresh()

    def _refresh(self) -> None:
        if self.width() <= 0:
            QLabel.setText(self, self._full_text)
            return
        available = max(1, self.width() - 2)
        rendered = QFontMetrics(self.font()).elidedText(
            self._full_text,
            Qt.TextElideMode.ElideRight,
            available,
        )
        QLabel.setText(self, rendered)


class MoSSLabStartupSplash(QSplashScreen):
    """Code-rendered, DPI-aware MoSSLab startup screen.

    The transparent MoSSLab Logo is the only raster dependency. Application
    identity, status, percentage, progress, colors, typography, and geometry
    stay live and editable.
    """

    def __init__(
        self,
        title: str,
        subtitle: str,
        *,
        version: str = "",
        logo_path: str | Path | None = None,
        theme_path: str | Path | None = None,
        initial_status: str | None = None,
    ) -> None:
        self.theme = load_splash_theme(theme_path)
        flags = (
            Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
        )
        super().__init__(_build_background(self.theme), flags)
        self.setObjectName("mosslab_startup_splash")
        self.setAccessibleName(f"{title} loading screen")
        self.setAccessibleDescription(
            "MoSSLab application startup progress and status"
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._progress = 0

        rail = self.theme.brand_rail_width
        logo_box_width = max(120, rail - 40)
        logo_box_height = min(180, self.theme.height - 96)
        logo_y = max(38, (self.theme.height - logo_box_height) // 2)
        self.logo_label = QLabel(self)
        self.logo_label.setObjectName("mosslab_logo")
        self.logo_label.setAccessibleName("MoSSLab Logo")
        self.logo_label.setGeometry(20, logo_y, logo_box_width, logo_box_height)
        self.logo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.logo_label.setStyleSheet("background:transparent;border:none;")
        resolved_logo = (
            Path(logo_path).expanduser()
            if logo_path is not None
            else Path(__file__).parent / "assets" / "mosslab-logo.png"
        )
        logo = QPixmap(str(resolved_logo))
        if logo.isNull():
            self.logo_label.setText(
                "MoSSLab\nMotiong Schools\nSystems Laboratory"
            )
            self.logo_label.setFont(
                _font(self.theme, 15.0, QFont.Weight.DemiBold)
            )
            self.logo_label.setStyleSheet(
                f"color:{self.theme.title};background:transparent;border:none;"
            )
        else:
            self.logo_label.setPixmap(
                logo.scaled(
                    logo_box_width,
                    logo_box_height,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )

        content_x = rail + 40
        content_width = self.theme.width - content_x - 48
        self.eyebrow_label = QLabel(self.theme.eyebrow, self)
        self.eyebrow_label.setObjectName("splash_eyebrow")
        self.eyebrow_label.setGeometry(content_x, 58, content_width, 22)
        self.eyebrow_label.setFont(
            _font(self.theme, self.theme.eyebrow_pt, QFont.Weight.DemiBold)
        )
        self.eyebrow_label.setStyleSheet(
            f"color:{self.theme.teal};background:transparent;border:none;"
        )

        self.title_label = QLabel(str(title).strip(), self)
        self.title_label.setObjectName("splash_application_title")
        self.title_label.setAccessibleName("Application name")
        self.title_label.setGeometry(content_x, 88, content_width, 84)
        self.title_label.setWordWrap(True)
        self.title_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        self.title_label.setFont(
            _font(self.theme, self.theme.title_pt, QFont.Weight.DemiBold)
        )
        self.title_label.setStyleSheet(
            f"color:{self.theme.title};background:transparent;border:none;"
        )

        self.subtitle_label = QLabel(str(subtitle).strip(), self)
        self.subtitle_label.setObjectName("splash_application_subtitle")
        self.subtitle_label.setAccessibleName("Application description")
        self.subtitle_label.setGeometry(content_x, 176, content_width, 54)
        self.subtitle_label.setWordWrap(True)
        self.subtitle_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
        )
        self.subtitle_label.setFont(
            _font(self.theme, self.theme.subtitle_pt, QFont.Weight.Normal)
        )
        self.subtitle_label.setStyleSheet(
            f"color:{self.theme.secondary};background:transparent;border:none;"
        )

        status_y = self.theme.height - 127
        self.status_label = _ElidedLabel(
            initial_status or self.theme.default_status,
            self,
        )
        self.status_label.setObjectName("splash_status")
        self.status_label.setAccessibleName("Loading status")
        self.status_label.setGeometry(
            content_x,
            status_y,
            max(80, content_width - 62),
            24,
        )
        self.status_label.setFont(
            _font(self.theme, self.theme.status_pt, QFont.Weight.Medium)
        )
        self.status_label.setStyleSheet(
            f"color:{self.theme.muted};background:transparent;border:none;"
        )

        self.percent_label = QLabel("0%", self)
        self.percent_label.setObjectName("splash_percentage")
        self.percent_label.setAccessibleName("Loading percentage")
        self.percent_label.setGeometry(
            content_x + content_width - 54,
            status_y,
            54,
            24,
        )
        self.percent_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self.percent_label.setFont(
            _font(self.theme, self.theme.percent_pt, QFont.Weight.Bold)
        )
        self.percent_label.setStyleSheet(
            f"color:{self.theme.teal};background:transparent;border:none;"
        )

        self.progress_bar = QProgressBar(self)
        self.progress_bar.setObjectName("startup_progress_bar")
        self.progress_bar.setAccessibleName("Application loading progress")
        self.progress_bar.setGeometry(
            content_x,
            status_y + 34,
            content_width,
            9,
        )
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setStyleSheet(
            "QProgressBar{"
            f"background:{self.theme.progress_track};"
            "border:none;border-radius:4px;"
            "}"
            "QProgressBar::chunk{"
            "background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            f"stop:0 {self.theme.blue},stop:1 {self.theme.teal});"
            "border:none;border-radius:4px;"
            "}"
        )

        self.version_label = QLabel(str(version).strip(), self)
        self.version_label.setObjectName("splash_version")
        self.version_label.setAccessibleName("Application version")
        self.version_label.setGeometry(
            content_x,
            self.theme.height - 52,
            content_width,
            20,
        )
        self.version_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self.version_label.setFont(
            _font(self.theme, self.theme.version_pt, QFont.Weight.Normal)
        )
        self.version_label.setStyleSheet(
            f"color:{self.theme.muted};background:transparent;border:none;"
        )
        self.version_label.setVisible(bool(str(version).strip()))

    @property
    def progress(self) -> int:
        return self._progress

    def report(
        self,
        value: int,
        message: str = "",
        app: QApplication | None = None,
    ) -> None:
        """Advance startup progress monotonically and keep the UI responsive."""

        bounded = max(0, min(100, int(value)))
        self._progress = max(self._progress, bounded)
        status = str(message or self.theme.default_status).strip()
        self.progress_bar.setValue(self._progress)
        self.progress_bar.setAccessibleDescription(
            f"{self._progress} percent. {status}"
        )
        self.percent_label.setText(f"{self._progress}%")
        self.status_label.setText(status)
        self.repaint()
        active_app = app or QApplication.instance()
        if active_app is not None:
            active_app.processEvents(
                QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents
            )

    def set_progress(self, value: int, message: str = "") -> None:
        """Compatibility alias used by existing MoSSLab applications."""

        self.report(value, message)
