from __future__ import annotations

import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "school_csm_control_center" / "ui" / "main_window.py"
OVERLAY = ROOT / "school_csm_control_center" / "ui" / "narrative_report_overlay.py"
RENDERER = ROOT / "school_csm_control_center" / "ui" / "narrative_report_printing.py"
PRINT_OVERLAY = ROOT / "school_csm_control_center" / "ui" / "dashboard_print_overlay.py"


class NarrativeUiIntegrationContractTests(unittest.TestCase):
    def test_main_window_connects_both_history_actions_to_verified_stores(self) -> None:
        source = MAIN.read_text(encoding="utf-8")
        ast.parse(source)
        self.assertIn("self.snapshot_store = DashboardSnapshotStore", source)
        self.assertIn("self.narrative_store = NarrativeReportStore", source)
        self.assertIn("self.history.narrative_requested.connect", source)
        self.assertIn("self.history.reprint_requested.connect", source)
        self.assertIn("decode_dashboard_snapshot(self.snapshot_store, reference)", source)
        self.assertIn("self.print_overlay.open_reprint(source, snapshot)", source)
        self.assertIn("self.narrative_overlay.open_for_print_record", source)

    def test_successful_print_freezes_snapshot_before_submitting_audit(self) -> None:
        source = PRINT_OVERLAY.read_text(encoding="utf-8")
        save = source.index("snapshot_reference = self.snapshot_store.save")
        submit = source.index("self.print_store.mark_submitting", save)
        execute = source.index("self._print_executor", submit)
        self.assertLess(save, submit)
        self.assertLess(submit, execute)
        self.assertIn('"dashboard_snapshot": snapshot_reference', source)

    def test_renderer_consumes_validated_fixed_sections(self) -> None:
        overlay = OVERLAY.read_text(encoding="utf-8")
        renderer = RENDERER.read_text(encoding="utf-8")
        ast.parse(overlay)
        ast.parse(renderer)
        self.assertIn("narrative=deepcopy(self._document)", overlay)
        for key in (
            "executive_summary",
            "scope_and_methodology",
            "key_findings",
            "strengths",
            "areas_for_improvement",
            "recommended_actions",
            "conclusion",
        ):
            self.assertIn(f'cls._section_text(narrative, "{key}")', renderer)

    def test_required_validity_and_control_number_repeat_contract(self) -> None:
        renderer = RENDERER.read_text(encoding="utf-8")
        required = (
            "This Narrative Report is valid only when attached to or accompanied by the "
            "successful Dashboard Printout bearing Control Number "
            "{DASHBOARD_CONTROL_NUMBER}. Without the referenced Dashboard Printout, this "
            "Narrative Report shall be considered invalid."
        )
        compact = "".join(
            line.strip().strip('"')
            for line in renderer.splitlines()[20:26]
        )
        self.assertIn(required, compact)
        self.assertIn("document.dashboard_control_number", renderer)
        self.assertIn("Page {page_number} of {page_count}", renderer)
        self.assertIn('("CSM Coordinator", "School Head / Head of Office")', renderer)


if __name__ == "__main__":
    unittest.main()
