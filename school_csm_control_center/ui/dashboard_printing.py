from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math
from typing import Any, Iterable

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QRegion
from PySide6.QtPrintSupport import QPrinter
from PySide6.QtWidgets import (
    QApplication,
    QAbstractButton,
    QAbstractScrollArea,
    QGridLayout,
    QTableWidget,
    QWidget,
)

from school_csm_control_center.app_identity import FORMAL_APPLICATION_NAME
from school_csm_control_center.ui import theme


PRINT_PAGE_BACKGROUND = QColor("#FFFFFF")
PHILIPPINE_STANDARD_TIME = timezone(timedelta(hours=8), name="PHT")
FOOTER_HEIGHT_MM = 22.0
FOOTER_PADDING_MM = 1.5


def _printer_printable_rect(printer: QPrinter) -> QRectF:
    """Return the painter-space printable rectangle.

    QPrinter uses the printable-area origin when full-page mode is disabled.
    Normalizing the page-layout paint rectangle to (0, 0) prevents margins
    from being applied twice and keeps the footer inside the printable area.
    """

    raw = QRectF(printer.pageLayout().paintRectPixels(max(72, printer.resolution())))
    return QRectF(0.0, 0.0, max(1.0, raw.width()), max(1.0, raw.height()))


# ISO/IEC 16388 Code 39 encodings. Each symbol has nine alternating bar/space
# elements; ``w`` is three modules wide and ``n`` is one module wide.
CODE39_PATTERNS: dict[str, str] = {
    "0": "nnnwwnwnn", "1": "wnnwnnnnw", "2": "nnwwnnnnw",
    "3": "wnwwnnnnn", "4": "nnnwwnnnw", "5": "wnnwwnnnn",
    "6": "nnwwwnnnn", "7": "nnnwnnwnw", "8": "wnnwnnwnn",
    "9": "nnwwnnwnn", "A": "wnnnnwnnw", "B": "nnwnnwnnw",
    "C": "wnwnnwnnn", "D": "nnnnwwnnw", "E": "wnnnwwnnn",
    "F": "nnwnwwnnn", "G": "nnnnnwwnw", "H": "wnnnnwwnn",
    "I": "nnwnnwwnn", "J": "nnnnwwwnn", "K": "wnnnnnnww",
    "L": "nnwnnnnww", "M": "wnwnnnnwn", "N": "nnnnwnnww",
    "O": "wnnnwnnwn", "P": "nnwnwnnwn", "Q": "nnnnnnwww",
    "R": "wnnnnnwwn", "S": "nnwnnnwwn", "T": "nnnnwnwwn",
    "U": "wwnnnnnnw", "V": "nwwnnnnnw", "W": "wwwnnnnnn",
    "X": "nwnnwnnnw", "Y": "wwnnwnnnn", "Z": "nwwnwnnnn",
    "-": "nwnnnnwnw", ".": "wwnnnnwnn", " ": "nwwnnnwnn",
    "$": "nwnwnwnnn", "/": "nwnwnnnwn", "+": "nwnnnwnwn",
    "%": "nnnwnwnwn", "*": "nwnnwnwnn",
}


def code39_modules(value: str) -> tuple[bool, ...]:
    """Return a standards-based Code 39 module stream for ``value``.

    ``True`` modules are black bars. A wide element is three times a narrow
    element, a one-module gap separates symbols, and start/stop asterisks are
    added automatically.
    """

    normalized = str(value or "").strip().upper()
    if not normalized:
        raise ValueError("A print control number is required for the barcode.")
    unsupported = sorted({character for character in normalized if character not in CODE39_PATTERNS or character == "*"})
    if unsupported:
        raise ValueError(
            "The print control number contains unsupported Code 39 character(s): "
            + ", ".join(repr(character) for character in unsupported)
        )

    modules: list[bool] = []
    encoded = f"*{normalized}*"
    for symbol_index, symbol in enumerate(encoded):
        pattern = CODE39_PATTERNS[symbol]
        for element_index, width in enumerate(pattern):
            modules.extend([element_index % 2 == 0] * (3 if width == "w" else 1))
        if symbol_index != len(encoded) - 1:
            modules.append(False)
    return tuple(modules)


@dataclass(frozen=True)
class PrintReportMetadata:
    control_number: str
    generated_at: datetime
    system_name: str = FORMAL_APPLICATION_NAME
    school_name: str = "School"
    filter_scope: str = "Date range: All available responses"
    school_logo_path: str = ""
    deped_logo_path: str = ""
    app_logo_path: str = ""
    mosslab_logo_path: str = ""
    mosslab_seal_path: str = ""

    def __post_init__(self) -> None:
        control_number = str(self.control_number or "").strip().upper()
        system_name = str(self.system_name or "").strip()
        school_name = str(self.school_name or "").strip()
        filter_scope = " ".join(str(self.filter_scope or "").split())
        if not control_number:
            raise ValueError("A print control number is required.")
        if not isinstance(self.generated_at, datetime):
            raise TypeError("generated_at must be a datetime.")
        code39_modules(control_number)
        object.__setattr__(self, "control_number", control_number)
        object.__setattr__(self, "system_name", system_name or FORMAL_APPLICATION_NAME)
        object.__setattr__(self, "school_name", school_name or "School")
        object.__setattr__(self, "filter_scope", filter_scope or "Date range: All available responses")
        for field_name in (
            "school_logo_path",
            "deped_logo_path",
            "app_logo_path",
            "mosslab_logo_path",
            "mosslab_seal_path",
        ):
            object.__setattr__(self, field_name, str(getattr(self, field_name) or "").strip())

    @property
    def timestamp_text(self) -> str:
        local_time = self.generated_at.astimezone(PHILIPPINE_STANDARD_TIME)
        return local_time.strftime("%d %B %Y, %I:%M:%S %p PHT")


@dataclass(frozen=True)
class DashboardSnapshot:
    image: QImage
    logical_width: int
    logical_height: int
    render_scale: float
    component_bounds: dict[str, QRectF]
    report_metadata: PrintReportMetadata | None = None


@dataclass(frozen=True)
class PrintPage:
    number: int
    source_rect: QRectF
    target_rect: QRectF


@dataclass(frozen=True)
class _GridPlacement:
    widget: QWidget
    row: int
    column: int
    row_span: int
    column_span: int
    alignment: Qt.AlignmentFlag


def _grid_placements(layout: QGridLayout) -> list[_GridPlacement]:
    placements: list[_GridPlacement] = []
    for index in range(layout.count()):
        item = layout.itemAt(index)
        widget = item.widget()
        if widget is None:
            continue
        row, column, row_span, column_span = layout.getItemPosition(index)
        placements.append(
            _GridPlacement(
                widget,
                row,
                column,
                row_span,
                column_span,
                item.alignment(),
            )
        )
    return placements


def _take_grid(layout: QGridLayout) -> list[_GridPlacement]:
    placements = _grid_placements(layout)
    while layout.count():
        layout.takeAt(0)
    return placements


def _restore_grid(layout: QGridLayout, placements: Iterable[_GridPlacement]) -> None:
    while layout.count():
        layout.takeAt(0)
    for item in placements:
        layout.addWidget(
            item.widget,
            item.row,
            item.column,
            item.row_span,
            item.column_span,
            item.alignment,
        )
    layout.invalidate()
    layout.activate()


def _draw_code39(painter: QPainter, rect: QRectF, value: str) -> None:
    modules = code39_modules(value)
    if not modules or rect.width() <= 0 or rect.height() <= 0:
        return
    quiet_modules = 10
    module_width = rect.width() / (len(modules) + quiet_modules * 2)
    left = rect.left() + quiet_modules * module_width
    painter.save()
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#07131F"))
    run_start = 0
    while run_start < len(modules):
        value_at_run = modules[run_start]
        run_end = run_start + 1
        while run_end < len(modules) and modules[run_end] == value_at_run:
            run_end += 1
        if value_at_run:
            painter.drawRect(
                QRectF(
                    left + run_start * module_width,
                    rect.top(),
                    max(module_width, (run_end - run_start) * module_width),
                    rect.height(),
                )
            )
        run_start = run_end
    painter.restore()


def _draw_report_header(
    painter: QPainter,
    rect: QRectF,
    metadata: PrintReportMetadata,
) -> None:
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(QPen(QColor(theme.CARD_BORDER), 1.0))
    painter.setBrush(QColor("#F3F8FB"))
    painter.drawRoundedRect(rect, 10, 10)

    left = rect.left() + 16
    top = rect.top() + 11
    text_width = max(180.0, rect.width() * 0.53)
    painter.setPen(QColor("#0A1D30"))
    painter.setFont(QFont("Segoe UI", 15, QFont.Weight.Bold))
    painter.drawText(
        QRectF(left, top, text_width, 25),
        int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
        "Complete Dashboard Analysis",
    )
    painter.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))
    painter.setPen(QColor("#24536A"))
    painter.drawText(
        QRectF(left, top + 29, text_width, 20),
        int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
        f"System-generated by {metadata.system_name}",
    )
    painter.setFont(QFont("Segoe UI", 8))
    painter.setPen(QColor("#3D5D6D"))
    painter.drawText(
        QRectF(left, top + 52, text_width, 18),
        int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
        f"Generated: {metadata.timestamp_text}",
    )
    painter.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
    painter.drawText(
        QRectF(left, top + 73, text_width, 18),
        int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
        f"Print control number: {metadata.control_number}",
    )
    painter.setFont(QFont("Segoe UI", 8, QFont.Weight.DemiBold))
    painter.setPen(QColor("#24536A"))
    painter.drawText(
        QRectF(left, top + 94, max(180.0, rect.width() - 32), 18),
        int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
        metadata.filter_scope,
    )

    barcode_left = rect.left() + max(text_width + 31, rect.width() * 0.58)
    barcode_rect = QRectF(
        barcode_left,
        top + 5,
        max(100.0, rect.right() - barcode_left - 16),
        58,
    )
    _draw_code39(painter, barcode_rect, metadata.control_number)
    painter.setFont(QFont("Consolas", 8, QFont.Weight.Bold))
    painter.setPen(QColor("#07131F"))
    painter.drawText(
        QRectF(barcode_rect.left(), barcode_rect.bottom() + 3, barcode_rect.width(), 19),
        int(Qt.AlignmentFlag.AlignCenter),
        f"*{metadata.control_number}*",
    )
    painter.restore()


def _draw_image_contained(
    painter: QPainter,
    rect: QRectF,
    path: str,
    *,
    placeholder: str = "",
    framed: bool = False,
) -> bool:
    """Draw an image centered inside ``rect`` while preserving its aspect ratio."""

    image = QImage(path) if path else QImage()
    painter.save()
    if framed:
        painter.setPen(QPen(QColor("#B8CBD4"), 0.8))
        painter.setBrush(QColor("#F8FBFD"))
        painter.drawRoundedRect(rect, 4.0, 4.0)
        content_rect = rect.adjusted(2.0, 2.0, -2.0, -2.0)
    else:
        content_rect = rect
    if not image.isNull() and content_rect.width() > 0 and content_rect.height() > 0:
        scaled = image.size().scaled(
            max(1, int(content_rect.width())),
            max(1, int(content_rect.height())),
            Qt.AspectRatioMode.KeepAspectRatio,
        )
        target = QRectF(
            content_rect.center().x() - scaled.width() / 2.0,
            content_rect.center().y() - scaled.height() / 2.0,
            float(scaled.width()),
            float(scaled.height()),
        )
        painter.drawImage(target, image)
        painter.restore()
        return True
    if placeholder:
        painter.setPen(QColor("#476675"))
        placeholder_font = QFont("Segoe UI")
        placeholder_font.setPointSizeF(5.5)
        placeholder_font.setWeight(QFont.Weight.Bold)
        painter.setFont(placeholder_font)
        painter.drawText(
            content_rect,
            int(Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap),
            placeholder,
        )
    painter.restore()
    return False


class DashboardPrintRenderer:
    """Capture and paginate the full Dashboard without changing saved data."""

    @staticmethod
    def apply_ink_mode(snapshot: DashboardSnapshot, mode: str = "standard") -> DashboardSnapshot:
        """Return a paper-friendly copy of a captured Dashboard.

        ``low_ink`` lightens broad card and chart fills while preserving dark
        text, table rules, chart strokes, and other small foreground details.
        The source snapshot remains untouched so the operator can switch modes
        without recapturing the live Dashboard.
        """

        normalized = str(mode or "standard").strip().casefold()
        if normalized not in {"low_ink", "light", "light_ink"}:
            return snapshot
        source = snapshot.image.convertToFormat(QImage.Format.Format_RGBA8888).copy()
        if source.isNull():
            return snapshot
        try:
            import cv2  # type: ignore
            import numpy as np

            height = source.height()
            width = source.width()
            stride = source.bytesPerLine()
            raw = np.frombuffer(source.bits(), dtype=np.uint8, count=source.sizeInBytes())
            rows = raw.reshape((height, stride))
            rgba = rows[:, : width * 4].reshape((height, width, 4))
            rgb = rgba[:, :, :3].astype(np.float32)
            gray = cv2.cvtColor(rgb.astype(np.uint8), cv2.COLOR_RGB2GRAY)
            local_mean = cv2.GaussianBlur(gray, (0, 0), 2.2)
            dark_detail = ((local_mean.astype(np.int16) - gray.astype(np.int16)) > 15) & (gray < 205)
            dark_detail = cv2.dilate(dark_detail.astype(np.uint8), np.ones((2, 2), np.uint8), iterations=1).astype(bool)

            # Large fills receive a strong white blend. Dark foreground details
            # are retained so labels and chart lines remain readable.
            lightened = rgb + (255.0 - rgb) * 0.76
            preserved = np.minimum(230.0, rgb * 0.92)
            lightened[dark_detail] = preserved[dark_detail]
            lightened[lightened > 248.0] = 255.0
            rgba[:, :, :3] = np.clip(lightened, 0, 255).astype(np.uint8)
            processed = source.copy()
        except Exception:
            # Conservative fallback when optional image-processing modules are
            # unavailable: blend the report toward white without altering alpha.
            processed = QImage(source.size(), QImage.Format.Format_ARGB32_Premultiplied)
            processed.fill(QColor("#FFFFFF"))
            painter = QPainter(processed)
            painter.setOpacity(0.34)
            painter.drawImage(0, 0, source)
            painter.end()

        return DashboardSnapshot(
            image=processed,
            logical_width=snapshot.logical_width,
            logical_height=snapshot.logical_height,
            render_scale=snapshot.render_scale,
            component_bounds=dict(snapshot.component_bounds),
            report_metadata=snapshot.report_metadata,
        )

    @staticmethod
    def capture(
        board: QWidget,
        *,
        render_scale: float = 2.0,
        metadata: PrintReportMetadata | None = None,
    ) -> DashboardSnapshot:
        scale = min(3.0, max(1.0, float(render_scale)))
        header = board.findChild(QWidget, "dashboard_header")
        filter_strip = getattr(board, "filter_strip", None)
        body = getattr(board, "body", None)
        if header is None or filter_strip is None or body is None:
            raise ValueError("The Dashboard is missing printable header, filter, or body content.")

        outer_scroll = getattr(board, "scroll", None)
        saved_outer_value = outer_scroll.verticalScrollBar().value() if outer_scroll is not None else 0
        table_states: list[tuple[QTableWidget, dict[str, Any]]] = []
        scroll_states: list[tuple[QAbstractScrollArea, dict[str, Any]]] = []
        # Interactive controls belong to the live Dashboard, not to a printed
        # report. Preserve each button's explicit hidden state so capture can
        # temporarily remove action icons without changing the live interface.
        button_states: list[tuple[QAbstractButton, bool]] = [
            (button, button.isHidden())
            for button in board.findChildren(QAbstractButton)
        ]
        body_state = {
            "minimum_height": body.minimumHeight(),
            "maximum_height": body.maximumHeight(),
            "size": body.size(),
        }
        board_state = {
            "minimum_width": board.minimumWidth(),
            "maximum_width": board.maximumWidth(),
            "size": board.size(),
        }
        body_print_stylesheet = body.styleSheet()
        kpi_layout = getattr(board, "kpi_grid", None)
        analysis_layout = getattr(board, "analysis_grid", None)
        if not isinstance(kpi_layout, QGridLayout) or not isinstance(analysis_layout, QGridLayout):
            raise ValueError("The Dashboard is missing its printable section layouts.")
        kpi_placements: list[_GridPlacement] = []
        analysis_placements: list[_GridPlacement] = []
        kpi_column_stretches = [
            kpi_layout.columnStretch(column)
            for column in range(max(4, kpi_layout.columnCount()))
        ]
        analysis_column_stretches = [
            analysis_layout.columnStretch(column)
            for column in range(max(3, analysis_layout.columnCount()))
        ]

        try:
            for button, _was_hidden in button_states:
                button.setHidden(True)
            QApplication.processEvents()

            # Printing uses a purpose-built reading order. Reflow the cards into
            # a single column temporarily; the original grid coordinates and
            # alignments are restored in ``finally``.
            kpi_placements = _grid_placements(kpi_layout)
            analysis_placements = _grid_placements(analysis_layout)
            print_width = min(board.width(), 820)
            board.setMinimumWidth(0)
            board.setMaximumWidth(print_width)
            board.resize(print_width, board.height())
            reflow = getattr(board, "_reflow", None)
            if callable(reflow):
                reflow(force=True)
            QApplication.processEvents()
            _take_grid(kpi_layout)
            _take_grid(analysis_layout)
            metric_order = list(getattr(board, "metric_cards", []))
            analysis_order = list(getattr(board, "analysis_widgets", []))
            for column in range(len(kpi_column_stretches)):
                kpi_layout.setColumnStretch(column, 1 if column == 0 else 0)
            for column in range(len(analysis_column_stretches)):
                analysis_layout.setColumnStretch(column, 1 if column == 0 else 0)
            for row, widget in enumerate(metric_order):
                kpi_layout.addWidget(widget, row, 0)
            for row, widget in enumerate(analysis_order):
                analysis_layout.addWidget(widget, row, 0)
            kpi_layout.invalidate()
            analysis_layout.invalidate()
            kpi_layout.activate()
            analysis_layout.activate()

            for name in ("services_table", "recent_table"):
                table = getattr(board, name, None)
                if not isinstance(table, QTableWidget):
                    continue
                state = {
                    "minimum_height": table.minimumHeight(),
                    "maximum_height": table.maximumHeight(),
                    "vertical_policy": table.verticalScrollBarPolicy(),
                    "vertical_value": table.verticalScrollBar().value(),
                    "horizontal_value": table.horizontalScrollBar().value(),
                }
                table_states.append((table, state))
                row_height = table.verticalHeader().defaultSectionSize()
                rows_height = sum(
                    table.rowHeight(row) if table.rowHeight(row) > 0 else row_height
                    for row in range(table.rowCount())
                    if not table.isRowHidden(row)
                )
                full_height = table.horizontalHeader().height() + rows_height + table.frameWidth() * 2 + 6
                table.setFixedHeight(max(state["minimum_height"], full_height))
                table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
                table.verticalScrollBar().setValue(0)
                table.horizontalScrollBar().setValue(0)

            insights_scroll = getattr(board, "insights_scroll", None)
            insights_content = getattr(board, "insights_content", None)
            if isinstance(insights_scroll, QAbstractScrollArea) and isinstance(insights_content, QWidget):
                state = {
                    "minimum_height": insights_scroll.minimumHeight(),
                    "maximum_height": insights_scroll.maximumHeight(),
                    "vertical_policy": insights_scroll.verticalScrollBarPolicy(),
                    "vertical_value": insights_scroll.verticalScrollBar().value(),
                    "content_size": insights_content.size(),
                    "content_widget": insights_content,
                }
                scroll_states.append((insights_scroll, state))
                content_height = max(
                    insights_content.minimumSizeHint().height(),
                    insights_content.sizeHint().height(),
                    insights_content.layout().sizeHint().height() if insights_content.layout() is not None else 0,
                )
                insights_scroll.setFixedHeight(max(state["minimum_height"], content_height + 4))
                insights_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
                insights_scroll.verticalScrollBar().setValue(0)

            # Print on a clean paper canvas while preserving the intentional
            # fills of cards, charts, tables, headers, and analysis sections.
            body.setStyleSheet("QWidget#dashboard_body { background: transparent; }")
            QApplication.processEvents()
            expanded_body_height = max(
                body.height(),
                body.sizeHint().height(),
                body.layout().sizeHint().height() if body.layout() is not None else 0,
            )
            body.setFixedHeight(expanded_body_height)
            QApplication.processEvents()
            printable_widgets = [
                *getattr(board, "metric_cards", []),
                *getattr(board, "analysis_widgets", []),
            ]
            if printable_widgets:
                bottom_margin = (
                    body.layout().contentsMargins().bottom()
                    if body.layout() is not None
                    else 0
                )
                content_height = max(
                    widget.geometry().bottom() + 1
                    for widget in printable_widgets
                ) + bottom_margin
                if 0 < content_height < body.height():
                    body.setFixedHeight(content_height)
                    QApplication.processEvents()

            margin_left = 14
            margin_right = 14
            margin_top = 12
            margin_bottom = 12
            gap = 9
            report_header_height = 138 if metadata is not None else 0
            content_width = max(header.width(), filter_strip.width(), body.width())
            logical_width = margin_left + content_width + margin_right
            logical_height = (
                margin_top
                + report_header_height
                + (gap if metadata is not None else 0)
                + header.height()
                + gap
                + filter_strip.height()
                + gap
                + body.height()
                + margin_bottom
            )
            physical_size = QSize(
                max(1, math.ceil(logical_width * scale)),
                max(1, math.ceil(logical_height * scale)),
            )
            image = QImage(physical_size, QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(PRINT_PAGE_BACKGROUND)
            painter = QPainter(image)
            if not painter.isActive():
                raise RuntimeError("The Dashboard print image could not be created.")
            painter.scale(scale, scale)

            components: dict[str, QRectF] = {}
            y = margin_top
            if metadata is not None:
                report_rect = QRectF(margin_left, y, content_width, report_header_height)
                _draw_report_header(painter, report_rect, metadata)
                components["report_header"] = report_rect
                y += report_header_height + gap
            flags = QWidget.RenderFlag.DrawWindowBackground | QWidget.RenderFlag.DrawChildren
            for name, widget in (
                ("header", header),
                ("filters", filter_strip),
                ("body", body),
            ):
                x = margin_left
                widget.render(painter, QPoint(x, y), QRegion(), flags)
                components[name] = QRectF(x, y, widget.width(), widget.height())
                if name == "body":
                    for index, card in enumerate(getattr(board, "metric_cards", []), 1):
                        card_rect = card.geometry()
                        components[f"metric_{index}"] = QRectF(
                            x + card_rect.x(),
                            y + card_rect.y(),
                            card_rect.width(),
                            card_rect.height(),
                        )
                    for index, section in enumerate(getattr(board, "analysis_widgets", []), 1):
                        section_rect = section.geometry()
                        components[f"section_{index}"] = QRectF(
                            x + section_rect.x(),
                            y + section_rect.y(),
                            section_rect.width(),
                            section_rect.height(),
                        )
                y += widget.height() + (gap if name != "body" else 0)
            painter.end()
            return DashboardSnapshot(
                image=image,
                logical_width=logical_width,
                logical_height=logical_height,
                render_scale=scale,
                component_bounds=components,
                report_metadata=metadata,
            )
        finally:
            for table, state in table_states:
                table.setMinimumHeight(state["minimum_height"])
                table.setMaximumHeight(state["maximum_height"])
                table.setVerticalScrollBarPolicy(state["vertical_policy"])
                table.verticalScrollBar().setValue(state["vertical_value"])
                table.horizontalScrollBar().setValue(state["horizontal_value"])
                table.updateGeometry()
            for scroll, state in scroll_states:
                scroll.setMinimumHeight(state["minimum_height"])
                scroll.setMaximumHeight(state["maximum_height"])
                scroll.setVerticalScrollBarPolicy(state["vertical_policy"])
                scroll.verticalScrollBar().setValue(state["vertical_value"])
                state["content_widget"].resize(state["content_size"])
                scroll.updateGeometry()
            if kpi_placements:
                _restore_grid(kpi_layout, kpi_placements)
            if analysis_placements:
                _restore_grid(analysis_layout, analysis_placements)
            for column, stretch in enumerate(kpi_column_stretches):
                kpi_layout.setColumnStretch(column, stretch)
            for column, stretch in enumerate(analysis_column_stretches):
                analysis_layout.setColumnStretch(column, stretch)
            board.setMaximumWidth(board_state["maximum_width"])
            board.setMinimumWidth(board_state["minimum_width"])
            board.resize(board_state["size"])
            for button, was_hidden in button_states:
                button.setHidden(was_hidden)
            reflow = getattr(board, "_reflow", None)
            if callable(reflow):
                reflow(force=True)
            body.setStyleSheet(body_print_stylesheet)
            if body.layout() is not None:
                body.layout().invalidate()
                body.layout().activate()
            # Hold the exact live body geometry through one completed layout
            # pass. Restoring flexible constraints too early lets the temporary
            # single-column print size leak back into the Dashboard.
            body.setFixedHeight(body_state["size"].height())
            QApplication.processEvents()
            body.setMinimumHeight(body_state["minimum_height"])
            body.setMaximumHeight(body_state["maximum_height"])
            body.resize(body_state["size"])
            body.updateGeometry()
            if outer_scroll is not None:
                outer_scroll.verticalScrollBar().setValue(saved_outer_value)

    @staticmethod
    def build_page_plan(
        source_size: QSize,
        paint_rect: QRect | QRectF,
        *,
        scale_mode: str = "fit_width",
        width_percent: int = 100,
        break_regions: Iterable[QRectF] = (),
    ) -> list[PrintPage]:
        source_width = float(source_size.width())
        source_height = float(source_size.height())
        target = QRectF(paint_rect)
        if source_width <= 0 or source_height <= 0 or target.width() <= 0 or target.height() <= 0:
            return []

        if scale_mode == "fit_page":
            scale = min(target.width() / source_width, target.height() / source_height)
            target_width = source_width * scale
            target_height = source_height * scale
            target_rect = QRectF(
                target.left() + (target.width() - target_width) / 2,
                target.top() + (target.height() - target_height) / 2,
                target_width,
                target_height,
            )
            return [PrintPage(1, QRectF(0, 0, source_width, source_height), target_rect)]

        percent = min(100, max(25, int(width_percent))) if scale_mode == "custom" else 100
        scale = (target.width() / source_width) * (percent / 100.0)
        target_width = source_width * scale
        source_page_height = target.height() / scale
        target_left = target.left() + (target.width() - target_width) / 2
        regions = sorted(
            (
                QRectF(region)
                for region in break_regions
                if QRectF(region).height() > 0 and QRectF(region).width() > 0
            ),
            key=lambda region: region.top(),
        )
        pages: list[PrintPage] = []
        source_y = 0.0
        while source_y < source_height - 0.01:
            slice_height = min(source_page_height, source_height - source_y)
            nominal_end = source_y + slice_height
            if nominal_end < source_height - 0.01:
                # Prefer a boundary immediately before a card that would
                # otherwise be split. Cards taller than one page must split.
                split_candidates = [
                    region.top()
                    for region in regions
                    if (
                        region.top() > source_y + 0.01
                        and region.top() < nominal_end - 0.01
                        and region.bottom() > nominal_end + 0.01
                        and region.height() <= source_page_height + 0.01
                    )
                ]
                if split_candidates:
                    slice_height = max(0.01, max(split_candidates) - source_y)
            pages.append(
                PrintPage(
                    len(pages) + 1,
                    QRectF(0, source_y, source_width, slice_height),
                    QRectF(target_left, target.top(), target_width, slice_height * scale),
                )
            )
            source_y += slice_height
        return pages

    @staticmethod
    def printer_page_plan(
        printer: QPrinter,
        snapshot: DashboardSnapshot,
        *,
        scale_mode: str = "fit_width",
        width_percent: int = 100,
    ) -> list[PrintPage]:
        paint_rect = _printer_printable_rect(printer)
        if snapshot.report_metadata is not None:
            resolution = max(72, printer.resolution())
            footer_height = max(42.0, resolution * FOOTER_HEIGHT_MM / 25.4)
            paint_rect.setHeight(max(1.0, paint_rect.height() - footer_height))
        break_regions = [
            QRectF(
                bounds.left() * snapshot.render_scale,
                bounds.top() * snapshot.render_scale,
                bounds.width() * snapshot.render_scale,
                bounds.height() * snapshot.render_scale,
            )
            for name, bounds in snapshot.component_bounds.items()
            if name.startswith(("report_header", "header", "filters", "metric_", "section_"))
        ]
        return DashboardPrintRenderer.build_page_plan(
            snapshot.image.size(),
            paint_rect,
            scale_mode=scale_mode,
            width_percent=width_percent,
            break_regions=break_regions,
        )

    @staticmethod
    def paint(
        printer: QPrinter,
        snapshot: DashboardSnapshot,
        *,
        scale_mode: str = "fit_width",
        width_percent: int = 100,
        selected_pages: Iterable[int] | None = None,
    ) -> int:
        plan = DashboardPrintRenderer.printer_page_plan(
            printer,
            snapshot,
            scale_mode=scale_mode,
            width_percent=width_percent,
        )
        if selected_pages is not None:
            wanted = {int(page) for page in selected_pages}
            plan = [page for page in plan if page.number in wanted]
        if not plan:
            return 0

        painter = QPainter()
        if not painter.begin(printer):
            raise RuntimeError("The selected printer could not start the print job.")
        try:
            for index, page in enumerate(plan):
                if index and not printer.newPage():
                    raise RuntimeError("The printer could not create the next Dashboard page.")
                painter.drawImage(page.target_rect, snapshot.image, page.source_rect)
                if snapshot.report_metadata is not None:
                    DashboardPrintRenderer._paint_footer(
                        painter,
                        printer,
                        snapshot.report_metadata,
                        page.number,
                        len(DashboardPrintRenderer.printer_page_plan(
                            printer,
                            snapshot,
                            scale_mode=scale_mode,
                            width_percent=width_percent,
                        )),
                    )
        finally:
            painter.end()
        return len(plan)

    @staticmethod
    def _paint_footer(
        painter: QPainter,
        printer: QPrinter,
        metadata: PrintReportMetadata,
        page_number: int,
        total_pages: int,
    ) -> None:
        paint_rect = _printer_printable_rect(printer)
        resolution = max(72, printer.resolution())
        mm = resolution / 25.4
        footer_height = max(74.0, mm * FOOTER_HEIGHT_MM)
        footer_padding = max(4.0, mm * FOOTER_PADDING_MM)
        footer_rect = QRectF(
            0.0,
            max(0.0, paint_rect.height() - footer_height),
            paint_rect.width(),
            max(1.0, footer_height - 1.0),
        )

        painter.save()
        painter.setClipRect(paint_rect)
        painter.fillRect(footer_rect, QColor("#FFFFFF"))
        painter.setPen(QPen(QColor("#55727F"), max(1.0, resolution / 300.0)))
        painter.drawLine(footer_rect.topLeft(), footer_rect.topRight())

        content = footer_rect.adjusted(0.0, footer_padding, 0.0, -footer_padding)
        gap = max(5.0, mm * 1.4)
        logo_height = min(content.height(), mm * 15.0)
        school_slot = QRectF(
            content.left(), content.center().y() - logo_height / 2.0, logo_height, logo_height
        )
        deped_width = min(mm * 25.0, content.width() * 0.13)
        deped_slot = QRectF(
            school_slot.right() + gap,
            content.center().y() - logo_height / 2.0,
            deped_width,
            logo_height,
        )
        app_slot = QRectF(
            deped_slot.right() + gap,
            content.center().y() - logo_height / 2.0,
            logo_height,
            logo_height,
        )
        mosslab_width = min(mm * 25.0, content.width() * 0.13)
        mosslab_slot = QRectF(
            app_slot.right() + gap,
            content.center().y() - logo_height / 2.0,
            mosslab_width,
            logo_height,
        )
        seal_slot = QRectF(
            mosslab_slot.right() + gap,
            content.center().y() - logo_height / 2.0,
            logo_height,
            logo_height,
        )

        _draw_image_contained(
            painter, school_slot, metadata.school_logo_path, placeholder="SCHOOL"
        )
        _draw_image_contained(painter, deped_slot, metadata.deped_logo_path)
        _draw_image_contained(painter, app_slot, metadata.app_logo_path)
        _draw_image_contained(painter, mosslab_slot, metadata.mosslab_logo_path)
        _draw_image_contained(painter, seal_slot, metadata.mosslab_seal_path)

        text_left = seal_slot.right() + gap * 1.4
        text_rect = QRectF(
            text_left,
            content.top(),
            max(1.0, content.right() - text_left),
            content.height(),
        )
        page_width = min(max(mm * 22.0, text_rect.width() * 0.18), text_rect.width() * 0.28)
        detail_rect = QRectF(
            text_rect.left(),
            text_rect.top(),
            max(1.0, text_rect.width() - page_width - gap),
            text_rect.height(),
        )
        page_rect = QRectF(
            text_rect.right() - page_width,
            text_rect.top(),
            page_width,
            text_rect.height(),
        )

        school_font = QFont("Segoe UI")
        school_font.setPointSizeF(7.4)
        school_font.setWeight(QFont.Weight.Bold)
        painter.setFont(school_font)
        painter.setPen(QColor("#123D5A"))
        school_line = painter.fontMetrics().elidedText(
            metadata.school_name, Qt.TextElideMode.ElideRight, max(1, int(detail_rect.width()))
        )
        line_height = max(9.0, detail_rect.height() / 3.2)
        painter.drawText(
            QRectF(detail_rect.left(), detail_rect.top(), detail_rect.width(), line_height),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextSingleLine),
            school_line,
        )

        detail_font = QFont("Segoe UI")
        detail_font.setPointSizeF(6.2)
        painter.setFont(detail_font)
        painter.setPen(QColor("#385A69"))
        system_line = painter.fontMetrics().elidedText(
            f"System-generated by {metadata.system_name}",
            Qt.TextElideMode.ElideRight,
            max(1, int(detail_rect.width())),
        )
        painter.drawText(
            QRectF(detail_rect.left(), detail_rect.top() + line_height, detail_rect.width(), line_height),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextSingleLine),
            system_line,
        )
        audit_line = painter.fontMetrics().elidedText(
            f"{metadata.timestamp_text}  |  Control {metadata.control_number}",
            Qt.TextElideMode.ElideRight,
            max(1, int(detail_rect.width())),
        )
        painter.drawText(
            QRectF(detail_rect.left(), detail_rect.bottom() - line_height, detail_rect.width(), line_height),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextSingleLine),
            audit_line,
        )

        page_font = QFont("Segoe UI")
        page_font.setPointSizeF(7.0)
        page_font.setWeight(QFont.Weight.Bold)
        painter.setFont(page_font)
        painter.setPen(QColor("#123D5A"))
        painter.drawText(
            page_rect,
            int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextSingleLine),
            f"Page {page_number} of {total_pages}",
        )
        painter.restore()
