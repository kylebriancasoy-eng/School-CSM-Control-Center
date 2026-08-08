from __future__ import annotations

import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class StartupStylesheetSafetyTests(unittest.TestCase):
    def test_title_bar_fstring_has_no_accidental_background_name(self) -> None:
        path = ROOT / "school_csm_control_center" / "ui" / "title_bar.py"
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        accidental = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Name)
            and isinstance(node.ctx, ast.Load)
            and node.id == "background"
        ]
        self.assertEqual(accidental, [], f"Unescaped stylesheet braces at lines: {accidental}")

    def test_mosslab_logo_selector_uses_escaped_fstring_braces(self) -> None:
        source = (ROOT / "school_csm_control_center" / "ui" / "title_bar.py").read_text(encoding="utf-8")
        self.assertIn("QLabel#title_bar_mosslab_logo {{", source)
        self.assertIn("padding: 1px;\n            }}", source)


if __name__ == "__main__":
    unittest.main()
