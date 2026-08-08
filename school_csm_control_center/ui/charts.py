from __future__ import annotations

"""Dependency-free, accessible QPainter charts for live CSM analysis."""

from collections import OrderedDict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
import math
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import QSizePolicy, QWidget

from school_csm_control_center.ui import theme


@dataclass(frozen=True)
class BarDatum:
    label: str
    value: float


@dataclass(frozen=True)
class DonutDatum:
    label: str
    value: float
    color: str


@dataclass(frozen=True)
class DonutLayout:
    mode: str
    legend_rect: QRectF
    donut_area: QRectF
    diameter: float
    legend_columns: int


@dataclass(frozen=True)
class TrendDatum:
    label: str
    value: float


class _ChartBase(QWidget):
    def __init__(
        self,
        title: str,
        *,
        empty_title: str,
        empty_message: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._title = str(title or "").strip()
        self._context_text = ""
        self._empty_title = str(empty_title)
        self._empty_message = str(empty_message)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(240)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)
        self.setAccessibleName(self._title or "Survey analysis chart")

    @property
    def title(self) -> str:
        return self._title

    def set_title(self, title: str) -> None:
        self._title = str(title or "").strip()
        self.setAccessibleName(self._title or "Survey analysis chart")
        self.update()

    @property
    def context_text(self) -> str:
        return self._context_text

    def set_context(self, text: str | None) -> None:
        """Show a short evidence cue in the chart header."""

        self._context_text = " ".join(str(text or "").split())
        self.update()

    def _new_painter(self) -> QPainter:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        return painter

    def _draw_surface(self, painter: QPainter) -> QRectF:
        surface = QRectF(self.rect()).adjusted(0.75, 0.75, -0.75, -0.75)
        painter.setPen(QPen(QColor(theme.CARD_BORDER), 1.2))
        painter.setBrush(QColor(theme.PANEL_BG))
        painter.drawRoundedRect(surface, 11, 11)

        header_left = surface.left() + 14
        header_width = surface.width() - 28
        context_width = min(245.0, header_width * 0.46) if self._context_text else 0.0
        title_width = header_width - context_width - (10 if context_width else 0)
        title_rect = QRectF(header_left, surface.top() + 7, title_width, 28)
        _set_font(painter, 17, bold=True)
        painter.setPen(QColor(theme.TEXT_PRIMARY))
        title = painter.fontMetrics().elidedText(
            self._title,
            Qt.TextElideMode.ElideRight,
            max(24, int(title_rect.width())),
        )
        painter.drawText(
            title_rect,
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            title,
        )
        if self._context_text:
            context_rect = QRectF(
                surface.right() - 14 - context_width,
                surface.top() + 8,
                context_width,
                25,
            )
            _set_font(painter, 13, bold=True)
            painter.setPen(QColor(theme.ACCENT_CYAN))
            context = painter.fontMetrics().elidedText(
                self._context_text,
                Qt.TextElideMode.ElideLeft,
                max(24, int(context_rect.width())),
            )
            painter.drawText(
                context_rect,
                int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                context,
            )
        return QRectF(
            surface.left() + 14,
            surface.top() + 43,
            surface.width() - 28,
            max(0.0, surface.height() - 56),
        )

    def _draw_empty(self, painter: QPainter, rect: QRectF) -> None:
        empty_rect = rect.adjusted(3, 3, -3, -3)
        dash = QPen(QColor(theme.CARD_BORDER), 1.2, Qt.PenStyle.DashLine)
        painter.setPen(dash)
        painter.setBrush(QColor(8, 31, 49, 115))
        painter.drawRoundedRect(empty_rect, 10, 10)

        center_x = empty_rect.center().x()
        icon_center = QPointF(center_x, empty_rect.center().y() - 28)
        painter.setPen(QPen(QColor(theme.ACCENT_TEAL), 1.8))
        painter.setBrush(QColor(43, 217, 197, 20))
        painter.drawEllipse(icon_center, 18, 18)
        _set_font(painter, 15, bold=True)
        painter.setPen(QColor(theme.ACCENT_TEAL))
        painter.drawText(
            QRectF(icon_center.x() - 18, icon_center.y() - 18, 36, 36),
            int(Qt.AlignmentFlag.AlignCenter),
            "i",
        )

        title_rect = QRectF(
            empty_rect.left() + 18,
            icon_center.y() + 24,
            empty_rect.width() - 36,
            22,
        )
        _set_font(painter, 15, bold=True)
        painter.setPen(QColor(theme.TEXT_PRIMARY))
        painter.drawText(title_rect, int(Qt.AlignmentFlag.AlignCenter), self._empty_title)

        message_rect = QRectF(
            empty_rect.left() + 24,
            title_rect.bottom() + 2,
            empty_rect.width() - 48,
            38,
        )
        _set_font(painter, 13)
        painter.setPen(QColor(theme.TEXT_MUTED))
        painter.drawText(
            message_rect,
            int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap),
            self._empty_message,
        )
        self.setAccessibleDescription(f"{self._empty_title}. {self._empty_message}")
        self.setToolTip(f"{self._empty_title}. {self._empty_message}")


class DimensionBarChart(_ChartBase):
    """Horizontal bars for the service-quality dimension scores."""

    def __init__(
        self,
        title: str = "Service Quality Dimensions",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(
            title,
            empty_title="No dimension scores yet",
            empty_message="Completed SQD responses will appear here automatically.",
            parent=parent,
        )
        self._data: tuple[BarDatum, ...] = ()
        self._maximum = 100.0
        self._suffix = "%"
        self.setMinimumHeight(250)

    @property
    def normalized_data(self) -> tuple[BarDatum, ...]:
        return self._data

    def set_data(
        self,
        data: Mapping[str, Any] | Iterable[Any] | None,
        *,
        maximum: float = 100.0,
        suffix: str = "%",
    ) -> None:
        normalized_maximum = _finite_number(maximum)
        self._maximum = normalized_maximum if normalized_maximum and normalized_maximum > 0 else 100.0
        self._suffix = str(suffix or "")
        self._data = tuple(_normalize_bar_data(data))
        self.setMinimumHeight(max(250, 76 + len(self._data) * 36))
        self._refresh_accessibility()
        self.updateGeometry()
        self.update()

    def clear(self) -> None:
        self.set_data((), maximum=self._maximum, suffix=self._suffix)

    def _refresh_accessibility(self) -> None:
        if not self._data:
            empty = "No dimension scores. Completed SQD responses will appear automatically."
            self.setAccessibleDescription(empty)
            self.setToolTip(empty)
            return
        summary = "; ".join(
            f"{item.label}: {_format_number(item.value)}{self._suffix}"
            for item in self._data
        )
        self.setAccessibleDescription(summary)
        self.setToolTip(summary)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = self._new_painter()
        content = self._draw_surface(painter)
        if not self._data:
            self._draw_empty(painter, content)
            painter.end()
            return

        row_height = content.height() / max(1, len(self._data))
        row_height = min(46.0, max(32.0, row_height))
        total_height = row_height * len(self._data)
        y = content.top() + max(0.0, (content.height() - total_height) / 2.0)
        label_width = min(200.0, max(104.0, content.width() * 0.34))
        value_width = 64.0
        track_left = content.left() + label_width + 10
        track_width = max(36.0, content.right() - track_left - value_width - 7)

        for index, item in enumerate(self._data):
            row_rect = QRectF(content.left(), y, content.width(), row_height - 4)
            if index % 2:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(13, 41, 64, 75))
                painter.drawRoundedRect(row_rect, 5, 5)

            label_rect = QRectF(row_rect.left() + 2, row_rect.top(), label_width - 5, row_rect.height())
            _set_font(painter, 11, bold=True)
            painter.setPen(QColor(theme.TEXT_SECONDARY))
            label_text = painter.fontMetrics().elidedText(
                item.label,
                Qt.TextElideMode.ElideRight,
                max(20, int(label_rect.width())),
            )
            painter.drawText(
                label_rect,
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                label_text,
            )

            track_rect = QRectF(
                track_left,
                row_rect.center().y() - 7,
                track_width,
                14,
            )
            painter.setPen(QPen(QColor(27, 85, 114, 150), 1))
            painter.setBrush(QColor(5, 20, 35, 210))
            painter.drawRoundedRect(track_rect, 7, 7)

            ratio = min(1.0, max(0.0, item.value / self._maximum))
            fill_width = track_rect.width() * ratio
            if fill_width > 0:
                fill_rect = QRectF(track_rect.left(), track_rect.top(), fill_width, track_rect.height())
                gradient = QLinearGradient(fill_rect.topLeft(), fill_rect.topRight())
                gradient.setColorAt(0.0, QColor(theme.ACCENT_TEAL_DARK))
                gradient.setColorAt(1.0, QColor(theme.ACCENT_CYAN))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(gradient)
                painter.drawRoundedRect(fill_rect, 7, 7)

            value_rect = QRectF(track_rect.right() + 7, row_rect.top(), value_width, row_rect.height())
            _set_font(painter, 11, bold=True)
            painter.setPen(QColor(theme.TEXT_PRIMARY))
            painter.drawText(
                value_rect,
                int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                f"{_format_number(item.value)}{self._suffix}",
            )
            y += row_height

        painter.end()


class ResponseDonutChart(_ChartBase):
    """Donut chart with an integrated, readable response legend."""

    def __init__(
        self,
        title: str = "Response Distribution",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(
            title,
            empty_title="No response distribution yet",
            empty_message="Saved survey answers will populate this distribution.",
            parent=parent,
        )
        self._data: tuple[DonutDatum, ...] = ()
        self._center_label: str | None = None
        self.setMinimumHeight(270)

    @property
    def normalized_data(self) -> tuple[DonutDatum, ...]:
        return self._data

    @property
    def total(self) -> float:
        return sum(item.value for item in self._data)

    def set_data(
        self,
        data: Mapping[str, Any] | Iterable[Any] | None,
        *,
        colors: Mapping[str, str | QColor] | Sequence[str | QColor] | None = None,
        center_label: str | None = None,
    ) -> None:
        self._data = tuple(_normalize_donut_data(data, colors))
        self._center_label = str(center_label).strip() if center_label is not None else None
        self.setMinimumHeight(max(270, 165 + len(self._data) * 24))
        self._refresh_accessibility()
        self.updateGeometry()
        self.update()

    def clear(self) -> None:
        self.set_data(())

    def _refresh_accessibility(self) -> None:
        total = self.total
        if total <= 0:
            empty = "No response distribution. Saved survey answers will populate this chart."
            self.setAccessibleDescription(empty)
            self.setToolTip(empty)
            return
        summary = "; ".join(
            f"{item.label}: {_format_number(item.value)}, {item.value / total * 100:.1f}%"
            for item in self._data
        )
        self.setAccessibleDescription(summary)
        self.setToolTip(summary)

    @staticmethod
    def _layout_for_content(content: QRectF) -> DonutLayout:
        """Place compact details left of a large donut whenever width allows."""

        if content.width() >= 320 and content.height() >= 150:
            gap = 10.0
            desired_legend_width = min(220.0, max(150.0, content.width() * 0.42))
            legend_width = min(
                desired_legend_width,
                max(120.0, content.width() - gap - 148.0),
            )
            legend_rect = QRectF(
                content.left(),
                content.top(),
                legend_width,
                content.height(),
            )
            donut_left = legend_rect.right() + gap
            donut_area = QRectF(
                donut_left,
                content.top(),
                max(0.0, content.right() - donut_left),
                content.height(),
            )
            diameter = min(
                280.0,
                max(112.0, min(donut_area.width(), donut_area.height()) - 8.0),
            )
            return DonutLayout("horizontal", legend_rect, donut_area, diameter, 1)

        donut_height = min(176.0, content.height() * 0.57)
        donut_area = QRectF(content.left(), content.top(), content.width(), donut_height)
        legend_rect = QRectF(
            content.left(),
            donut_area.bottom() + 8,
            content.width(),
            max(0.0, content.bottom() - donut_area.bottom() - 8),
        )
        diameter = min(
            210.0,
            max(100.0, min(donut_area.width(), donut_area.height()) - 8.0),
        )
        return DonutLayout("stacked", legend_rect, donut_area, diameter, 2)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = self._new_painter()
        content = self._draw_surface(painter)
        total = self.total
        if not self._data or total <= 0:
            self._draw_empty(painter, content)
            painter.end()
            return

        layout = self._layout_for_content(content)
        donut_area = layout.donut_area
        legend_rect = layout.legend_rect
        diameter = layout.diameter
        arc_rect = QRectF(
            donut_area.center().x() - diameter / 2,
            donut_area.center().y() - diameter / 2,
            diameter,
            diameter,
        )
        thickness = min(44.0, max(18.0, diameter * 0.17))
        inset = thickness / 2 + 2
        arc_rect = arc_rect.adjusted(inset, inset, -inset, -inset)

        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(
            QPen(
                QColor(27, 85, 114, 105),
                thickness,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.FlatCap,
            )
        )
        painter.drawEllipse(arc_rect)

        start_angle = 90 * 16
        for item in self._data:
            if item.value <= 0:
                continue
            span = max(1, round(item.value / total * 360 * 16))
            painter.setPen(
                QPen(
                    QColor(item.color),
                    thickness,
                    Qt.PenStyle.SolidLine,
                    Qt.PenCapStyle.FlatCap,
                )
            )
            painter.drawArc(arc_rect, start_angle, -span)
            start_angle -= span

        center = arc_rect.center()
        center_width = max(58.0, arc_rect.width() - thickness * 0.85)
        total_text = _format_number(total)
        total_font_size = 26 if diameter >= 170 else 24
        if len(total_text) > 5:
            total_font_size = min(total_font_size, 22)
        if len(total_text) > 7:
            total_font_size = min(total_font_size, 18)
        _set_font(painter, total_font_size, bold=True)
        painter.setPen(QColor(theme.TEXT_PRIMARY))
        painter.drawText(
            QRectF(center.x() - center_width / 2, center.y() - 27, center_width, 32),
            int(Qt.AlignmentFlag.AlignCenter),
            total_text,
        )
        _set_font(painter, 12, bold=True)
        painter.setPen(QColor(theme.TEXT_MUTED))
        center_label = self._center_label or "RESPONSES"
        center_label = painter.fontMetrics().elidedText(
            center_label.upper(),
            Qt.TextElideMode.ElideRight,
            max(30, int(center_width)),
        )
        painter.drawText(
            QRectF(center.x() - center_width / 2, center.y() + 5, center_width, 18),
            int(Qt.AlignmentFlag.AlignCenter),
            center_label,
        )

        self._draw_legend(painter, legend_rect, total, columns=layout.legend_columns)
        painter.end()

    def _draw_legend(
        self,
        painter: QPainter,
        rect: QRectF,
        total: float,
        *,
        columns: int,
    ) -> None:
        columns = max(1, columns)
        rows = max(1, math.ceil(len(self._data) / columns))
        row_height = min(30.0, max(26.0, rect.height() / rows))
        column_width = rect.width() / columns
        total_height = rows * row_height
        top = rect.top() + max(0.0, (rect.height() - total_height) / 2)

        for index, item in enumerate(self._data):
            column = index // rows
            row = index % rows
            item_rect = QRectF(
                rect.left() + column * column_width,
                top + row * row_height,
                column_width,
                row_height,
            )
            dot_center = QPointF(item_rect.left() + 6, item_rect.center().y())
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(item.color))
            painter.drawEllipse(dot_center, 5, 5)

            percentage = item.value / total * 100 if total else 0.0
            value_text = f"{_format_number(item.value)} · {percentage:.1f}%"
            _set_font(painter, 12, bold=True)
            max_value_width = max(44, int(item_rect.width() * 0.58))
            value_text = painter.fontMetrics().elidedText(
                value_text,
                Qt.TextElideMode.ElideLeft,
                max(30, max_value_width - 4),
            )
            value_width = min(
                max_value_width,
                painter.fontMetrics().horizontalAdvance(value_text) + 7,
            )
            painter.setPen(QColor(theme.TEXT_PRIMARY))
            painter.drawText(
                QRectF(item_rect.right() - value_width, item_rect.top(), value_width, item_rect.height()),
                int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                value_text,
            )

            label_width = max(10, int(item_rect.width() - value_width - 20))
            label_text = painter.fontMetrics().elidedText(
                item.label,
                Qt.TextElideMode.ElideRight,
                label_width,
            )
            _set_font(painter, 12)
            painter.setPen(QColor(theme.TEXT_SECONDARY))
            painter.drawText(
                QRectF(item_rect.left() + 15, item_rect.top(), label_width, item_rect.height()),
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                label_text,
            )


class TrendChart(_ChartBase):
    """Responsive line/area chart for satisfaction over time."""

    _BASELINE_NOTE = "Only one reporting period — add another period to show a trend line."

    def __init__(
        self,
        title: str = "Satisfaction Trend",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(
            title,
            empty_title="No trend available yet",
            empty_message="At least one dated survey result is needed to begin the trend.",
            parent=parent,
        )
        self._data: tuple[TrendDatum, ...] = ()
        self._y_min = 0.0
        self._y_max = 100.0
        self._suffix = "%"
        self.setMinimumHeight(260)

    @property
    def normalized_data(self) -> tuple[TrendDatum, ...]:
        return self._data

    @property
    def display_mode(self) -> str:
        if not self._data:
            return "empty"
        return "baseline" if len(self._data) == 1 else "trend"

    def set_data(
        self,
        data: Mapping[str, Any] | Iterable[Any] | None,
        *,
        y_min: float = 0.0,
        y_max: float = 100.0,
        suffix: str = "%",
    ) -> None:
        minimum = _finite_number(y_min)
        maximum = _finite_number(y_max)
        self._y_min = minimum if minimum is not None else 0.0
        self._y_max = maximum if maximum is not None else 100.0
        if self._y_max <= self._y_min:
            self._y_max = self._y_min + 1.0
        self._suffix = str(suffix or "")
        self._data = tuple(_normalize_trend_data(data))
        self._refresh_accessibility()
        self.update()

    def clear(self) -> None:
        self.set_data(
            (),
            y_min=self._y_min,
            y_max=self._y_max,
            suffix=self._suffix,
        )

    def _refresh_accessibility(self) -> None:
        if not self._data:
            empty = "No trend. At least one dated survey result is needed."
            self.setAccessibleDescription(empty)
            self.setToolTip(empty)
            return
        summary = "; ".join(
            f"{item.label}: {_format_number(item.value)}{self._suffix}"
            for item in self._data
        )
        if self.display_mode == "baseline":
            summary += f". {self._BASELINE_NOTE}"
        self.setAccessibleDescription(summary)
        self.setToolTip(summary)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = self._new_painter()
        content = self._draw_surface(painter)
        if not self._data:
            self._draw_empty(painter, content)
            painter.end()
            return

        plot = content.adjusted(48, 8, -13, -34)
        if plot.width() <= 20 or plot.height() <= 20:
            painter.end()
            return

        self._draw_grid(painter, plot)
        points = self._points(plot)
        if not points:
            painter.end()
            return

        if self.display_mode == "trend":
            area = QPainterPath(points[0])
            for point in points[1:]:
                area.lineTo(point)
            area.lineTo(points[-1].x(), plot.bottom())
            area.lineTo(points[0].x(), plot.bottom())
            area.closeSubpath()
            gradient = QLinearGradient(plot.topLeft(), plot.bottomLeft())
            gradient.setColorAt(0.0, QColor(43, 217, 197, 90))
            gradient.setColorAt(1.0, QColor(43, 217, 197, 4))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(gradient)
            painter.drawPath(area)

            line = QPainterPath(points[0])
            for point in points[1:]:
                line.lineTo(point)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(
                QPen(
                    QColor(theme.ACCENT_TEAL),
                    3.0,
                    Qt.PenStyle.SolidLine,
                    Qt.PenCapStyle.RoundCap,
                    Qt.PenJoinStyle.RoundJoin,
                )
            )
            painter.drawPath(line)

            for point in points:
                painter.setPen(QPen(QColor(theme.ACCENT_CYAN), 2.2))
                painter.setBrush(QColor(theme.PANEL_BG))
                painter.drawEllipse(point, 5.0, 5.0)
        else:
            self._draw_baseline(painter, plot, points[0])

        self._draw_x_labels(painter, plot, points)
        self._draw_summary(painter, content)
        painter.end()

    def _draw_baseline(self, painter: QPainter, plot: QRectF, point: QPointF) -> None:
        """Draw reading guides for one period without implying a trend."""

        horizontal_end = max(plot.left(), point.x() - 9)
        painter.setPen(
            QPen(
                QColor(98, 219, 255, 165),
                1.4,
                Qt.PenStyle.DashLine,
                Qt.PenCapStyle.RoundCap,
            )
        )
        painter.drawLine(QPointF(plot.left(), point.y()), QPointF(horizontal_end, point.y()))

        painter.setPen(QPen(QColor(27, 85, 114, 150), 1.0, Qt.PenStyle.DotLine))
        painter.drawLine(QPointF(point.x(), point.y() + 9), QPointF(point.x(), plot.bottom()))

        painter.setPen(QPen(QColor(theme.PANEL_BG), 2.5))
        painter.setBrush(QColor(theme.ACCENT_CYAN))
        painter.drawEllipse(point, 6.0, 6.0)

        value_text = f"{_format_number(self._data[0].value)}{self._suffix}"
        _set_font(painter, 13, bold=True)
        painter.setPen(QColor(theme.TEXT_PRIMARY))
        painter.drawText(
            QRectF(point.x() + 11, point.y() - 24, 92, 22),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            value_text,
        )

        _set_font(painter, 12)
        painter.setPen(QColor(theme.TEXT_SECONDARY))
        note = painter.fontMetrics().elidedText(
            self._BASELINE_NOTE,
            Qt.TextElideMode.ElideRight,
            max(80, int(plot.width() - 18)),
        )
        note_y = plot.bottom() - 30 if point.y() < plot.center().y() else plot.top() + 7
        painter.drawText(
            QRectF(plot.left() + 9, note_y, plot.width() - 18, 22),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            note,
        )

    def _draw_grid(self, painter: QPainter, plot: QRectF) -> None:
        steps = 4
        _set_font(painter, 12)
        for step in range(steps + 1):
            ratio = step / steps
            y = plot.bottom() - plot.height() * ratio
            value = self._y_min + (self._y_max - self._y_min) * ratio
            painter.setPen(QPen(QColor(27, 85, 114, 95), 1, Qt.PenStyle.DashLine))
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            painter.setPen(QColor(theme.TEXT_MUTED))
            painter.drawText(
                QRectF(plot.left() - 47, y - 10, 41, 20),
                int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                f"{_format_number(value)}{self._suffix}",
            )

    def _points(self, plot: QRectF) -> list[QPointF]:
        count = len(self._data)
        points: list[QPointF] = []
        for index, item in enumerate(self._data):
            x_ratio = index / (count - 1) if count > 1 else 0.5
            y_ratio = (item.value - self._y_min) / (self._y_max - self._y_min)
            y_ratio = min(1.0, max(0.0, y_ratio))
            points.append(
                QPointF(
                    plot.left() + plot.width() * x_ratio,
                    plot.bottom() - plot.height() * y_ratio,
                )
            )
        return points

    def _draw_x_labels(
        self,
        painter: QPainter,
        plot: QRectF,
        points: list[QPointF],
    ) -> None:
        count = len(self._data)
        if count <= 5:
            selected = list(range(count))
        else:
            selected = sorted({0, count // 4, count // 2, (count * 3) // 4, count - 1})
        _set_font(painter, 12)
        painter.setPen(QColor(theme.TEXT_MUTED))
        for index in selected:
            width = min(90.0, max(42.0, plot.width() / max(1, len(selected))))
            label = painter.fontMetrics().elidedText(
                self._data[index].label,
                Qt.TextElideMode.ElideRight,
                max(20, int(width)),
            )
            painter.drawText(
                QRectF(points[index].x() - width / 2, plot.bottom() + 7, width, 22),
                int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop),
                label,
            )

    def _draw_summary(self, painter: QPainter, content: QRectF) -> None:
        first = self._data[0].value
        latest = self._data[-1].value
        change = latest - first
        if len(self._data) == 1:
            summary = f"Baseline {_format_number(latest)}{self._suffix}"
            color = QColor(theme.ACCENT_CYAN)
        else:
            sign = "+" if change > 0 else ""
            summary = f"{sign}{_format_number(change)}{self._suffix} from first result"
            color = QColor(theme.SUCCESS if change >= 0 else theme.DANGER)
        _set_font(painter, 13, bold=True)
        painter.setPen(color)
        width = min(190.0, content.width() * 0.55)
        painter.drawText(
            QRectF(content.right() - width, content.top() - 34, width, 22),
            int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
            summary,
        )


def _normalize_bar_data(
    data: Mapping[str, Any] | Iterable[Any] | None,
) -> list[BarDatum]:
    # Dimension labels are unique: later values update earlier ones without
    # disturbing the original display order.
    values: OrderedDict[str, float] = OrderedDict()
    for label, value, _color in _iter_labeled_values(data):
        number = _finite_number(value)
        clean_label = str(label or "").strip()
        if not clean_label or number is None:
            continue
        values[clean_label] = max(0.0, number)
    return [BarDatum(label, value) for label, value in values.items()]


def _normalize_donut_data(
    data: Mapping[str, Any] | Iterable[Any] | None,
    colors: Mapping[str, str | QColor] | Sequence[str | QColor] | None,
) -> list[DonutDatum]:
    values: OrderedDict[str, float] = OrderedDict()
    embedded_colors: dict[str, Any] = {}
    for label, value, embedded_color in _iter_labeled_values(data):
        number = _finite_number(value)
        clean_label = str(label or "").strip()
        if not clean_label or number is None:
            continue
        values[clean_label] = values.get(clean_label, 0.0) + max(0.0, number)
        if embedded_color is not None:
            embedded_colors[clean_label] = embedded_color

    color_mapping = colors if isinstance(colors, Mapping) else {}
    color_sequence = (
        list(colors)
        if isinstance(colors, Sequence) and not isinstance(colors, (str, bytes))
        else []
    )
    normalized: list[DonutDatum] = []
    for index, (label, value) in enumerate(values.items()):
        chosen: Any = embedded_colors.get(label)
        if label in color_mapping:
            chosen = color_mapping[label]
        elif chosen is None and index < len(color_sequence):
            chosen = color_sequence[index]
        if chosen is None:
            chosen = theme.CHART_COLORS[index % len(theme.CHART_COLORS)]
        color = QColor(chosen)
        if not color.isValid():
            color = QColor(theme.CHART_COLORS[index % len(theme.CHART_COLORS)])
        normalized.append(DonutDatum(label, value, color.name()))
    return normalized


def _normalize_trend_data(
    data: Mapping[str, Any] | Iterable[Any] | None,
) -> list[TrendDatum]:
    normalized: list[TrendDatum] = []
    for label, value, _color in _iter_labeled_values(data):
        number = _finite_number(value)
        clean_label = str(label or "").strip()
        if not clean_label or number is None:
            continue
        normalized.append(TrendDatum(clean_label, number))
    return normalized


def _iter_labeled_values(
    data: Mapping[str, Any] | Iterable[Any] | None,
) -> Iterable[tuple[Any, Any, Any]]:
    if data is None:
        return ()
    if isinstance(data, Mapping):
        if _looks_like_datum(data):
            raw: Iterable[Any] = (data,)
        else:
            raw = data.items()
    elif isinstance(data, (str, bytes)):
        return ()
    else:
        raw = data

    results: list[tuple[Any, Any, Any]] = []
    for item in raw:
        if isinstance(item, Mapping):
            label = _first_present(item, ("label", "name", "dimension", "date", "period"))
            value = _first_present(item, ("value", "count", "score", "percentage", "rate"))
            color = item.get("color")
            results.append((label, value, color))
            continue
        if isinstance(item, (tuple, list)) and len(item) >= 2:
            color = item[2] if len(item) >= 3 else None
            results.append((item[0], item[1], color))
    return results


def _looks_like_datum(value: Mapping[str, Any]) -> bool:
    label_keys = {"label", "name", "dimension", "date", "period"}
    value_keys = {"value", "count", "score", "percentage", "rate"}
    return bool(label_keys.intersection(value) and value_keys.intersection(value))


def _first_present(value: Mapping[str, Any], keys: Iterable[str]) -> Any:
    for key in keys:
        if key in value:
            return value[key]
    return None


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, str):
        cleaned = value.strip().replace(",", "")
        if cleaned.endswith("%"):
            cleaned = cleaned[:-1].strip()
        if not cleaned:
            return None
        value = cleaned
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _format_number(value: float) -> str:
    if math.isclose(value, round(value), abs_tol=1e-9):
        return f"{int(round(value)):,}"
    return f"{value:,.1f}".rstrip("0").rstrip(".")


def _set_font(painter: QPainter, pixel_size: int, *, bold: bool = False) -> None:
    font = QFont(painter.font())
    font.setFamily(theme.FONT_FAMILY)
    font.setPixelSize(max(7, int(pixel_size)))
    font.setBold(bool(bold))
    painter.setFont(font)


__all__ = [
    "BarDatum",
    "DimensionBarChart",
    "DonutDatum",
    "ResponseDonutChart",
    "TrendChart",
    "TrendDatum",
]
