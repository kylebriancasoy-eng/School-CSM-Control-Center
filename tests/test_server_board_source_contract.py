from __future__ import annotations

from pathlib import Path
import unittest


class ServerBoardSourceContractTests(unittest.TestCase):
    def test_server_board_uses_icon_only_buttons_and_compact_qr_tiles(self) -> None:
        root = Path(__file__).resolve().parents[1]
        source = (root / "school_csm_control_center" / "ui" / "server_board.py").read_text(encoding="utf-8")
        self.assertNotIn("QPushButton(", source)
        self.assertIn("TooltipIconButton", source)
        self.assertIn("server_qr_tile", source)
        self.assertIn("setMaximumWidth(1080)", source)
        self.assertIn("button_size=36", source)

    def test_named_qr_is_disabled_when_local_dns_is_not_verified(self) -> None:
        root = Path(__file__).resolve().parents[1]
        source = (root / "school_csm_control_center" / "ui" / "server_board.py").read_text(encoding="utf-8")
        self.assertIn('named_enabled = bool(named_access)', source)
        self.assertIn('button.setEnabled(named_enabled)', source)
        self.assertIn('"Local DNS not verified"', source)
        self.assertIn('"Primary verified manual address"', source)


if __name__ == "__main__":
    unittest.main()
