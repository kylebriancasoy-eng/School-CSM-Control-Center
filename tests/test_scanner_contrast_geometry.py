from __future__ import annotations

import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from PIL import Image, ImageChops, ImageDraw

from school_csm_control_center.mrs_printing import render_official_mrs_form
from school_csm_control_center.web_server import scanner_engine
from school_csm_control_center.web_server.scanner_engine import ScannerProcessingOptions, process_mrs_image


ROOT = Path(__file__).parents[1]
CONTROL = "CSM-MRS-123627-2026-07-0001"


def contrast_test_sheet() -> Image.Image:
    image = render_official_mrs_form(ROOT, CONTROL, school_name="Calapi Elementary School")
    # Add neutral gray content inside the page so post-perspective contrast has
    # a measurable effect without changing the corner-marker source image.
    draw = ImageDraw.Draw(image)
    draw.rectangle((500, 250, 740, 290), fill=(165, 165, 165))
    return image


class ScannerContrastGeometryTests(unittest.TestCase):
    def test_contrast_is_applied_after_corner_detection(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "sheet.png"
            contrast_test_sheet().save(source)
            detector_inputs: list[str] = []
            original_detector = scanner_engine._detect_aruco_markers_robust

            def capture_detector_input(gray, cv2, np):
                detector_inputs.append(hashlib.sha256(gray.tobytes()).hexdigest())
                return original_detector(gray, cv2, np)

            with patch.object(scanner_engine, "_detect_aruco_markers_robust", side_effect=capture_detector_input):
                normal = process_mrs_image(
                    source,
                    root / "normal",
                    ROOT,
                    options=ScannerProcessingOptions(contrast=1.0),
                )
                enhanced = process_mrs_image(
                    source,
                    root / "enhanced",
                    ROOT,
                    options=ScannerProcessingOptions(contrast=2.0),
                )

            self.assertEqual(len(detector_inputs), 2)
            self.assertEqual(detector_inputs[0], detector_inputs[1])
            self.assertEqual(normal["source_corners"], enhanced["source_corners"])
            self.assertEqual(normal["marker_ids"], [10, 11, 12, 13])
            self.assertEqual(enhanced["marker_ids"], [10, 11, 12, 13])
            self.assertEqual(enhanced["marker_detection_source"], "original_geometry")
            self.assertEqual(enhanced["contrast_stage"], "post_perspective_recognition")

            with Image.open(root / "normal" / "canonical.jpg") as first, Image.open(
                root / "enhanced" / "canonical.jpg"
            ) as second:
                difference = ImageChops.difference(first.convert("RGB"), second.convert("RGB"))
                self.assertIsNotNone(difference.getbbox())


if __name__ == "__main__":
    unittest.main()
