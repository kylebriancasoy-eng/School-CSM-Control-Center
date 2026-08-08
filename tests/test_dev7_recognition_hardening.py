from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter

from school_csm_control_center.mrs_printing import BARCODE_PRINT_REGION_PX, render_official_mrs_form
from school_csm_control_center.web_server.scanner_engine import (
    _normalize_control_number_ocr,
    _tesseract_executable,
    process_mrs_image,
)


ROOT = Path(__file__).parents[1]
CONTROL = "CSM-MRS-123627-2026-07-0001"
MAP_PATH = ROOT / "assets" / "mrs_v0.4" / "CSM_MRS_Coordinate_Map_v0.4.json"


class Dev7RecognitionHardeningTests(unittest.TestCase):
    def test_control_number_ocr_normalization(self) -> None:
        self.assertEqual(
            _normalize_control_number_ocr(" CSM MRS 123627 2O26 O7 OOO1 "),
            CONTROL,
        )

    def test_barcode_survives_blur_low_contrast_and_jpeg(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            image = render_official_mrs_form(ROOT, CONTROL, school_name="Calapi Elementary School")
            image = image.resize((1488, 2105), Image.Resampling.LANCZOS)
            image = image.filter(ImageFilter.GaussianBlur(0.7))
            image = ImageEnhance.Contrast(image).enhance(0.72)
            source = root / "degraded.jpg"
            image.save(source, "JPEG", quality=48)
            result = process_mrs_image(source, root / "out", ROOT)
        self.assertEqual(result["barcode"]["value"], CONTROL)
        self.assertEqual(result["barcode"]["status"], "Normal")
        self.assertGreaterEqual(result["barcode"]["variants_checked"], 8)

    def test_human_readable_control_number_recovers_destroyed_barcode_when_ocr_available(self) -> None:
        if not _tesseract_executable():
            self.skipTest("Optional printed-text OCR runtime is unavailable.")
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            image = render_official_mrs_form(ROOT, CONTROL, school_name="Calapi Elementary School")
            x, y, width, height = BARCODE_PRINT_REGION_PX
            # Destroy the bars while preserving the human-readable value below.
            from PIL import ImageDraw
            draw = ImageDraw.Draw(image)
            draw.rectangle((x - 20, y - 15, x + width + 20, y + height - 10), fill="white")
            source = root / "text_fallback.png"
            image.save(source)
            result = process_mrs_image(source, root / "out", ROOT)
            self.assertTrue((root / "out" / "control_number.jpg").is_file())
        self.assertEqual(result["barcode"]["value"], CONTROL)
        self.assertEqual(result["barcode"]["value_source"], "human_readable_ocr")

    def test_handwritten_date_model_and_date_constraints_recover_boxed_digits(self) -> None:
        coordinate_map = json.loads(MAP_PATH.read_text(encoding="utf-8"))["templates"]["en"]
        model = np.load(ROOT / "assets" / "recognition" / "handwritten_digits_v1.npz")
        images, labels = model["images"], model["labels"]
        sequence = "07162026"
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            page = render_official_mrs_form(ROOT, CONTROL, school_name="Calapi Elementary School").convert("L")
            for index, (digit, box) in enumerate(zip(sequence, coordinate_map["date_digit_boxes_px"])):
                choices = np.where(labels == int(digit))[0]
                sample = images[choices[(index * 13 + 7) % len(choices)]]
                array = np.uint8(255 - (sample / max(1.0, float(sample.max()))) * 255)
                glyph = Image.fromarray(array, "L").resize((42, 56), Image.Resampling.BICUBIC)
                glyph_array = np.where(np.asarray(glyph) < 205, 0, 255).astype("uint8")
                glyph = Image.fromarray(glyph_array, "L")
                x, y, width, height = [int(round(value)) for value in box]
                page.paste(glyph, (x + (width - 42) // 2, y + (height - 56) // 2))
            source = root / "handwritten_date.png"
            page.convert("RGB").save(source)
            result = process_mrs_image(source, root / "out", ROOT)
        self.assertEqual(result["date_recognition"]["value"], "2026-07-16")
        self.assertTrue(result["date_recognition"]["requires_operator_review"])
        self.assertTrue(result["date_recognition"]["resolved_by_date_constraints"])

    def test_scanner_remote_exposes_control_text_crop_and_manual_comment_transcription(self) -> None:
        html = (ROOT / "school_csm_control_center" / "web_server" / "static" / "scanner_remote.html").read_text(encoding="utf-8")
        jobs = (ROOT / "school_csm_control_center" / "web_server" / "scanner_jobs.py").read_text(encoding="utf-8")
        self.assertIn('id="controlNumberCrop"', html)
        self.assertIn("manual transcription", html)
        self.assertIn('"control_number.jpg"', jobs)


if __name__ == "__main__":
    unittest.main()
