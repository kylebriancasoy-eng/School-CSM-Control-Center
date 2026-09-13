from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRectF
from PySide6.QtWidgets import QApplication, QDialog, QFrame, QProgressBar

from school_csm_control_center.ui.dashboard_board import DashboardBoard, ProfileBreakdownRow
from tests.test_analysis_service import sample_records


class CompactDashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.board = DashboardBoard()
        self.board.resize(1200, 700)
        self.board.show()
        self.board.refresh(sample_records())
        self.app.processEvents()

    def tearDown(self) -> None:
        self.board.close()
        self.board.deleteLater()
        self.app.processEvents()

    @staticmethod
    def _position(board: DashboardBoard, widget) -> tuple[int, int, int, int]:
        index = board.analysis_grid.indexOf(widget)
        return board.analysis_grid.getItemPosition(index)

    def test_compact_wide_layout_reuses_all_existing_sections(self) -> None:
        self.board._reflow(force=True)
        self.app.processEvents()
        self.assertEqual(self.board._layout_mode, "wide")
        self.assertEqual(self.board.analysis_grid.count(), 9)
        self.assertEqual(self._position(self.board, self.board.trend_chart), (0, 0, 1, 2))
        self.assertEqual(self._position(self.board, self.board.donut_chart), (0, 2, 1, 1))
        self.assertEqual(self._position(self.board, self.board.dimension_chart), (1, 0, 1, 2))
        self.assertEqual(self._position(self.board, self.board.cc_card), (1, 2, 1, 1))
        self.assertEqual(self._position(self.board, self.board.services_card), (2, 0, 1, 2))
        self.assertEqual(self._position(self.board, self.board.demographics_card), (2, 2, 1, 1))
        self.assertEqual(self._position(self.board, self.board.insights_card), (3, 0, 1, 1))
        self.assertEqual(self._position(self.board, self.board.feedback_card), (3, 1, 1, 1))
        self.assertEqual(self._position(self.board, self.board.recent_card), (3, 2, 1, 1))
        self.assertTrue(all(card.minimumHeight() >= 96 for card in self.board.metric_cards))
        self.assertLessEqual(max(card.height() for card in self.board.metric_cards), 110)
        header = self.board.findChild(QFrame, "dashboard_header")
        self.assertIsNotNone(header)
        self.assertLessEqual(header.height(), 80)
        self.assertLessEqual(self.board.filter_strip.height(), 70)
        self.assertGreaterEqual(self.board.scroll.viewport().height(), 460)
        self.assertLessEqual(self.board.body.sizeHint().height(), 1600)
        self.assertGreaterEqual(self.board.header_subtitle.font().pixelSize(), 11)
        self.assertGreaterEqual(self.board.total_metric.title_label.font().pixelSize(), 11)
        self.assertGreaterEqual(self.board.services_card.title_label.font().pixelSize(), 15)
        self.assertGreaterEqual(self.board.services_table.font().pixelSize(), 11)
        self.assertGreaterEqual(self.board.cc_awareness.label.font().pixelSize(), 11)
        self.assertGreaterEqual(self.board.trend_chart.minimumHeight(), 260)
        self.assertGreaterEqual(self.board.donut_chart.minimumHeight(), 285)
        self.assertGreaterEqual(
            self.board.dimension_chart.minimumHeight(),
            76 + len(self.board._analysis["dimensions"]) * 36,
        )
        donut_content = QRectF(
            0,
            0,
            self.board.donut_chart.width() - 28,
            self.board.donut_chart.height() - 56,
        )
        donut_layout = self.board.donut_chart._layout_for_content(donut_content)
        self.assertEqual(donut_layout.mode, "horizontal")
        self.assertLess(donut_layout.legend_rect.right(), donut_layout.donut_area.left())
        self.assertGreaterEqual(
            donut_layout.diameter,
            min(donut_layout.donut_area.width(), donut_layout.donut_area.height()) * 0.75,
        )
        margins = self.board.layout().contentsMargins()
        self.assertEqual((margins.left(), margins.top()), (14, 12))

    def test_live_scope_and_existing_sections_show_visible_evidence(self) -> None:
        self.assertIn("2 of 2 responses", self.board.filter_summary_label.text())
        self.assertIn("2 filtered responses", self.board.header_subtitle.text())
        self.assertIn("1 onsite", self.board.total_metric.detail_label.text())
        self.assertIn("1 online", self.board.total_metric.detail_label.text())
        self.assertIn("8/13 positive", self.board.overall_metric.detail_label.text())
        self.assertIn("100.00% answered", self.board.overall_metric.detail_label.text())
        self.assertIn("18.46 points to Satisfactory", self.board.band_metric.detail_label.text())
        self.assertIn("1/1 positive", self.board.sqd0_metric.detail_label.text())

        donut = {item.label: item.value for item in self.board.donut_chart.normalized_data}
        self.assertEqual(donut["Unanswered"], 0)
        self.assertEqual(sum(donut.values()), 14)
        self.assertIn("14/14 answered", self.board.donut_chart.context_text)
        self.assertIn("Best", self.board.dimension_chart.context_text)
        self.assertIn("Focus", self.board.dimension_chart.context_text)
        self.assertIn("1/2", self.board.cc_awareness.value.text())
        self.assertIn("1 onsite", self.board.cc_summary.text())
        self.assertIn("1 online", self.board.cc_summary.text())

        profile_rows = self.board.findChildren(ProfileBreakdownRow)
        self.assertEqual(len(profile_rows), 4)
        self.assertTrue(all(row.bar.value() > 0 for row in profile_rows))
        self.assertTrue(all(row.secondary.text().strip() for row in profile_rows))

        self.assertIn("Most selected", self.board.services_summary.text())
        self.assertIn("%", self.board.services_table.item(0, 1).text())
        self.assertIn("average", self.board.services_table.item(0, 0).toolTip())
        self.assertLessEqual(self.board.services_table.height(), 200)
        self.assertIn("of 2 responses include comments", self.board.feedback_summary.text())
        self.assertIn("·", self.board.recent_table.item(0, 3).text())
        self.assertTrue(self.board.trend_chart.toolTip().strip())
        insight_count = len(self.board._analysis["insights"])
        self.assertIn(f"{insight_count} live observation", self.board.insights_summary.text())
        self.assertEqual(
            len(self.board.insights_content.findChildren(QFrame, "insight_row")),
            insight_count,
        )
        self.assertEqual(self.board.findChildren(QDialog), [])

    def test_medium_narrow_reflow_has_no_duplicate_sections(self) -> None:
        self.board.resize(1000, 700)
        self.board._reflow(force=True)
        self.assertEqual(self.board._layout_mode, "medium")
        self.assertEqual(self.board.analysis_grid.count(), 9)
        self.assertEqual(self._position(self.board, self.board.services_card), (3, 0, 1, 2))

        self.board.resize(760, 700)
        self.board._reflow(force=True)
        self.assertEqual(
            self.board._layout_mode,
            "narrow",
            (
                f"actual={self.board.width()} min_hint={self.board.minimumSizeHint().width()} "
                f"analysis_min={self.board.body.minimumSizeHint().width()} "
                f"filters_min={self.board.filter_strip.minimumSizeHint().width()} "
                f"combos={[combo.minimumSizeHint().width() for combo in (self.board.period_combo, self.board.mode_combo, self.board.client_combo, self.board.service_combo)]}"
            ),
        )
        self.assertEqual(self.board.analysis_grid.count(), 9)
        for row, widget in enumerate(self.board.analysis_widgets):
            self.assertEqual(self._position(self.board, widget), (row, 0, 1, 1))

    def test_filter_summary_and_reset_state_follow_active_filters(self) -> None:
        self.assertFalse(self.board.reset_filters_button.isEnabled())
        self.board.mode_combo.setCurrentIndex(self.board.mode_combo.findData("online"))
        self.app.processEvents()
        self.assertTrue(self.board.reset_filters_button.isEnabled())
        self.assertIn("1 of 2 responses", self.board.filter_summary_label.text())
        self.assertIn("1 active filter", self.board.filter_summary_label.text())

        self.board.reset_filters()
        self.board.period_combo.setCurrentIndex(self.board.period_combo.findData("month"))
        self.app.processEvents()
        self.assertIn("1 active filter", self.board.filter_summary_label.text())

    def test_trend_tooltip_describes_the_latest_plotted_period(self) -> None:
        records = sample_records()
        records.append(
            {
                "id": "no-score-august",
                "mode": "online",
                "survey_date": "2026-08-01",
                "meta": {"service_availed": "Enrollment"},
                "sqd": {f"sqd{index}": 0 for index in range(1, 9)},
            }
        )
        self.board.refresh(records)
        self.app.processEvents()

        self.assertIn("Jul 2026", self.board.trend_chart.toolTip())
        self.assertNotIn("Aug 2026", self.board.trend_chart.toolTip())

    def test_one_scored_period_is_labeled_as_a_baseline(self) -> None:
        records = sample_records()
        records[1]["survey_date"] = "2026-06-20"
        self.board.refresh(records)
        self.app.processEvents()

        self.assertEqual(self.board.trend_chart.display_mode, "baseline")
        self.assertIn("Baseline Jun 2026", self.board.trend_chart.toolTip())
        self.assertIn("only scored period", self.board.trend_chart.toolTip())
        self.assertIn("requires at least two scored periods", self.board.trend_chart.toolTip())


if __name__ == "__main__":
    unittest.main()
