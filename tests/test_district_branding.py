"""The supplied seal is preserved and displayed without changing school branding."""

import hashlib
import os
from pathlib import Path
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication, QLabel

from district_management_server.branding import district_header, district_icon


class DistrictBrandingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.root = Path(__file__).resolve().parents[1]

    def test_original_seal_is_preserved_and_icon_loads(self):
        source = self.root / "registration_service/static/district-seal.png"
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),
            "0f208b430acab5b8f17b7e4d91781b79069152e76128689b9234be30c94f5e82")
        self.assertFalse(district_icon(self.root).isNull())

    def test_header_displays_seal_with_accessible_label(self):
        header = district_header(self.root, "District status", "Infrastructure only")
        self.addCleanup(header.deleteLater)
        seal = header.findChild(QLabel, "districtSeal")
        self.assertIsNotNone(seal)
        self.assertEqual(seal.accessibleName(), "Official Motiong District seal")
        self.assertFalse(seal.pixmap().isNull())
        self.assertLessEqual(seal.pixmap().width(), 96)
        self.assertLessEqual(seal.pixmap().height(), 96)


if __name__ == "__main__":
    unittest.main()
