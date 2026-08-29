from __future__ import annotations

import importlib.util
from importlib import metadata
from pathlib import Path
import sys
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "verify_build_environment.py"
SPEC = importlib.util.spec_from_file_location("verify_build_environment", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
preflight = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = preflight
SPEC.loader.exec_module(preflight)


class BuildDependencyPreflightTests(unittest.TestCase):
    def test_lock_parser_requires_exact_unique_pins(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            lock_path = Path(directory) / "requirements-lock.txt"
            lock_path.write_text(
                "# release dependencies\nqrcode[pil]==8.2\ncryptography==49.0.0\n",
                encoding="utf-8",
            )
            parsed = preflight.parse_locked_requirements(lock_path)
            self.assertEqual(
                [(row.name, row.version) for row in parsed],
                [("qrcode", "8.2"), ("cryptography", "49.0.0")],
            )

            lock_path.write_text("cryptography>=49\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "exact package==version pin"):
                preflight.parse_locked_requirements(lock_path)

    def test_version_check_reports_missing_and_mismatched_packages(self) -> None:
        requirements = (
            preflight.LockedRequirement("openai", "2.46.0"),
            preflight.LockedRequirement("cryptography", "49.0.0"),
        )

        def lookup(name: str) -> str:
            if name == "openai":
                return "2.26.0"
            raise metadata.PackageNotFoundError(name)

        installed, errors = preflight.check_locked_versions(requirements, lookup)
        self.assertEqual(installed, ["openai==2.26.0"])
        self.assertTrue(any("release lock requires openai==2.46.0" in row for row in errors))
        self.assertTrue(any("cryptography 49.0.0 is required" in row for row in errors))

    def test_functional_import_probe_fails_closed(self) -> None:
        def importer(name: str) -> object:
            if name == "cv2":
                raise ModuleNotFoundError("test missing cv2")
            return object()

        errors = preflight.run_functional_probes(
            importer,
            openai_probe=lambda _importer: None,
            cryptography_probe=lambda _importer: None,
        )
        self.assertEqual(len(errors), 1)
        self.assertIn("Could not import cv2", errors[0])

    def test_pyinstaller_major_version_seven_is_rejected(self) -> None:
        actual, errors = preflight.check_pyinstaller_version(lambda _name: "7")
        self.assertEqual(actual, "7")
        self.assertEqual(len(errors), 1)
        self.assertIn("requires >=6.11,<7", errors[0])

    def test_warning_scan_blocks_cryptography_but_ignores_optional_openai_extras(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            warning_path = Path(directory) / "warn.txt"
            warning_path.write_text(
                "missing module named 'websockets.sync' - imported by openai.resources\n"
                "missing module named 'PySide6.QtPrintSupport' - imported by launcher\n"
                "missing module named 'cryptography.hazmat' - imported by gateway_migration\n",
                encoding="utf-8",
            )
            errors = preflight.scan_pyinstaller_warnings(warning_path)
        self.assertEqual(len(errors), 2)
        self.assertTrue(any("PySide6.QtPrintSupport" in row for row in errors))
        self.assertTrue(any("cryptography.hazmat" in row for row in errors))

    def test_windows_build_runs_preflight_before_pyinstaller_and_scans_warnings(self) -> None:
        source = (REPO_ROOT / "scripts" / "build_app.ps1").read_text(encoding="utf-8")
        first_check = source.index("$dependencyVerifier --requirements $requirementsLock")
        clean = source.index("if ($Clean")
        pyinstaller = source.index("-m PyInstaller")
        warning_check = source.index("$dependencyVerifier --warnings-only")
        self.assertLess(first_check, clean)
        self.assertLess(first_check, pyinstaller)
        self.assertGreater(warning_check, pyinstaller)
        self.assertIn("warn-SchoolCSMControlCenter.txt", source)

    def test_frozen_launcher_dependency_metadata_is_bundled(self) -> None:
        source = (
            REPO_ROOT / "packaging" / "pyinstaller" / "SchoolCSMControlCenter.spec"
        ).read_text(encoding="utf-8")
        self.assertIn("METADATA_DISTRIBUTIONS", source)
        self.assertIn("datas += copy_metadata(distribution)", source)
        for distribution in (
            "PySide6",
            "qrcode",
            "Pillow",
            "numpy",
            "opencv-contrib-python-headless",
            "openai",
            "cryptography",
        ):
            self.assertIn(f'"{distribution}"', source)


if __name__ == "__main__":
    unittest.main()
