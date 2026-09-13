from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from PIL import ImageDraw

from school_csm_control_center.mrs_printing import (
    SQD5_OMISSION_REGION_PX,
    encode_code128b,
    infer_connection_type,
    is_virtual_printer_descriptor,
    is_virtual_printer_name,
    available_mrs_templates,
    render_code128_image,
    render_official_mrs_form,
    template_spec,
)
from school_csm_control_center.web_server.scanner_engine import process_mrs_image


ROOT = Path(__file__).parents[1]
CONTROL = "CSM-MRS-123627-2026-07-0001"


class MRSPrintingTests(unittest.TestCase):
    def test_virtual_output_queues_are_excluded(self) -> None:
        for name in (
            "Microsoft Print to PDF",
            "Microsoft XPS Document Writer",
            "Fax",
            "OneNote",
            "Adobe PDF",
            "PDFCreator",
        ):
            with self.subTest(name=name):
                self.assertTrue(is_virtual_printer_name(name))
        self.assertFalse(is_virtual_printer_name("Epson L3210 Series"))
        self.assertTrue(is_virtual_printer_descriptor("Office Queue", "Generic PDF Driver", "PORTPROMPT:"))
        self.assertFalse(is_virtual_printer_descriptor("Epson L3210 Series", "EPSON Driver", "USB001"))
        self.assertEqual(infer_connection_type("Epson L3210 Series", "USB001"), "USB")
        self.assertEqual(infer_connection_type("Shared Printer", "IP_192.168.1.50"), "Network")

    def test_code128b_has_checksum_and_stop_symbol(self) -> None:
        codes = encode_code128b(CONTROL)
        self.assertEqual(codes[0], 104)
        self.assertEqual(codes[-1], 106)
        self.assertGreater(len(codes), len(CONTROL))
        barcode = render_code128_image(CONTROL, 900, 90)
        self.assertEqual(barcode.mode, "L")
        self.assertGreater(barcode.width, 800)

    def test_official_page_renders_in_memory_and_scanner_decodes_it(self) -> None:
        with TemporaryDirectory() as temporary:
            page = render_official_mrs_form(ROOT, CONTROL, school_name="Calapi Elementary School")
            self.assertEqual(page.size, (2480, 3508))
            source = Path(temporary) / "official.png"
            page.save(source)
            result = process_mrs_image(source, Path(temporary) / "recognized", ROOT)
            self.assertEqual(result["barcode"]["value"], CONTROL)
            self.assertEqual(result["barcode"]["status"], "Normal")


    def test_all_three_language_templates_are_installed_and_round_trip(self) -> None:
        installed = {spec["code"]: spec for spec in available_mrs_templates(ROOT)}
        self.assertEqual(set(installed), {"en", "fil", "war"})
        for code in ("en", "fil", "war"):
            spec = template_spec(code)
            with self.subTest(language=spec["language"]), TemporaryDirectory() as temporary:
                page = render_official_mrs_form(
                    ROOT, CONTROL, school_name="Calapi Elementary School", language=spec["language"]
                )
                source = Path(temporary) / f"{code}.png"
                page.save(source)
                result = process_mrs_image(source, Path(temporary) / "recognized", ROOT)
                self.assertEqual(result["language_code"], code)
                self.assertEqual(result["template_id"], spec["template_id"])
                self.assertEqual(result["barcode"]["value"], CONTROL)
                self.assertEqual(result["barcode"]["status"], "Normal")

    def test_new_official_pages_visibly_omit_sqd5_without_moving_later_rows(self) -> None:
        left, top, right, bottom = SQD5_OMISSION_REGION_PX
        self.assertLess(top, bottom)
        for language in ("English", "Filipino", "Waray-Waray"):
            with self.subTest(language=language):
                page = render_official_mrs_form(
                    ROOT,
                    CONTROL,
                    school_name="Calapi Elementary School",
                    language=language,
                )
                # This point crossed the old SQD5 response-bubble outline. New
                # prints clear it, while the legacy source templates stay intact.
                self.assertEqual(page.getpixel((1814, 2774)), (255, 255, 255))
                self.assertEqual(page.size, (2480, 3508))

    def test_end_to_end_barcode_crossout_is_detected(self) -> None:
        with TemporaryDirectory() as temporary:
            page = render_official_mrs_form(ROOT, CONTROL, school_name="Calapi Elementary School")
            draw = ImageDraw.Draw(page)
            draw.line((250, 680, 1430, 690), fill="black", width=18)
            source = Path(temporary) / "crossed.png"
            page.save(source)
            result = process_mrs_image(source, Path(temporary) / "recognized", ROOT)
            self.assertEqual(result["barcode"]["value"], CONTROL)
            self.assertEqual(result["barcode"]["status"], "Crossed Out")
            self.assertGreater(result["barcode"]["crossout_coverage"], 0.8)


if __name__ == "__main__":
    unittest.main()
