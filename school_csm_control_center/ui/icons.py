from __future__ import annotations

"""Painter-drawn action icons used by the CSM interface.

Keeping icons in code gives the standalone module crisp, scalable controls
without adding an asset or icon-font dependency.
"""

from functools import lru_cache

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

from school_csm_control_center.ui import theme


ICON_NAMES = frozenset(
    {
        "add",
        "close",
        "save",
        "reset",
        "export",
        "info",
        "edit",
        "delete",
        "filter",
        "check",
        "cancel",
        "calendar",
        "dashboard",
        "history",
        "server",
        "view",
        "lock",
        "previous",
        "next",
        "minimize",
        "maximize",
        "restore",
        "expand",
        "collapse",
        "print",
        "zoom_in",
        "zoom_out",
        "fit_page",
        "fit_width",
        "play",
        "stop",
        "copy",
        "open",
        "settings",
        "wifi",
        "qr",
        "key",
        "school",
        "upload",
    }
)


def _color_key(color: str | QColor) -> str:
    value = QColor(color)
    if not value.isValid():
        raise ValueError(f"Invalid icon color: {color!r}")
    return value.name(QColor.NameFormat.HexArgb)


def action_icon(
    name: str,
    color: str | QColor = theme.ACCENT_CYAN,
    size: int = 64,
) -> QIcon:
    """Return a neon-style icon for a supported action name.

    Unknown names are rejected instead of silently showing the wrong action.
    """

    normalized_name = str(name or "").strip().lower()
    if normalized_name not in ICON_NAMES:
        supported = ", ".join(sorted(ICON_NAMES))
        raise ValueError(
            f"Unsupported CSM action icon {name!r}. Supported icons: {supported}."
        )
    logical_size = max(16, min(int(size), 256))
    return QIcon(_icon_pixmap(normalized_name, _color_key(color), logical_size))


@lru_cache(maxsize=192)
def _icon_pixmap(name: str, color_key: str, size: int) -> QPixmap:
    # Icons are drawn in a stable 64-unit coordinate system and then scaled.
    canvas = max(64, size * 2)
    pixmap = QPixmap(canvas, canvas)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.scale(canvas / 64.0, canvas / 64.0)
    painter.setBrush(Qt.BrushStyle.NoBrush)

    base = QColor(color_key)
    for width, alpha in ((9.0, 24), (5.0, 54), (2.8, 255)):
        stroke = QColor(base)
        stroke.setAlpha(alpha)
        painter.setPen(
            QPen(
                stroke,
                width,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
        )
        _draw_shape(painter, name)

    painter.end()
    if canvas != size:
        pixmap = pixmap.scaled(
            size,
            size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    return pixmap


def _draw_shape(painter: QPainter, name: str) -> None:

    if name == "play":
        path = QPainterPath()
        path.moveTo(20, 12)
        path.lineTo(52, 32)
        path.lineTo(20, 52)
        path.closeSubpath()
        painter.drawPath(path)
        return

    if name == "stop":
        painter.drawRoundedRect(QRectF(16, 16, 32, 32), 3, 3)
        return

    if name == "copy":
        painter.drawRoundedRect(QRectF(12, 18, 30, 34), 3, 3)
        painter.drawRoundedRect(QRectF(22, 10, 30, 34), 3, 3)
        return

    if name == "open":
        painter.drawRoundedRect(QRectF(11, 20, 33, 33), 3, 3)
        painter.drawLine(31, 11, 53, 11)
        painter.drawLine(53, 11, 53, 33)
        painter.drawLine(52, 12, 29, 35)
        return

    if name == "settings":
        painter.drawEllipse(QRectF(23, 23, 18, 18))
        painter.drawEllipse(QRectF(13, 13, 38, 38))
        for x1, y1, x2, y2 in (
            (32, 7, 32, 15), (32, 49, 32, 57),
            (7, 32, 15, 32), (49, 32, 57, 32),
            (14, 14, 20, 20), (44, 44, 50, 50),
            (50, 14, 44, 20), (20, 44, 14, 50),
        ):
            painter.drawLine(x1, y1, x2, y2)
        return

    if name == "wifi":
        painter.drawArc(QRectF(8, 13, 48, 42), 38 * 16, 104 * 16)
        painter.drawArc(QRectF(16, 23, 32, 28), 38 * 16, 104 * 16)
        painter.drawArc(QRectF(24, 33, 16, 14), 38 * 16, 104 * 16)
        painter.drawPoint(32, 51)
        return

    if name == "qr":
        for x, y in ((10, 10), (40, 10), (10, 40)):
            painter.drawRect(QRectF(x, y, 14, 14))
            painter.drawRect(QRectF(x + 4, y + 4, 6, 6))
        painter.drawLine(38, 38, 54, 38)
        painter.drawLine(38, 38, 38, 54)
        painter.drawLine(46, 46, 54, 46)
        painter.drawLine(46, 46, 46, 54)
        painter.drawLine(54, 46, 54, 54)
        return

    if name == "key":
        painter.drawEllipse(QRectF(9, 15, 25, 25))
        painter.drawLine(31, 34, 54, 52)
        painter.drawLine(42, 43, 48, 37)
        painter.drawLine(48, 48, 53, 43)
        return

    if name == "school":
        roof = QPainterPath()
        roof.moveTo(8, 27)
        roof.lineTo(32, 10)
        roof.lineTo(56, 27)
        painter.drawPath(roof)
        painter.drawRoundedRect(QRectF(13, 25, 38, 30), 2, 2)
        painter.drawRect(QRectF(27, 38, 10, 17))
        painter.drawLine(21, 32, 21, 39)
        painter.drawLine(43, 32, 43, 39)
        return

    if name == "upload":
        painter.drawLine(32, 47, 32, 13)
        painter.drawLine(20, 25, 32, 13)
        painter.drawLine(44, 25, 32, 13)
        tray = QPainterPath()
        tray.moveTo(12, 41)
        tray.lineTo(12, 54)
        tray.lineTo(52, 54)
        tray.lineTo(52, 41)
        painter.drawPath(tray)
        return

    if name == "add":
        painter.drawLine(32, 13, 32, 51)
        painter.drawLine(13, 32, 51, 32)
        return

    if name == "close":
        painter.drawLine(16, 16, 48, 48)
        painter.drawLine(48, 16, 16, 48)
        return

    if name == "save":
        body = QPainterPath()
        body.moveTo(13, 10)
        body.lineTo(45, 10)
        body.lineTo(53, 18)
        body.lineTo(53, 54)
        body.lineTo(11, 54)
        body.lineTo(11, 10)
        body.closeSubpath()
        painter.drawPath(body)
        painter.drawRoundedRect(QRectF(20, 10, 23, 15), 2, 2)
        painter.drawRoundedRect(QRectF(19, 35, 26, 19), 2, 2)
        painter.drawLine(38, 13, 38, 21)
        return

    if name == "reset":
        painter.drawArc(QRectF(12, 12, 40, 40), 38 * 16, 286 * 16)
        painter.drawLine(46, 12, 53, 21)
        painter.drawLine(53, 21, 42, 23)
        return

    if name == "export":
        painter.drawLine(32, 45, 32, 11)
        painter.drawLine(20, 23, 32, 11)
        painter.drawLine(44, 23, 32, 11)
        tray = QPainterPath()
        tray.moveTo(12, 39)
        tray.lineTo(12, 53)
        tray.lineTo(52, 53)
        tray.lineTo(52, 39)
        painter.drawPath(tray)
        return

    if name == "print":
        painter.drawRoundedRect(QRectF(11, 23, 42, 25), 4, 4)
        painter.drawRect(QRectF(18, 9, 28, 18))
        painter.drawRect(QRectF(18, 39, 28, 16))
        painter.drawLine(24, 45, 40, 45)
        painter.drawLine(24, 50, 40, 50)
        painter.drawPoint(46, 31)
        return

    if name in {"zoom_in", "zoom_out"}:
        painter.drawEllipse(QRectF(10, 10, 34, 34))
        painter.drawLine(39, 39, 54, 54)
        painter.drawLine(18, 27, 36, 27)
        if name == "zoom_in":
            painter.drawLine(27, 18, 27, 36)
        return

    if name == "fit_page":
        painter.drawRoundedRect(QRectF(15, 7, 34, 50), 2, 2)
        painter.drawLine(21, 17, 29, 17)
        painter.drawLine(21, 17, 21, 25)
        painter.drawLine(43, 17, 35, 17)
        painter.drawLine(43, 17, 43, 25)
        painter.drawLine(21, 47, 29, 47)
        painter.drawLine(21, 47, 21, 39)
        painter.drawLine(43, 47, 35, 47)
        painter.drawLine(43, 47, 43, 39)
        return

    if name == "fit_width":
        painter.drawRoundedRect(QRectF(12, 8, 40, 48), 2, 2)
        painter.drawLine(18, 32, 46, 32)
        painter.drawLine(18, 32, 24, 26)
        painter.drawLine(18, 32, 24, 38)
        painter.drawLine(46, 32, 40, 26)
        painter.drawLine(46, 32, 40, 38)
        return

    if name == "info":
        painter.drawEllipse(QRectF(10, 10, 44, 44))
        painter.drawLine(32, 29, 32, 45)
        painter.drawPoint(32, 20)
        return

    if name == "edit":
        pencil = QPainterPath()
        pencil.moveTo(13, 50)
        pencil.lineTo(17, 36)
        pencil.lineTo(41, 12)
        pencil.lineTo(52, 23)
        pencil.lineTo(28, 47)
        pencil.closeSubpath()
        painter.drawPath(pencil)
        painter.drawLine(20, 34, 31, 45)
        painter.drawLine(13, 51, 28, 47)
        return

    if name == "delete":
        painter.drawRoundedRect(QRectF(17, 19, 30, 37), 3, 3)
        painter.drawLine(13, 18, 51, 18)
        painter.drawLine(25, 11, 39, 11)
        painter.drawLine(25, 28, 25, 47)
        painter.drawLine(32, 28, 32, 47)
        painter.drawLine(39, 28, 39, 47)
        return

    if name == "filter":
        funnel = QPainterPath()
        funnel.moveTo(10, 13)
        funnel.lineTo(54, 13)
        funnel.lineTo(38, 32)
        funnel.lineTo(38, 50)
        funnel.lineTo(27, 56)
        funnel.lineTo(27, 32)
        funnel.closeSubpath()
        painter.drawPath(funnel)
        return

    if name == "check":
        painter.drawLine(11, 33, 26, 47)
        painter.drawLine(26, 47, 53, 17)
        return

    if name == "cancel":
        painter.drawEllipse(QRectF(10, 10, 44, 44))
        painter.drawLine(20, 20, 44, 44)
        painter.drawLine(44, 20, 20, 44)
        return

    if name == "calendar":
        painter.drawRoundedRect(QRectF(10, 14, 44, 40), 4, 4)
        painter.drawLine(10, 26, 54, 26)
        painter.drawLine(21, 9, 21, 20)
        painter.drawLine(43, 9, 43, 20)
        for x in (20, 32, 44):
            for y in (35, 45):
                painter.drawPoint(x, y)
        return

    if name == "dashboard":
        painter.drawRoundedRect(QRectF(10, 10, 18, 18), 3, 3)
        painter.drawRoundedRect(QRectF(36, 10, 18, 27), 3, 3)
        painter.drawRoundedRect(QRectF(10, 36, 18, 18), 3, 3)
        painter.drawRoundedRect(QRectF(36, 45, 18, 9), 3, 3)
        return

    if name == "history":
        painter.drawArc(QRectF(10, 10, 44, 44), -40 * 16, 310 * 16)
        painter.drawLine(12, 15, 5, 27)
        painter.drawLine(5, 27, 19, 27)
        painter.drawLine(32, 19, 32, 34)
        painter.drawLine(32, 34, 43, 40)
        return

    if name == "server":
        for y in (11, 27, 43):
            painter.drawRoundedRect(QRectF(10, y, 44, 12), 3, 3)
            painter.drawPoint(18, y + 6)
            painter.drawLine(26, y + 6, 46, y + 6)
        return

    if name == "view":
        eye = QPainterPath()
        eye.moveTo(7, 32)
        eye.cubicTo(18, 17, 46, 17, 57, 32)
        eye.cubicTo(46, 47, 18, 47, 7, 32)
        eye.closeSubpath()
        painter.drawPath(eye)
        painter.drawEllipse(QRectF(25, 25, 14, 14))
        return

    if name == "lock":
        painter.drawRoundedRect(QRectF(15, 28, 34, 27), 5, 5)
        painter.drawArc(QRectF(21, 9, 22, 31), 0, 180 * 16)
        painter.drawLine(32, 38, 32, 47)
        painter.drawPoint(32, 37)
        return

    if name == "previous":
        painter.drawLine(42, 11, 20, 32)
        painter.drawLine(20, 32, 42, 53)
        return

    if name == "next":
        painter.drawLine(22, 11, 44, 32)
        painter.drawLine(44, 32, 22, 53)
        return

    if name == "minimize":
        painter.drawLine(14, 43, 50, 43)
        return

    if name == "maximize":
        painter.drawRoundedRect(QRectF(14, 14, 36, 36), 2, 2)
        return

    if name == "restore":
        painter.drawRoundedRect(QRectF(12, 21, 31, 31), 2, 2)
        painter.drawLine(21, 21, 21, 12)
        painter.drawLine(21, 12, 52, 12)
        painter.drawLine(52, 12, 52, 43)
        painter.drawLine(43, 43, 52, 43)
        return

    if name == "expand":
        painter.drawLine(13, 23, 32, 42)
        painter.drawLine(32, 42, 51, 23)
        return

    if name == "collapse":
        painter.drawLine(13, 41, 32, 22)
        painter.drawLine(32, 22, 51, 41)
        return

    raise ValueError(f"Unsupported icon shape: {name}")
