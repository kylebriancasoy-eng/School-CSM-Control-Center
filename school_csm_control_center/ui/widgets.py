from __future__ import annotations

"""Reusable controls and content cards for the CSM dashboard."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QPoint, QSize, Qt
from PySide6.QtGui import QColor, QFocusEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from school_csm_control_center.ui import theme
from school_csm_control_center.ui.icons import action_icon


class TooltipIconButton(QPushButton):
    """An icon-only action button with an immediate tooltip flag.

    A non-empty tooltip is mandatory because it is also used as the control's
    accessible name, status tip, and accessible description.
    """

    def __init__(
        self,
        icon_name: str,
        tooltip: str,
        *,
        icon_color: str | QColor = theme.ACCENT_CYAN,
        button_size: int = 40,
        icon_size: int = 22,
        role: str = "default",
        parent: QWidget | None = None,
    ) -> None:
        clean_tooltip = str(tooltip or "").strip()
        if not clean_tooltip:
            raise ValueError("TooltipIconButton requires a non-empty tooltip.")
        super().__init__(parent)
        self._icon_name = ""
        self._icon_color = QColor(icon_color)
        self._button_size = max(32, int(button_size))
        self._logical_icon_size = max(16, min(int(icon_size), self._button_size - 8))

        self.setObjectName("csm_icon_button")
        self.setProperty("role", "default")
        self.setText("")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFixedSize(self._button_size, self._button_size)
        self.setIconSize(QSize(self._logical_icon_size, self._logical_icon_size))
        self.setToolTipDuration(6000)
        self.set_role(role)
        self.set_action(icon_name, clean_tooltip, icon_color=icon_color)

    @property
    def icon_name(self) -> str:
        return self._icon_name

    def set_action(
        self,
        icon_name: str,
        tooltip: str | None = None,
        *,
        icon_color: str | QColor | None = None,
    ) -> None:
        """Replace the icon and optionally its user-facing description."""

        clean_name = str(icon_name or "").strip().lower()
        if icon_color is not None:
            value = QColor(icon_color)
            if not value.isValid():
                raise ValueError(f"Invalid icon color: {icon_color!r}")
            self._icon_color = value
        if tooltip is not None:
            clean_tooltip = str(tooltip or "").strip()
            if not clean_tooltip:
                raise ValueError("TooltipIconButton requires a non-empty tooltip.")
            self._set_accessible_tooltip(clean_tooltip)
        elif not self.toolTip().strip():
            raise ValueError("TooltipIconButton requires a non-empty tooltip.")

        rendered = action_icon(clean_name, self._icon_color, 64)
        if rendered.isNull():
            raise ValueError(f"The {clean_name!r} action did not produce an icon.")
        self._icon_name = clean_name
        self.setIcon(rendered)

    def set_role(self, role: str) -> None:
        """Update the visual role and immediately repolish the button."""

        clean_role = str(role or "default").strip().lower() or "default"
        if self.property("role") == clean_role:
            return
        self.setProperty("role", clean_role)
        current_style = self.style()
        current_style.unpolish(self)
        current_style.polish(self)
        self.update()

    def setToolTip(self, tooltip: str) -> None:  # noqa: N802 - Qt API
        """Keep tooltip and accessibility metadata synchronized."""

        clean_tooltip = str(tooltip or "").strip()
        if not clean_tooltip:
            raise ValueError("TooltipIconButton requires a non-empty tooltip.")
        super().setToolTip(clean_tooltip)
        self.setStatusTip(clean_tooltip)
        self.setAccessibleName(clean_tooltip)
        self.setAccessibleDescription(
            f"{clean_tooltip}. Icon-only action button."
        )

    def set_tooltip(self, tooltip: str) -> None:
        """PEP-8 alias for :meth:`setToolTip`."""

        self.setToolTip(tooltip)

    def _set_accessible_tooltip(self, tooltip: str) -> None:
        self.setToolTip(tooltip)

    def _show_tooltip_flag(self) -> None:
        tooltip = self.toolTip().strip()
        if not tooltip or not self.isVisible():
            return
        anchor = self.mapToGlobal(QPoint(self.width() // 2, self.height() + 7))
        QToolTip.showText(anchor, tooltip, self, self.rect(), 6000)

    def enterEvent(self, event) -> None:  # type: ignore[override]
        super().enterEvent(event)
        self._show_tooltip_flag()

    def leaveEvent(self, event) -> None:  # type: ignore[override]
        QToolTip.hideText()
        super().leaveEvent(event)

    def focusInEvent(self, event: QFocusEvent) -> None:  # noqa: N802
        super().focusInEvent(event)
        self._show_tooltip_flag()

    def focusOutEvent(self, event: QFocusEvent) -> None:  # noqa: N802
        QToolTip.hideText()
        super().focusOutEvent(event)


class MetricCard(QFrame):
    """Compact live metric with a title, primary value, and optional detail."""

    def __init__(
        self,
        title: str,
        value: str | int | float = "—",
        detail: str = "",
        *,
        accent: str | QColor = theme.ACCENT_TEAL,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._accent = QColor(accent)
        if not self._accent.isValid():
            raise ValueError(f"Invalid metric-card accent: {accent!r}")
        self.setObjectName("csm_metric_card")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumSize(150, 96)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(13, 10, 13, 10)
        layout.setSpacing(3)

        self.title_label = QLabel(str(title))
        self.title_label.setObjectName("csm_metric_title")
        self.title_label.setWordWrap(True)
        layout.addWidget(self.title_label)

        self.value_label = QLabel()
        self.value_label.setObjectName("csm_metric_value")
        self.value_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.value_label)

        self.detail_label = QLabel()
        self.detail_label.setObjectName("csm_metric_detail")
        self.detail_label.setWordWrap(True)
        layout.addWidget(self.detail_label)
        layout.addStretch(1)

        self.setStyleSheet(self._stylesheet())
        self.set_metric(value, detail)

    def set_value(self, value: str | int | float) -> None:
        text = str(value if value not in (None, "") else "—")
        self.value_label.setText(text)
        self.value_label.setToolTip(text)
        self._refresh_accessibility()

    def set_detail(self, detail: str) -> None:
        text = str(detail or "").strip()
        self.detail_label.setText(text)
        self.detail_label.setVisible(bool(text))
        self._refresh_accessibility()

    def set_metric(
        self,
        value: str | int | float,
        detail: str | None = None,
    ) -> None:
        self.set_value(value)
        if detail is not None:
            self.set_detail(detail)

    def _refresh_accessibility(self) -> None:
        title = self.title_label.text().strip()
        value = self.value_label.text().strip()
        detail = self.detail_label.text().strip()
        description = f"{title}: {value}"
        if detail:
            description += f". {detail}"
        self.setAccessibleName(title or "Dashboard metric")
        self.setAccessibleDescription(description)
        self.setToolTip(description)

    def _stylesheet(self) -> str:
        accent = self._accent.name()
        return f"""
        QFrame#csm_metric_card {{
            background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
                stop:0 rgba(13, 41, 64, 0.98), stop:1 rgba(8, 27, 45, 0.98));
            border: 1px solid {theme.CARD_BORDER};
            border-left: 3px solid {accent};
            border-radius: 11px;
        }}
        QLabel#csm_metric_title {{
            color: {theme.TEXT_MUTED};
            font-size: 11px;
            font-weight: 700;
        }}
        QLabel#csm_metric_value {{
            color: {theme.TEXT_PRIMARY};
            font-size: 24px;
            font-weight: 800;
        }}
        QLabel#csm_metric_detail {{
            color: {accent};
            font-size: 11px;
            font-weight: 700;
        }}
        """


class SectionCard(QFrame):
    """Titled dashboard surface with a public content layout."""

    def __init__(
        self,
        title: str,
        subtitle: str = "",
        *,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("csm_section_card")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setStyleSheet(
            f"""
            QFrame#csm_section_card {{
                background: rgba(10, 29, 48, 0.94);
                border: 1px solid {theme.CARD_BORDER};
                border-radius: 12px;
            }}
            QLabel#csm_section_title {{
                color: {theme.TEXT_PRIMARY};
                font-size: 15px;
                font-weight: 800;
            }}
            QLabel#csm_section_subtitle {{
                color: {theme.TEXT_MUTED};
                font-size: 11px;
            }}
            QWidget#csm_section_content {{
                background: transparent;
            }}
            """
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(13, 10, 13, 12)
        root.setSpacing(8)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(10)
        title_column = QVBoxLayout()
        title_column.setContentsMargins(0, 0, 0, 0)
        title_column.setSpacing(2)

        self.title_label = QLabel(str(title))
        self.title_label.setObjectName("csm_section_title")
        self.title_label.setWordWrap(True)
        title_column.addWidget(self.title_label)

        self.subtitle_label = QLabel(str(subtitle or ""))
        self.subtitle_label.setObjectName("csm_section_subtitle")
        self.subtitle_label.setWordWrap(True)
        self.subtitle_label.setVisible(bool(str(subtitle or "").strip()))
        title_column.addWidget(self.subtitle_label)
        header.addLayout(title_column, 1)

        self.header_actions_layout = QHBoxLayout()
        self.header_actions_layout.setContentsMargins(0, 0, 0, 0)
        self.header_actions_layout.setSpacing(7)
        header.addLayout(self.header_actions_layout)
        root.addLayout(header)

        self.content_widget = QWidget()
        self.content_widget.setObjectName("csm_section_content")
        self.content_layout = QVBoxLayout(self.content_widget)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(8)
        root.addWidget(self.content_widget, 1)

        self.setAccessibleName(str(title) or "Dashboard section")
        self.setAccessibleDescription(str(subtitle or title))

    def set_subtitle(self, subtitle: str) -> None:
        text = str(subtitle or "").strip()
        self.subtitle_label.setText(text)
        self.subtitle_label.setVisible(bool(text))
        self.setAccessibleDescription(text or self.title_label.text())

    def add_widget(self, widget: QWidget, stretch: int = 0) -> None:
        self.content_layout.addWidget(widget, stretch)

    def add_header_action(self, button: TooltipIconButton) -> None:
        self.header_actions_layout.addWidget(button)


class EmptyState(QFrame):
    """Inline, non-modal explanation displayed when a surface has no data."""

    def __init__(
        self,
        title: str = "No survey results yet",
        message: str = "Add a completed survey to begin the live analysis.",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("csm_empty_state")
        self.setMinimumHeight(142)
        self.setStyleSheet(
            f"""
            QFrame#csm_empty_state {{
                background: rgba(9, 28, 46, 0.58);
                border: 1px dashed rgba(98, 219, 255, 0.42);
                border-radius: 11px;
            }}
            QLabel#csm_empty_icon {{
                background: rgba(43, 217, 197, 0.08);
                border: 1px solid rgba(43, 217, 197, 0.24);
                border-radius: 20px;
            }}
            QLabel#csm_empty_title {{
                color: {theme.TEXT_PRIMARY};
                font-size: 14px;
                font-weight: 800;
            }}
            QLabel#csm_empty_message {{
                color: {theme.TEXT_MUTED};
                font-size: 11px;
            }}
            """
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(7)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        icon_label = QLabel()
        icon_label.setObjectName("csm_empty_icon")
        icon_label.setFixedSize(40, 40)
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_label.setPixmap(action_icon("info", theme.ACCENT_TEAL, 22).pixmap(22, 22))
        layout.addWidget(icon_label, 0, Qt.AlignmentFlag.AlignCenter)

        self.title_label = QLabel(str(title))
        self.title_label.setObjectName("csm_empty_title")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title_label.setWordWrap(True)
        layout.addWidget(self.title_label)

        self.message_label = QLabel(str(message))
        self.message_label.setObjectName("csm_empty_message")
        self.message_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.message_label.setWordWrap(True)
        layout.addWidget(self.message_label)

        self.setAccessibleName(str(title))
        self.setAccessibleDescription(str(message))

    def set_content(self, title: str, message: str) -> None:
        self.title_label.setText(str(title))
        self.message_label.setText(str(message))
        self.setAccessibleName(str(title))
        self.setAccessibleDescription(str(message))


@dataclass(frozen=True)
class LegendItem:
    label: str
    value: str
    color: str


class DistributionLegend(QFrame):
    """Accessible legend for donut and distribution visualizations."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("csm_distribution_legend")
        self.setStyleSheet(
            f"""
            QFrame#csm_distribution_legend {{
                background: transparent;
                border: none;
            }}
            QLabel#csm_legend_label {{
                color: {theme.TEXT_SECONDARY};
                font-size: 10px;
            }}
            QLabel#csm_legend_value {{
                color: {theme.TEXT_PRIMARY};
                font-size: 10px;
                font-weight: 800;
            }}
            """
        )
        self._items: tuple[LegendItem, ...] = ()
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(7)
        self._layout.setAlignment(Qt.AlignmentFlag.AlignTop)

    @property
    def items(self) -> tuple[LegendItem, ...]:
        return self._items

    def set_items(self, items: Mapping[str, Any] | Iterable[Any]) -> None:
        normalized = _normalize_legend_items(items)
        self._items = tuple(normalized)
        _clear_layout(self._layout)

        for item in self._items:
            row = QWidget()
            row.setStyleSheet("background: transparent;")
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(7)

            dot = QLabel("●")
            dot.setFixedWidth(13)
            dot.setAlignment(Qt.AlignmentFlag.AlignCenter)
            dot.setStyleSheet(
                f"background: transparent; color: {item.color}; font-size: 12px;"
            )
            row_layout.addWidget(dot)

            label = QLabel(item.label)
            label.setObjectName("csm_legend_label")
            label.setWordWrap(True)
            row_layout.addWidget(label, 1)

            value = QLabel(item.value)
            value.setObjectName("csm_legend_value")
            value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            row_layout.addWidget(value)
            self._layout.addWidget(row)

        description = "; ".join(
            f"{item.label}: {item.value}" for item in self._items
        )
        self.setAccessibleName("Response distribution legend")
        self.setAccessibleDescription(description or "No distribution data")
        self.setVisible(bool(self._items))


def _normalize_legend_items(
    items: Mapping[str, Any] | Iterable[Any],
) -> list[LegendItem]:
    if isinstance(items, Mapping):
        raw_items: Iterable[Any] = items.items()
    else:
        raw_items = items or ()
    normalized: list[LegendItem] = []
    for index, item in enumerate(raw_items):
        label: Any
        value: Any
        color: Any = theme.CHART_COLORS[index % len(theme.CHART_COLORS)]
        if isinstance(item, Mapping):
            label = item.get("label", item.get("name", ""))
            value = item.get("value", item.get("count", ""))
            color = item.get("color", color)
        else:
            values = tuple(item) if isinstance(item, (tuple, list)) else ()
            if len(values) < 2:
                continue
            label, value = values[0], values[1]
            if len(values) > 2:
                color = values[2]
        clean_label = str(label or "").strip()
        clean_color = QColor(color)
        if not clean_label or not clean_color.isValid():
            continue
        normalized.append(
            LegendItem(clean_label, str(value), clean_color.name())
        )
    return normalized


def _clear_layout(layout: QVBoxLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        child_layout = item.layout()
        if widget is not None:
            widget.deleteLater()
        elif child_layout is not None:
            _clear_nested_layout(child_layout)


def _clear_nested_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        child_layout = item.layout()
        if widget is not None:
            widget.deleteLater()
        elif child_layout is not None:
            _clear_nested_layout(child_layout)
