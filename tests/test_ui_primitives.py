from __future__ import annotations

import math
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

from school_csm_control_center.ui import theme
from school_csm_control_center.ui.charts import (
    BarDatum,
    DimensionBarChart,
    DonutDatum,
    ResponseDonutChart,
    TrendChart,
    TrendDatum,
)
from school_csm_control_center.ui.icons import ICON_NAMES, action_icon
from school_csm_control_center.ui.widgets import MetricCard, SectionCard, TooltipIconButton


class UiPrimitiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyleSheet(theme.ROOT_STYLESHEET)

    def setUp(self) -> None:
        self.host = QWidget()
        self.host.resize(900, 680)
        self.layout = QVBoxLayout(self.host)

    def tearDown(self) -> None:
        self.host.close()
        self.host.deleteLater()
        self.app.processEvents()

    def test_every_supported_action_produces_a_non_null_pixmap(self) -> None:
        required = {
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
            "view",
            "lock",
            "print",
            "zoom_in",
            "zoom_out",
            "fit_page",
            "fit_width",
        }
        self.assertTrue(required.issubset(ICON_NAMES))
        for name in sorted(ICON_NAMES):
            with self.subTest(name=name):
                icon = action_icon(name)
                self.assertFalse(icon.isNull())
                self.assertFalse(icon.pixmap(32, 32).isNull())

    def test_unknown_action_is_rejected_instead_of_showing_wrong_icon(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported CSM action icon"):
            action_icon("not-a-real-action")

    def test_icon_button_contract_and_dynamic_role(self) -> None:
        button = TooltipIconButton("add", "Add Survey Result", parent=self.host)
        self.assertEqual(button.text(), "")
        self.assertFalse(button.icon().isNull())
        self.assertEqual(button.toolTip(), "Add Survey Result")
        self.assertEqual(button.statusTip(), "Add Survey Result")
        self.assertEqual(button.accessibleName(), "Add Survey Result")
        self.assertIn("Icon-only", button.accessibleDescription())

        button.set_role("danger")
        self.assertEqual(button.property("role"), "danger")
        button.setToolTip("Remove Survey Result")
        self.assertEqual(button.statusTip(), "Remove Survey Result")
        self.assertEqual(button.accessibleName(), "Remove Survey Result")
        self.assertIn("Remove Survey Result", button.accessibleDescription())

    def test_icon_button_requires_a_tooltip(self) -> None:
        with self.assertRaisesRegex(ValueError, "non-empty tooltip"):
            TooltipIconButton("add", "")

    def test_metric_and_section_cards_expose_live_update_apis(self) -> None:
        section = SectionCard("Overview", "Live survey summary", parent=self.host)
        metric = MetricCard("Responses", 0, "Ready", parent=section)
        section.add_widget(metric)
        section.add_header_action(TooltipIconButton("reset", "Reset filters"))
        metric.set_metric(12, "Updated now")

        self.assertEqual(metric.value_label.text(), "12")
        self.assertEqual(metric.detail_label.text(), "Updated now")
        self.assertEqual(section.content_layout.count(), 1)
        self.assertEqual(section.header_actions_layout.count(), 1)

    def test_chart_data_is_normalized_without_nan_or_negative_counts(self) -> None:
        bars = DimensionBarChart(parent=self.host)
        bars.set_data(
            [
                {"dimension": "Responsiveness", "score": "87.5%"},
                ("Reliability", 91),
                ("Reliability", 92),
                ("Negative", -3),
                ("Invalid", math.nan),
            ]
        )
        self.assertEqual(
            bars.normalized_data,
            (
                BarDatum("Responsiveness", 87.5),
                BarDatum("Reliability", 92.0),
                BarDatum("Negative", 0.0),
            ),
        )

        donut = ResponseDonutChart(parent=self.host)
        donut.set_data(
            [
                ("Strongly Agree", 8),
                ("Agree", "4"),
                ("Strongly Agree", 2),
                ("Disagree", -7),
                ("Invalid", float("inf")),
            ],
            colors={"Strongly Agree": "#2BD9C5"},
        )
        self.assertEqual(donut.total, 14.0)
        self.assertEqual(donut.normalized_data[0].value, 10.0)
        self.assertEqual(donut.normalized_data[2].value, 0.0)
        for item in donut.normalized_data:
            self.assertTrue(QColor(item.color).isValid())

        trend = TrendChart(parent=self.host)
        trend.set_data(
            [
                {"period": "Jan", "percentage": "81%"},
                ("Feb", 85.5),
                ("Bad", None),
            ]
        )
        self.assertEqual(
            trend.normalized_data,
            (TrendDatum("Jan", 81.0), TrendDatum("Feb", 85.5)),
        )
        self.assertEqual(trend.display_mode, "trend")

    def test_single_period_trend_is_an_explained_baseline(self) -> None:
        trend = TrendChart(parent=self.host)
        self.layout.addWidget(trend)
        trend.set_data([("Jul 2025", 93.3)])
        self.host.show()
        self.app.processEvents()

        self.assertEqual(trend.display_mode, "baseline")
        self.assertIn("Only one reporting period", trend.toolTip())
        self.assertIn("add another period to show a trend line", trend.accessibleDescription())
        self._assert_widget_renders(trend)

    def test_donut_uses_left_details_and_right_chart_with_narrow_fallback(self) -> None:
        chart = ResponseDonutChart(parent=self.host)
        desktop_content = QRectF(0, 0, 365, 229)
        desktop = chart._layout_for_content(desktop_content)

        self.assertEqual(desktop.mode, "horizontal")
        self.assertEqual(desktop.legend_columns, 1)
        self.assertLess(desktop.legend_rect.right(), desktop.donut_area.left())
        self.assertLess(desktop.legend_rect.center().x(), desktop_content.center().x())
        self.assertGreater(desktop.donut_area.center().x(), desktop_content.center().x())
        self.assertTrue(desktop_content.contains(desktop.legend_rect))
        self.assertTrue(desktop_content.contains(desktop.donut_area))
        self.assertGreaterEqual(
            desktop.diameter,
            min(desktop.donut_area.width(), desktop.donut_area.height()) * 0.75,
        )

        narrow_content = QRectF(0, 0, 260, 250)
        narrow = chart._layout_for_content(narrow_content)
        self.assertEqual(narrow.mode, "stacked")
        self.assertEqual(narrow.legend_columns, 2)
        self.assertLess(narrow.donut_area.bottom(), narrow.legend_rect.top())
        self.assertTrue(narrow_content.contains(narrow.donut_area))
        self.assertTrue(narrow_content.contains(narrow.legend_rect))
        self.assertGreater(narrow.diameter, 0)

    def test_charts_paint_both_empty_and_populated_states_offscreen(self) -> None:
        charts = [
            DimensionBarChart(parent=self.host),
            ResponseDonutChart(parent=self.host),
            TrendChart(parent=self.host),
        ]
        for chart in charts:
            self.layout.addWidget(chart)
        self.host.show()
        self.app.processEvents()

        for chart in charts:
            with self.subTest(chart=type(chart).__name__, state="empty"):
                self._assert_widget_renders(chart)

        charts[0].set_data({"Responsiveness": 88, "Reliability": 91})
        charts[1].set_data({"Strongly Agree": 12, "Agree": 4})
        charts[2].set_data({"Jan": 78, "Feb": 84, "Mar": 89})
        self.app.processEvents()
        for chart in charts:
            with self.subTest(chart=type(chart).__name__, state="populated"):
                self._assert_widget_renders(chart)

    def _assert_widget_renders(self, widget: QWidget) -> None:
        widget.resize(max(420, widget.width()), max(250, widget.height()))
        pixmap = QPixmap(widget.size())
        pixmap.fill(Qt.GlobalColor.transparent)
        widget.render(pixmap)
        self.assertFalse(pixmap.isNull())
        self.assertGreater(pixmap.width(), 0)
        self.assertGreater(pixmap.height(), 0)


if __name__ == "__main__":
    unittest.main()
