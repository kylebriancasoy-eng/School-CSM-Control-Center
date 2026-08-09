from __future__ import annotations

import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
OVERLAY = ROOT / "school_csm_control_center" / "ui" / "narrative_report_overlay.py"


class NarrativeReportOverlayContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = OVERLAY.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def test_public_integration_api_is_stable(self) -> None:
        overlay = next(
            node
            for node in self.tree.body
            if isinstance(node, ast.ClassDef) and node.name == "NarrativeReportOverlay"
        )
        methods = {node.name for node in overlay.body if isinstance(node, ast.FunctionDef)}
        self.assertIn("open_for_print_record", methods)
        self.assertIn("close_overlay", methods)
        for signal in ("closed", "report_changed", "print_completed", "print_failed"):
            self.assertRegex(self.source, rf"\n    {signal} = Signal\(")

    def test_open_path_verifies_snapshot_and_report(self) -> None:
        self.assertIn("self.snapshot_store.load(reference)", self.source)
        self.assertIn("self.report_store.verify", self.source)
        self.assertIn("validate_narrative_document", self.source)
        self.assertIn("actual_control != expected_control", self.source)

    def test_local_generation_is_automatic_and_reusable(self) -> None:
        self.assertIn("get_for_snapshot(snapshot_id)", self.source)
        self.assertIn("self.local_generator.generate(snapshot)", self.source)
        self.assertIn('generation_method="local"', self.source)

    def test_openai_is_explicit_off_thread_and_uses_credential_store(self) -> None:
        self.assertIn("class _OpenAIGenerationWorker(QRunnable)", self.source)
        self.assertIn("self._thread_pool.start(worker)", self.source)
        self.assertIn("self.credential_store.load()", self.source)
        self.assertIn("self.credential_store.save(key)", self.source)
        self.assertIn("self.credential_store.delete()", self.source)
        self.assertIn("https://platform.openai.com/api-keys", self.source)
        self.assertNotIn("OpenAI password input", self.source)

    def test_revision_approval_and_print_audits_use_store(self) -> None:
        self.assertIn("self.report_store.append_revision", self.source)
        self.assertIn("self.report_store.approve", self.source)
        self.assertIn("self.report_store.record_print", self.source)
        print_pos = self.source.index("printed = int(self._print_executor")
        audit_pos = self.source.index("report = self.report_store.record_print", print_pos)
        self.assertLess(print_pos, audit_pos)

    def test_required_print_content_is_delegated_to_renderer(self) -> None:
        self.assertIn("NarrativeReportRenderer.render_pages", self.source)
        self.assertIn("NarrativeReportRenderer.paint", self.source)
        self.assertIn("NarrativePrintDocument", self.source)


if __name__ == "__main__":
    unittest.main()
