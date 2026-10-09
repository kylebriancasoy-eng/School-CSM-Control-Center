from __future__ import annotations

import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import zipfile

import scripts.package_district_management_server as district_packaging
from scripts.package_district_management_server import (
    CLOUDFLARED_ENTRY,
    ENTRY_POINT,
    RELEASE_TAG,
    REQUIRED_RUNTIME_ASSETS,
    package_district_server,
)


TEST_CLOUDFLARED = b"MZ\x00pinned-cloudflared-test-fixture"
TEST_CLOUDFLARED_SHA256 = hashlib.sha256(TEST_CLOUDFLARED).hexdigest()


class DistrictReleasePackagingTests(unittest.TestCase):
    @staticmethod
    def _valid_app(root: Path) -> Path:
        app = root / "School CSM District Management Server"
        app.mkdir()
        (app / ENTRY_POINT).write_bytes(b"compiled-entry-point")
        internal = app / "_internal"
        internal.mkdir()
        (internal / "runtime.dll").write_bytes(b"runtime")

        tunnel = app.joinpath(*CLOUDFLARED_ENTRY.parts)
        tunnel.parent.mkdir(parents=True)
        tunnel.write_bytes(TEST_CLOUDFLARED)
        for relative in REQUIRED_RUNTIME_ASSETS:
            destination = app.joinpath(*relative.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(
                district_packaging.REPO_ROOT.joinpath(*relative.parts).read_bytes()
            )
        return app

    @staticmethod
    def _package(app: Path, output: Path, *, tag: str = RELEASE_TAG):
        with patch.object(
            district_packaging,
            "CLOUDFLARED_WINDOWS_AMD64_SHA256",
            TEST_CLOUDFLARED_SHA256,
        ):
            return package_district_server(app, output, tag=tag)

    def test_creates_deterministic_checksums_without_python_requirement(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            app = self._valid_app(root)

            first, first_checksum = self._package(app, root / "one")
            second, second_checksum = self._package(app, root / "two")

            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertEqual(first.name, f"School-CSM-District-Management-Server-{RELEASE_TAG}-win-x64.zip")
            expected = hashlib.sha256(first.read_bytes()).hexdigest().upper()
            self.assertEqual(
                first_checksum.read_text(encoding="ascii"),
                f"{expected}  {first.name}\n",
            )
            self.assertEqual(first_checksum.read_bytes(), second_checksum.read_bytes())
            with zipfile.ZipFile(first) as archive:
                self.assertIn(ENTRY_POINT, archive.namelist())
                self.assertIn("_internal/runtime.dll", archive.namelist())
                self.assertIn(str(CLOUDFLARED_ENTRY), archive.namelist())
                for relative in REQUIRED_RUNTIME_ASSETS:
                    self.assertIn(str(relative), archive.namelist())

    def test_rejects_missing_required_components(self) -> None:
        required = (CLOUDFLARED_ENTRY, *REQUIRED_RUNTIME_ASSETS)
        for relative in required:
            with self.subTest(relative=str(relative)), TemporaryDirectory() as temporary:
                root = Path(temporary)
                app = self._valid_app(root)
                app.joinpath(*relative.parts).unlink()
                with self.assertRaisesRegex(ValueError, "missing a required component"):
                    self._package(app, root / "output")

    def test_rejects_tampered_cloudflared(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            app = self._valid_app(root)
            app.joinpath(*CLOUDFLARED_ENTRY.parts).write_bytes(b"MZ\x00tampered")
            with self.assertRaisesRegex(ValueError, "failed its integrity check"):
                self._package(app, root / "output")

    def test_rejects_tampered_runtime_assets(self) -> None:
        for relative in REQUIRED_RUNTIME_ASSETS:
            with self.subTest(relative=str(relative)), TemporaryDirectory() as temporary:
                root = Path(temporary)
                app = self._valid_app(root)
                app.joinpath(*relative.parts).write_bytes(b"tampered")
                with self.assertRaisesRegex(ValueError, "failed its integrity check"):
                    self._package(app, root / "output")

    def test_rejects_runtime_database_configuration_and_logs(self) -> None:
        cases = (
            Path("Data") / "registration.sqlite3",
            Path("Configuration") / "server.json",
            Path("Logs") / "district-management-server.log",
            Path("_internal") / "copied-state.db",
        )
        for relative in cases:
            with self.subTest(relative=str(relative)), TemporaryDirectory() as temporary:
                root = Path(temporary)
                app = self._valid_app(root)
                unsafe = app / relative
                unsafe.parent.mkdir(parents=True, exist_ok=True)
                unsafe.write_bytes(b"must-not-publish")
                with self.assertRaisesRegex(ValueError, "runtime state"):
                    self._package(app, root / "output")

    def test_rejects_mismatched_release_tag(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            app = self._valid_app(root)
            with self.assertRaisesRegex(ValueError, "does not match"):
                self._package(app, root / "output", tag="v999.0.0")


if __name__ == "__main__":
    unittest.main()
