from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile


REPO_ROOT = Path(__file__).resolve().parents[1]
CREATE_RELEASE = REPO_ROOT / "scripts" / "create_release.py"
VERIFY_RELEASE = REPO_ROOT / "scripts" / "verify_release.py"
ALLOWED_OPENCV_RUNTIME_MODULES = {
    "cv2/__init__.py",
    "cv2/config.py",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def refresh_release_checksums(output: Path, manifest: dict) -> None:
    package = output / manifest["package"]["fileName"]
    installer = output / manifest["installer"]["fileName"]
    manifest["package"]["sha256"] = sha256(package)
    manifest["package"]["sizeBytes"] = package.stat().st_size
    manifest_path = output / "release.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    (output / "SHA256SUMS.txt").write_text(
        "".join(
            f"{sha256(path)}  {path.name}\n"
            for path in (package, installer, manifest_path)
        ),
        encoding="ascii",
        newline="\n",
    )


class ReleasePackagingTests(unittest.TestCase):
    def _build_fake_release(self, root: Path, output_name: str = "release") -> Path:
        app = root / "compiled-app"
        app.mkdir(parents=True)
        (app / "School CSM Control Center.exe").write_bytes(b"MZ\x00compiled-app-test")
        (app / "runtime.dll").write_bytes(b"runtime")
        nested = app / "assets"
        nested.mkdir()
        (nested / "brand.png").write_bytes(b"png")
        installer = root / "setup.exe"
        installer.write_bytes(b"MZ\x00setup-test")
        output = root / output_name
        environment = dict(os.environ)
        environment["SOURCE_DATE_EPOCH"] = "1767225600"
        subprocess.run(
            [
                sys.executable,
                str(CREATE_RELEASE),
                "--app-dir",
                str(app),
                "--installer",
                str(installer),
                "--output",
                str(output),
                "--repository",
                "example/school-csm",
            ],
            check=True,
            capture_output=True,
            text=True,
            env=environment,
        )
        return output

    def test_release_is_checksummed_and_verifiable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = self._build_fake_release(root)
            subprocess.run(
                [sys.executable, str(VERIFY_RELEASE), str(output)],
                check=True,
                capture_output=True,
                text=True,
            )
            manifest = json.loads((output / "release.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["applicationId"], "MoSSLab.SchoolCSMControlCenter")
            self.assertEqual(manifest["version"], "0.4.0")
            self.assertEqual(manifest["tag"], "v0.4.0")
            self.assertIn("/releases/download/v0.4.0/", manifest["package"]["url"])
            self.assertEqual(
                manifest["package"]["entryPoint"], "School CSM Control Center.exe"
            )
            package = output / manifest["package"]["fileName"]
            self.assertEqual(manifest["package"]["sha256"], sha256(package))
            with zipfile.ZipFile(package) as archive:
                self.assertIn("School CSM Control Center.exe", archive.namelist())
                self.assertFalse(
                    any(
                        Path(name).suffix.casefold()
                        in {".py", ".pyw", ".pyc", ".pyo", ".cmd", ".bat", ".vbs", ".ps1", ".psm1", ".sh"}
                        and name not in ALLOWED_OPENCV_RUNTIME_MODULES
                        for name in archive.namelist()
                    )
                )

    def test_required_opencv_runtime_modules_are_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            app = root / "compiled-app"
            (app / "cv2").mkdir(parents=True)
            (app / "School CSM Control Center.exe").write_bytes(b"MZ")
            (app / "cv2" / "__init__.py").write_text("# runtime loader\n", encoding="utf-8")
            (app / "cv2" / "config.py").write_text("# runtime config\n", encoding="utf-8")
            installer = root / "setup.exe"
            installer.write_bytes(b"MZ")
            output = root / "release"
            subprocess.run(
                [
                    sys.executable,
                    str(CREATE_RELEASE),
                    "--app-dir",
                    str(app),
                    "--installer",
                    str(installer),
                    "--output",
                    str(output),
                    "--repository",
                    "example/school-csm",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                [sys.executable, str(VERIFY_RELEASE), str(output)],
                check=True,
                capture_output=True,
                text=True,
            )

    def test_application_zip_is_reproducible(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = self._build_fake_release(root / "first")
            second = self._build_fake_release(root / "second")
            first_manifest = json.loads((first / "release.json").read_text(encoding="utf-8"))
            second_manifest = json.loads((second / "release.json").read_text(encoding="utf-8"))
            first_package = first / first_manifest["package"]["fileName"]
            second_package = second / second_manifest["package"]["fileName"]
            self.assertEqual(sha256(first_package), sha256(second_package))

    def test_development_launcher_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            app = root / "app"
            app.mkdir()
            (app / "School CSM Control Center.exe").write_bytes(b"MZ")
            (app / "START.cmd").write_text("python app.py", encoding="utf-8")
            installer = root / "setup.exe"
            installer.write_bytes(b"MZ")
            result = subprocess.run(
                [
                    sys.executable,
                    str(CREATE_RELEASE),
                    "--app-dir",
                    str(app),
                    "--installer",
                    str(installer),
                    "--output",
                    str(root / "release"),
                    "--repository",
                    "example/school-csm",
                ],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("development files", result.stderr)

    def test_verifier_rejects_backslash_traversal_even_with_matching_checksum(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = self._build_fake_release(Path(temporary))
            manifest = json.loads((output / "release.json").read_text(encoding="utf-8"))
            package = output / manifest["package"]["fileName"]
            with zipfile.ZipFile(package, "w") as archive:
                archive.writestr("School CSM Control Center.exe", b"MZ\x00compiled-app-test")
                archive.writestr("..\\outside.txt", b"unsafe")
            refresh_release_checksums(output, manifest)
            result = subprocess.run(
                [sys.executable, str(VERIFY_RELEASE), str(output)],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertRegex(result.stderr, r"(?:Invalid|Unsafe) ZIP entry")

    def test_verifier_rejects_symbolic_link_entry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = self._build_fake_release(Path(temporary))
            manifest = json.loads((output / "release.json").read_text(encoding="utf-8"))
            package = output / manifest["package"]["fileName"]
            with zipfile.ZipFile(package, "w") as archive:
                archive.writestr("School CSM Control Center.exe", b"MZ\x00compiled-app-test")
                link = zipfile.ZipInfo("linked-data")
                link.create_system = 3
                link.external_attr = 0o120777 << 16
                archive.writestr(link, "../../sensitive")
            refresh_release_checksums(output, manifest)
            result = subprocess.run(
                [sys.executable, str(VERIFY_RELEASE), str(output)],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Symbolic-link", result.stderr)

    def test_verifier_rejects_release_asset_from_another_repository(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = self._build_fake_release(Path(temporary))
            manifest = json.loads((output / "release.json").read_text(encoding="utf-8"))
            manifest["package"]["url"] = manifest["package"]["url"].replace(
                "example/school-csm", "attacker/substitute"
            )
            refresh_release_checksums(output, manifest)
            result = subprocess.run(
                [sys.executable, str(VERIFY_RELEASE), str(output)],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("does not match the GitHub release", result.stderr)

    def test_installer_source_contains_required_safety_boundaries(self) -> None:
        installer = (REPO_ROOT / "packaging" / "installer" / "InstallerEngine.cs").read_text(
            encoding="utf-8"
        )
        program = (REPO_ROOT / "packaging" / "installer" / "Program.cs").read_text(
            encoding="utf-8"
        )
        self.assertIn('ProgramFilesX86(), "MoSSLab"', installer)
        self.assertIn('OpenAiCredentialTarget = "MoSSLab.SchoolCSMControlCenter.OpenAIApiKey"', installer)
        self.assertIn("VerifyLocalPackage", installer)
        self.assertIn("CopyWithLimit", installer)
        self.assertIn("entry.ExternalAttributes", installer)
        self.assertIn("DeleteDirectoryNoFollow", installer)
        self.assertIn('@"Global\\MoSSLab.SchoolCSMControlCenter.Maintenance"', program)


if __name__ == "__main__":
    unittest.main()
