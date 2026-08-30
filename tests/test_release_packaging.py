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
        tunnel = app / "vendor" / "cloudflared" / "cloudflared.exe"
        tunnel.parent.mkdir(parents=True)
        tunnel.write_bytes(b"MZ\x00cloudflared-test-fixture")
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
            self.assertEqual(manifest["version"], "0.5.1")
            self.assertEqual(manifest["tag"], "v0.5.1")
            self.assertIn("/releases/download/v0.5.1/", manifest["package"]["url"])
            self.assertEqual(
                manifest["package"]["entryPoint"], "School CSM Control Center.exe"
            )
            package = output / manifest["package"]["fileName"]
            self.assertEqual(manifest["package"]["sha256"], sha256(package))
            with zipfile.ZipFile(package) as archive:
                self.assertIn("School CSM Control Center.exe", archive.namelist())
                self.assertIn("vendor/cloudflared/cloudflared.exe", archive.namelist())
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
            tunnel = app / "vendor" / "cloudflared" / "cloudflared.exe"
            tunnel.parent.mkdir(parents=True)
            tunnel.write_bytes(b"MZ\x00cloudflared-test-fixture")
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
            tunnel = app / "vendor" / "cloudflared" / "cloudflared.exe"
            tunnel.parent.mkdir(parents=True)
            tunnel.write_bytes(b"MZ\x00cloudflared-test-fixture")
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

    def test_release_rejects_missing_gateway_component(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            app = root / "compiled-app"
            app.mkdir(parents=True)
            (app / "School CSM Control Center.exe").write_bytes(b"MZ")
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
            self.assertIn("Internet Gateway component", result.stderr)

    def test_public_release_rejects_production_provider_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            app = root / "compiled-app"
            app.mkdir(parents=True)
            (app / "School CSM Control Center.exe").write_bytes(b"MZ")
            tunnel = app / "vendor" / "cloudflared" / "cloudflared.exe"
            tunnel.parent.mkdir(parents=True)
            tunnel.write_bytes(b"MZ\x00cloudflared-test-fixture")
            (app / "internet_gateway_provider.json").write_text(
                '{"production":true}', encoding="utf-8"
            )
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
            self.assertIn("must not be included in a public release", result.stderr)

    @unittest.skipUnless(os.name == "nt", "The Windows release scripts require PowerShell.")
    def test_clean_release_refuses_output_outside_dedicated_release_tree(self) -> None:
        scratch_root = REPO_ROOT / "tmp"
        scratch_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch_root) as temporary:
            unsafe_output = Path(temporary)
            marker = unsafe_output / "must-survive.txt"
            marker.write_text("preserve", encoding="utf-8")
            result = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(REPO_ROOT / "scripts" / "build_release.ps1"),
                    "-Repository",
                    "example/school-csm",
                    "-OutputRoot",
                    str(unsafe_output),
                    "-Clean",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertTrue(marker.is_file())
            self.assertIn("release directory", result.stderr)

    def test_installer_source_contains_required_safety_boundaries(self) -> None:
        installer = (REPO_ROOT / "packaging" / "installer" / "InstallerEngine.cs").read_text(
            encoding="utf-8"
        )
        windows = (REPO_ROOT / "packaging" / "installer" / "WindowsIntegration.cs").read_text(
            encoding="utf-8"
        )
        program = (REPO_ROOT / "packaging" / "installer" / "Program.cs").read_text(
            encoding="utf-8"
        )
        downloader = (
            REPO_ROOT / "packaging" / "installer" / "ResilientDownloader.cs"
        ).read_text(encoding="utf-8")
        build_installer = (REPO_ROOT / "scripts" / "build_installer.ps1").read_text(
            encoding="utf-8"
        )
        app_manifest = (REPO_ROOT / "packaging" / "installer" / "app.manifest").read_text(
            encoding="utf-8"
        )
        build_app = (REPO_ROOT / "scripts" / "build_app.ps1").read_text(encoding="utf-8")
        build_release = (REPO_ROOT / "scripts" / "build_release.ps1").read_text(
            encoding="utf-8"
        )
        pyinstaller_spec = (
            REPO_ROOT / "packaging" / "pyinstaller" / "SchoolCSMControlCenter.spec"
        ).read_text(encoding="utf-8")
        self.assertIn('ProgramFilesX86(), "MoSSLab"', installer)
        self.assertIn('ProviderConfigurationFileName = "internet_gateway_provider.json"', installer)
        self.assertIn("PreserveProviderConfiguration();", installer)
        self.assertIn("PreserveProviderConfiguration(true);", installer)
        self.assertIn("ProviderConfigurationPath", installer)
        self.assertIn('Path.Combine(ProviderConfigurationRoot, "Quarantine")', installer)
        self.assertIn("File.Move(source, quarantinePath);", installer)
        self.assertIn('OpenAiCredentialTarget = "MoSSLab.SchoolCSMControlCenter.OpenAIApiKey"', installer)
        for target in (
            "TunnelCredential",
            "InstallationSecret",
            "DevicePrivateKey",
        ):
            self.assertIn(
                f"MoSSLab.SchoolCSMControlCenter.InternetGateway.{target}", installer
            )
        self.assertIn("WindowsIntegration.DeleteCurrentUserCredentials()", installer)
        self.assertIn("if (removeUserData)", installer)
        self.assertNotIn("DeleteWithin(ProviderConfigurationRoot", installer)
        self.assertIn("WaitForApplicationExit", installer)
        self.assertIn("VerifyLocalPackage", installer)
        self.assertIn("CopyWithLimit", downloader)
        self.assertIn("entry.ExternalAttributes", installer)
        self.assertIn("DeleteDirectoryNoFollow", installer)
        self.assertIn('@"Global\\MoSSLab.SchoolCSMControlCenter.Maintenance"', program)
        self.assertIn('case "--wait-for-pid":', program)
        self.assertIn(
            "--wait-for-pid is accepted only together with --uninstall.", program
        )

        self.assertIn(
            'FirewallRuleName = "School CSM Control Center - Private Local Survey"',
            windows,
        )
        self.assertIn(
            'StartupValueName = "MoSSLab.SchoolCSMControlCenter"',
            windows,
        )
        self.assertIn('"ApplicationName", InstallerEngine.ApplicationExecutable', windows)
        self.assertIn('"Protocol", FirewallProtocolAny', windows)
        self.assertIn('"Profiles", FirewallProfilePrivate', windows)
        self.assertIn('"RemoteAddresses", "LocalSubnet"', windows)
        self.assertIn('"Direction", FirewallDirectionInbound', windows)
        self.assertNotIn('"Profiles", 4', windows)
        for legacy_rule in (
            "School CSM Control Center TCP 80",
            "School CSM Control Center TCP 8080",
            "School CSM Control Center TCP 53",
            "School CSM Control Center UDP 53",
        ):
            self.assertIn(f'"{legacy_rule}"', windows)
        self.assertIn('Registry.CurrentUser.OpenSubKey(StartupRunKey, true)', windows)
        self.assertIn('key.DeleteValue(StartupValueName, false)', windows)
        self.assertNotIn('Registry.CurrentUser.DeleteSubKey', windows)

        self.assertGreaterEqual(
            installer.count("WindowsIntegration.ConfigureFirewallRule();"),
            3,
        )
        self.assertGreaterEqual(
            installer.count("WindowsIntegration.RemoveFirewallRules();"),
            2,
        )
        self.assertIn("WindowsIntegration.RemoveCurrentUserStartupRegistration();", installer)
        self.assertIn("notification-area tray", installer)
        self.assertIn("MaximumAttempts = 5", downloader)
        self.assertIn("request.AddRange(rangeStart)", downloader)
        self.assertIn("TryParseContentRange", downloader)
        self.assertIn("GitHub connection was interrupted", downloader)
        self.assertIn("KeepAlive = false", downloader)
        self.assertIn('"ResilientDownloader.cs"', build_installer)
        self.assertIn('"ProviderConfiguration.cs"', build_installer)
        self.assertIn('InstallerVersion = "1.0.1.0"', build_installer)
        self.assertIn('assemblyIdentity version="1.0.1.0"', app_manifest)
        self.assertIn("scripts\\fetch_cloudflared.py", build_app)
        self.assertIn("Get-FileHash", build_app)
        self.assertIn('$releaseRoot = [System.IO.Path]::GetFullPath', build_app)
        self.assertIn('$releaseRoot = [System.IO.Path]::GetFullPath', build_release)
        self.assertIn("repository's release directory", build_release)
        self.assertIn('"vendor/cloudflared"', pyinstaller_spec)
        self.assertIn("engine.RecordFailure(error);", program)
        self.assertIn("engine.RecordFailure(completed.Error);", (
            REPO_ROOT / "packaging" / "installer" / "MaintenanceForm.cs"
        ).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
