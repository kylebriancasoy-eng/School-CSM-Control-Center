"""Deterministic multi-page rendering for snapshot-backed Narrative Reports."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Sequence

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QTextDocument,
)
from PySide6.QtPrintSupport import QPrinter


VALIDITY_STATEMENT = (
    "This Narrative Report is valid only when attached to or accompanied by the "
    "successful Dashboard Printout bearing Control Number "
    "{DASHBOARD_CONTROL_NUMBER}. Without the referenced Dashboard Printout, this "
    "Narrative Report shall be considered invalid."
)


@dataclass(frozen=True)
class NarrativePrintDocument:
    dashboard_control_number: str
    analysis: Mapping[str, Any]
    narrative: Mapping[str, Any]
    school: Mapping[str, Any]
    report_scope: str
    source_generated_at: str
    approved_by: str = ""
    approved_at: str = ""
    revision_number: int = 1
    generation_mode: str = "local"

    def __post_init__(self) -> None:
        control = str(self.dashboard_control_number or "").strip().upper()
        if not control:
            raise ValueError("A Dashboard print control number is required.")
        object.__setattr__(self, "dashboard_control_number", control)

    @property
    def validity_statement(self) -> str:
        return VALIDITY_STATEMENT.format(
            DASHBOARD_CONTROL_NUMBER=self.dashboard_control_number
        )


class NarrativeReportRenderer:
    """Render fixed report sections and graphs from one stored analysis snapshot."""

    PAGE_WIDTH = 1191
    PAGE_HEIGHT = 1684
    MARGIN = 68
    HEADER_HEIGHT = 118
    FOOTER_HEIGHT = 82

    @classmethod
    def build_page_specs(
        cls, document: NarrativePrintDocument
    ) -> list[dict[str, Any]]:
        narrative = document.narrative
        return [
            {
                "title": "Executive Summary",
                "chart": "overview",
                "body": cls._section_text(narrative, "executive_summary"),
                "show_validity": True,
            },
            {
                "title": "Scope and Methodology",
                "chart": "none",
                "body": cls._section_text(narrative, "scope_and_methodology"),
            },
            {
                "title": "Key Findings",
                "chart": "dimensions",
                "body": cls._section_text(narrative, "key_findings"),
            },
            {
                "title": "Strengths",
                "chart": "services",
                "body": cls._section_text(narrative, "strengths"),
            },
            {
                "title": "Areas for Improvement",
                "chart": "citizen_charter",
                "body": cls._section_text(narrative, "areas_for_improvement"),
            },
            {
                "title": "Recommended Actions",
                "chart": "demographics",
                "body": cls._section_text(narrative, "recommended_actions"),
            },
            {
                "title": "Conclusion and Satisfaction Trend",
                "chart": "trend",
                "body": cls._section_text(narrative, "conclusion"),
                "show_signatures": True,
            },
        ]

    @classmethod
    def render_pages(
        cls,
        document: NarrativePrintDocument,
        *,
        low_ink: bool = False,
    ) -> list[QImage]:
        specs = cls.build_page_specs(document)
        return [
            cls._render_page(
                document,
                spec,
                page_number=index,
                page_count=len(specs),
                low_ink=low_ink,
            )
            for index, spec in enumerate(specs, 1)
        ]

    @classmethod
    def paint(
        cls,
        printer: QPrinter,
        pages: Sequence[QImage],
        *,
        selected_pages: Sequence[int] | None = None,
    ) -> int:
        wanted = list(selected_pages or range(1, len(pages) + 1))
        wanted = [number for number in wanted if 1 <= int(number) <= len(pages)]
        if not wanted:
            return 0
        painter = QPainter()
        if not painter.begin(printer):
            raise RuntimeError("The selected printer could not start the Narrative Report job.")
        try:
            for output_index, page_number in enumerate(wanted):
                if output_index and not printer.newPage():
                    raise RuntimeError(
                        "The printer could not create the next Narrative Report page."
                    )
                image = pages[page_number - 1]
                target = QRectF(printer.pageRect(QPrinter.Unit.DevicePixel))
                source = QRectF(image.rect())
                scale = min(
                    target.width() / max(1.0, source.width()),
                    target.height() / max(1.0, source.height()),
                )
                width = source.width() * scale
                height = source.height() * scale
                destination = QRectF(
                    target.x() + (target.width() - width) / 2.0,
                    target.y() + (target.height() - height) / 2.0,
                    width,
                    height,
                )
                painter.drawImage(destination, image, source)
        finally:
            painter.end()
        return len(wanted)

    @classmethod
    def _render_page(
        cls,
        document: NarrativePrintDocument,
        spec: Mapping[str, Any],
        *,
        page_number: int,
        page_count: int,
        low_ink: bool,
    ) -> QImage:
        image = QImage(
            cls.PAGE_WIDTH,
            cls.PAGE_HEIGHT,
            QImage.Format.Format_ARGB32_Premultiplied,
        )
        image.fill(QColor("#FFFFFF"))
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        ink = QColor("#183042")
        accent = QColor("#2B7A78" if not low_ink else "#7A9A98")
        pale = QColor("#E8F3F2" if not low_ink else "#F5F8F8")

        content_left = cls.MARGIN
        content_width = cls.PAGE_WIDTH - cls.MARGIN * 2
        school_name = str(document.school.get("school_name") or "School")

        painter.setPen(ink)
        painter.setFont(QFont("Arial", 13, QFont.Weight.Bold))
        painter.drawText(
            QRectF(content_left, 42, content_width * 0.62, 32),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            "CLIENT SATISFACTION MEASUREMENT NARRATIVE REPORT",
        )
        painter.setFont(QFont("Arial", 9))
        painter.drawText(
            QRectF(content_left, 77, content_width * 0.65, 25),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            school_name,
        )
        painter.setFont(QFont("Arial", 9, QFont.Weight.Bold))
        painter.drawText(
            QRectF(
                content_left + content_width * 0.62,
                42,
                content_width * 0.38,
                30,
            ),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            document.dashboard_control_number,
        )
        painter.setFont(QFont("Arial", 8))
        painter.drawText(
            QRectF(
                content_left + content_width * 0.62,
                75,
                content_width * 0.38,
                26,
            ),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            f"Narrative revision {document.revision_number} · {document.generation_mode.title()}",
        )
        painter.fillRect(
            QRectF(content_left, cls.HEADER_HEIGHT - 8, content_width, 3), accent
        )

        y = cls.HEADER_HEIGHT + 18
        painter.setPen(ink)
        painter.setFont(QFont("Arial", 15, QFont.Weight.Bold))
        painter.drawText(
            QRectF(content_left, y, content_width, 40),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            str(spec.get("title") or "Narrative Report"),
        )
        y += 50
        painter.setFont(QFont("Arial", 8))
        painter.setPen(QColor("#52616B"))
        painter.drawText(
            QRectF(content_left, y, content_width, 40),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
            f"Dashboard scope: {document.report_scope or 'Complete Dashboard'}\n"
            f"Dashboard generated: {cls._timestamp(document.source_generated_at)}",
        )
        y += 48

        if bool(spec.get("show_validity")):
            validity_rect = QRectF(content_left, y, content_width, 92)
            painter.fillRect(validity_rect, pale)
            painter.setPen(QPen(accent, 2))
            painter.drawRoundedRect(validity_rect, 8, 8)
            cls._draw_rich_text(
                painter,
                validity_rect.adjusted(14, 11, -14, -10),
                f"<b>VALIDITY NOTICE</b><br>{document.validity_statement}",
                point_size=8.5,
            )
            y += 108

        chart_kind = str(spec.get("chart") or "none")
        if chart_kind != "none":
            chart_rect = QRectF(content_left, y, content_width, 430)
            painter.fillRect(chart_rect, QColor("#FAFCFD"))
            painter.setPen(QPen(QColor("#D1DEE5"), 1))
            painter.drawRoundedRect(chart_rect, 8, 8)
            cls._draw_chart(
                painter,
                chart_rect.adjusted(20, 22, -20, -20),
                chart_kind,
                document.analysis,
                accent,
                low_ink,
            )
            y += 450

        body_bottom = cls.PAGE_HEIGHT - cls.MARGIN - cls.FOOTER_HEIGHT
        if bool(spec.get("show_signatures")):
            signature_height = 170
            body_bottom -= signature_height
        body_rect = QRectF(
            content_left,
            y,
            content_width,
            max(80, body_bottom - y),
        )
        cls._draw_rich_text(
            painter,
            body_rect,
            cls._paragraph_html(str(spec.get("body") or "No narrative was recorded.")),
            point_size=9.3,
        )

        if bool(spec.get("show_signatures")):
            cls._draw_signatures(
                painter,
                QRectF(content_left, body_bottom + 16, content_width, 145),
                document,
                ink,
            )

        footer_top = cls.PAGE_HEIGHT - cls.FOOTER_HEIGHT
        painter.setPen(QPen(QColor("#BCCBD3"), 1))
        painter.drawLine(content_left, footer_top, content_left + content_width, footer_top)
        painter.setFont(QFont("Arial", 7.5))
        painter.setPen(QColor("#52616B"))
        painter.drawText(
            QRectF(content_left, footer_top + 10, content_width * 0.74, 45),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
            "Valid only with Dashboard Printout "
            f"{document.dashboard_control_number}",
        )
        painter.setFont(QFont("Arial", 8, QFont.Weight.Bold))
        painter.drawText(
            QRectF(
                content_left + content_width * 0.74,
                footer_top + 10,
                content_width * 0.26,
                34,
            ),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop,
            f"{document.dashboard_control_number} · Page {page_number} of {page_count}",
        )
        painter.end()
        return image

    @classmethod
    def _draw_chart(
        cls,
        painter: QPainter,
        rect: QRectF,
        kind: str,
        analysis: Mapping[str, Any],
        accent: QColor,
        low_ink: bool,
    ) -> None:
        if kind == "overview":
            overview = cls._mapping(analysis.get("overview"))
            metrics = [
                ("Responses", overview.get("total_responses", 0), "count"),
                ("Positive rate", overview.get("positive_rate"), "percent"),
                ("Average", overview.get("average_rating"), "rating"),
                ("Completion", overview.get("completion_rate"), "percent"),
            ]
            cls._draw_metric_cards(painter, rect, metrics, accent, low_ink)
            return
        if kind == "dimensions":
            rows = [
                (
                    f"{row.get('code') or ''} {row.get('dimension') or row.get('name') or ''}".strip(),
                    row.get("positive_rate"),
                )
                for row in cls._list_of_mappings(analysis.get("dimensions"))
            ]
            cls._draw_bar_chart(painter, rect, rows, accent)
            return
        if kind == "trend":
            rows = [
                (str(row.get("label") or row.get("period") or ""), row.get("positive_rate"))
                for row in cls._list_of_mappings(analysis.get("trend"))
            ]
            cls._draw_line_chart(painter, rect, rows, accent)
            return
        if kind == "citizen_charter":
            cc = cls._mapping(analysis.get("citizen_charter"))
            rows = [
                ("Awareness", cc.get("awareness_rate")),
                ("Saw the Charter", cc.get("saw_charter_rate")),
                ("Easy to see", cc.get("easy_to_see_rate")),
                ("Helpful", cc.get("helpfulness_rate")),
            ]
            cls._draw_bar_chart(painter, rect, rows, accent)
            return
        if kind == "services":
            rows = [
                (str(row.get("service") or "Unspecified"), row.get("positive_rate"))
                for row in cls._list_of_mappings(analysis.get("services"))[:8]
            ]
            cls._draw_bar_chart(painter, rect, rows, accent)
            return
        if kind == "demographics":
            demographics = cls._mapping(analysis.get("demographics"))
            selected = cls._list_of_mappings(demographics.get("client_type"))
            rows = [
                (str(row.get("label") or "Unspecified"), row.get("percent"))
                for row in selected[:8]
            ]
            cls._draw_bar_chart(painter, rect, rows, accent, label="Response share")

    @staticmethod
    def _draw_metric_cards(
        painter: QPainter,
        rect: QRectF,
        metrics: Sequence[tuple[str, Any, str]],
        accent: QColor,
        low_ink: bool,
    ) -> None:
        gap = 12.0
        width = (rect.width() - gap * (len(metrics) - 1)) / max(1, len(metrics))
        for index, (label, value, value_type) in enumerate(metrics):
            card = QRectF(rect.x() + index * (width + gap), rect.y() + 70, width, 220)
            painter.fillRect(card, QColor("#EAF4F3" if not low_ink else "#F7F9F9"))
            painter.setPen(QPen(QColor("#C3D7D6"), 1))
            painter.drawRoundedRect(card, 10, 10)
            painter.setPen(QColor("#52616B"))
            painter.setFont(QFont("Arial", 8, QFont.Weight.Bold))
            painter.drawText(
                card.adjusted(10, 25, -10, -120),
                Qt.AlignmentFlag.AlignCenter,
                label.upper(),
            )
            if value in (None, ""):
                text = "No data"
            elif value_type == "percent":
                text = f"{float(value):.2f}%"
            elif value_type == "rating":
                text = f"{float(value):.2f}/5"
            else:
                text = str(value)
            painter.setPen(accent)
            painter.setFont(QFont("Arial", 20, QFont.Weight.Bold))
            painter.drawText(
                card.adjusted(8, 85, -8, -45),
                Qt.AlignmentFlag.AlignCenter,
                text,
            )

    @staticmethod
    def _draw_bar_chart(
        painter: QPainter,
        rect: QRectF,
        rows: Sequence[tuple[str, Any]],
        accent: QColor,
        *,
        label: str = "Positive-response rate",
    ) -> None:
        painter.setPen(QColor("#52616B"))
        painter.setFont(QFont("Arial", 8, QFont.Weight.Bold))
        painter.drawText(rect.adjusted(0, 0, 0, -rect.height() + 22), label)
        if not rows:
            painter.setFont(QFont("Arial", 10))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "No chart data")
            return
        chart = rect.adjusted(0, 32, 0, 0)
        row_height = chart.height() / max(1, len(rows))
        label_width = min(320.0, chart.width() * 0.34)
        for index, (name, raw_value) in enumerate(rows):
            y = chart.y() + index * row_height
            value = max(0.0, min(100.0, float(raw_value or 0.0)))
            painter.setPen(QColor("#183042"))
            painter.setFont(QFont("Arial", 7.5))
            painter.drawText(
                QRectF(chart.x(), y, label_width - 10, row_height),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                NarrativeReportRenderer._elide(name, 38),
            )
            bar = QRectF(
                chart.x() + label_width,
                y + row_height * 0.26,
                chart.width() - label_width - 58,
                row_height * 0.48,
            )
            painter.fillRect(bar, QColor("#E8EEF1"))
            painter.fillRect(
                QRectF(bar.x(), bar.y(), bar.width() * value / 100.0, bar.height()),
                accent,
            )
            painter.drawText(
                QRectF(bar.right() + 8, y, 50, row_height),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                f"{value:.1f}%",
            )

    @staticmethod
    def _draw_line_chart(
        painter: QPainter,
        rect: QRectF,
        rows: Sequence[tuple[str, Any]],
        accent: QColor,
    ) -> None:
        if not rows:
            painter.setFont(QFont("Arial", 10))
            painter.setPen(QColor("#52616B"))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "No trend data")
            return
        chart = rect.adjusted(64, 28, -30, -56)
        painter.setPen(QPen(QColor("#D4E0E6"), 1))
        for step in range(6):
            y = chart.bottom() - chart.height() * step / 5.0
            painter.drawLine(chart.left(), y, chart.right(), y)
            painter.setPen(QColor("#52616B"))
            painter.setFont(QFont("Arial", 7))
            painter.drawText(
                QRectF(rect.x(), y - 10, 54, 20),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                f"{step * 20}%",
            )
            painter.setPen(QPen(QColor("#D4E0E6"), 1))
        points = []
        for index, (name, raw_value) in enumerate(rows):
            x = (
                chart.center().x()
                if len(rows) == 1
                else chart.left() + chart.width() * index / (len(rows) - 1)
            )
            value = max(0.0, min(100.0, float(raw_value or 0.0)))
            y = chart.bottom() - chart.height() * value / 100.0
            points.append((x, y, name, value))
        path = QPainterPath()
        path.moveTo(points[0][0], points[0][1])
        for x, y, _, _ in points[1:]:
            path.lineTo(x, y)
        painter.setPen(QPen(accent, 4))
        painter.drawPath(path)
        for x, y, name, value in points:
            painter.setBrush(accent)
            painter.setPen(QPen(QColor("#FFFFFF"), 2))
            painter.drawEllipse(QRectF(x - 6, y - 6, 12, 12))
            painter.setPen(QColor("#183042"))
            painter.setFont(QFont("Arial", 7.5))
            painter.drawText(
                QRectF(x - 55, chart.bottom() + 9, 110, 25),
                Qt.AlignmentFlag.AlignCenter,
                NarrativeReportRenderer._elide(name, 16),
            )
            painter.drawText(
                QRectF(x - 40, y - 31, 80, 20),
                Qt.AlignmentFlag.AlignCenter,
                f"{value:.1f}%",
            )

    @staticmethod
    def _draw_signatures(
        painter: QPainter,
        rect: QRectF,
        document: NarrativePrintDocument,
        ink: QColor,
    ) -> None:
        half = rect.width() / 2.0
        painter.setPen(QPen(ink, 1))
        for index, role in enumerate(("CSM Coordinator", "School Head / Head of Office")):
            left = rect.x() + index * half + 32
            width = half - 64
            line_y = rect.y() + 70
            painter.drawLine(left, line_y, left + width, line_y)
            painter.setFont(QFont("Arial", 8, QFont.Weight.Bold))
            painter.drawText(
                QRectF(left, line_y + 6, width, 25),
                Qt.AlignmentFlag.AlignCenter,
                role,
            )
            painter.setFont(QFont("Arial", 7))
            painter.drawText(
                QRectF(left, line_y + 31, width, 22),
                Qt.AlignmentFlag.AlignCenter,
                "Signature over printed name / Date",
            )
        if document.approved_by:
            painter.setFont(QFont("Arial", 7.5))
            painter.drawText(
                QRectF(rect.x(), rect.bottom() - 24, rect.width(), 20),
                Qt.AlignmentFlag.AlignCenter,
                f"System approval recorded for {document.approved_by} on "
                f"{NarrativeReportRenderer._timestamp(document.approved_at)}",
            )

    @staticmethod
    def _draw_rich_text(
        painter: QPainter,
        rect: QRectF,
        html: str,
        *,
        point_size: float,
    ) -> None:
        document = QTextDocument()
        document.setDefaultFont(QFont("Arial", point_size))
        document.setDocumentMargin(0)
        document.setHtml(html)
        document.setTextWidth(rect.width())
        painter.save()
        painter.translate(rect.topLeft())
        document.drawContents(painter, QRectF(0, 0, rect.width(), rect.height()))
        painter.restore()

    @staticmethod
    def _paragraph_html(text: str) -> str:
        paragraphs = [part.strip() for part in str(text or "").split("\n") if part.strip()]
        return "".join(f"<p style='line-height:135%'>{NarrativeReportRenderer._escape(part)}</p>" for part in paragraphs)

    @staticmethod
    def _body(*values: Any) -> str:
        return "\n\n".join(
            str(value).strip() for value in values if str(value or "").strip()
        )

    @staticmethod
    def _list_text(title: str, value: Any) -> str:
        if not isinstance(value, (list, tuple)) or not value:
            return f"{title}: None recorded."
        return title + ":\n" + "\n".join(f"• {str(item).strip()}" for item in value)

    @staticmethod
    def _section_text(document: Mapping[str, Any], key: str) -> str:
        sections = document.get("sections")
        if not isinstance(sections, list):
            return "No narrative was recorded."
        for section in sections:
            if not isinstance(section, Mapping) or section.get("key") != key:
                continue
            paragraphs = section.get("paragraphs")
            bullets = section.get("bullets")
            values = paragraphs if isinstance(paragraphs, list) and paragraphs else bullets
            if not isinstance(values, list) or not values:
                return "No narrative was recorded."
            if values is bullets:
                return "\n".join(
                    f"• {str(item).strip()}" for item in values if str(item).strip()
                )
            return "\n\n".join(
                str(item).strip() for item in values if str(item).strip()
            )
        return "No narrative was recorded."

    @staticmethod
    def _mapping(value: Any) -> Mapping[str, Any]:
        return value if isinstance(value, Mapping) else {}

    @staticmethod
    def _list_of_mappings(value: Any) -> list[Mapping[str, Any]]:
        return [item for item in value if isinstance(item, Mapping)] if isinstance(value, list) else []

    @staticmethod
    def _timestamp(value: str) -> str:
        text = str(value or "").strip()
        if not text:
            return "Not recorded"
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return text
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone()
        return parsed.strftime("%d %B %Y, %I:%M %p")

    @staticmethod
    def _escape(value: str) -> str:
        return (
            str(value)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )

    @staticmethod
    def _elide(value: str, limit: int) -> str:
        text = " ".join(str(value or "").split())
        return text if len(text) <= limit else text[: max(1, limit - 1)].rstrip() + "…"
