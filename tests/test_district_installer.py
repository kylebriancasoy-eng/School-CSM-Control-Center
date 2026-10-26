from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from scripts import create_district_release as manifests
from scripts import package_district_management_server as packages
from tests.test_district_release_packaging import (
    DistrictReleasePackagingTests, TEST_CLOUDFLARED_SHA256,
)

ROOT = Path(__file__).resolve().parents[1]


class DistrictInstallerManifestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = DistrictReleasePackagingTests._valid_app(self.root)
        self.installer = self.root / manifests.INSTALLER_NAME
        self.installer.write_bytes(b"MZ-district-bootstrap-fixture")
        self.checksum_patch = patch.object(packages, "CLOUDFLARED_WINDOWS_AMD64_SHA256", TEST_CLOUDFLARED_SHA256)
        self.checksum_patch.start()
        self.addCleanup(self.checksum_patch.stop)
        self.archive, _ = packages.package_district_server(self.app, self.root / "archives")

    def generate(self, **changes):
        values = dict(app_dir=self.app, archive=self.archive, installer=self.installer,
                      output=self.root / "published", repository="owner/repository")
        values.update(changes)
        return manifests.create_manifest(**values)

    def test_distinct_product_manifest_hashes_every_asset_and_checks_native_identity(self):
        with patch.object(manifests, "check_native_metadata") as native:
            result = self.generate()
        self.assertEqual(result.name, "district-release.json")
        data = json.loads(result.read_text())
        self.assertEqual(data["applicationId"], "MoSSLab.SchoolCSMDistrictManagementServer")
        self.assertEqual(data["package"]["entryPoint"], packages.ENTRY_POINT)
        self.assertEqual(data["installer"]["fileName"], manifests.INSTALLER_NAME)
        self.assertEqual(native.call_count, 2)
        self.assertEqual(native.call_args_list[0].args, (self.app / packages.ENTRY_POINT,
            str(manifests.VERSION_DATA["WINDOWS_FILE_VERSION_TEXT"])))
        for item in (data["package"], data["installer"]):
            path = result.parent / item["fileName"]
            self.assertEqual(item["sha256"], hashlib.sha256(path.read_bytes()).hexdigest().upper())
            self.assertEqual(item["sizeBytes"], path.stat().st_size)
            self.assertTrue(item["url"].startswith("https://github.com/owner/repository/releases/download/"))
        self.assertFalse((result.parent / "release.json").exists())
        self.assertTrue((result.parent / "District-SHA256SUMS.txt").is_file())

    def test_rejects_wrong_product_setup_bad_metadata_and_mismatched_archive(self):
        wrong = self.root / "School-CSM-Control-Center-Setup.exe"
        wrong.write_bytes(b"school")
        with self.assertRaises(ValueError): self.generate(installer=wrong)
        with patch.object(manifests, "check_native_metadata", side_effect=ValueError("wrong native product")):
            with self.assertRaises(ValueError): self.generate()
        with zipfile.ZipFile(self.archive, "a") as archive:
            archive.writestr("unexpected-data.json", "private")
        with patch.object(manifests, "check_native_metadata"):
            with self.assertRaisesRegex(ValueError, "entries"): self.generate()

    def test_rejects_runtime_backups_and_bad_repository_or_tag(self):
        for relative in ("Backups/recovery.mossdistrict", "School Deployment/private.json", "saved.mossdistrict"):
            path = self.app / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"private")
            with self.assertRaisesRegex(ValueError, "runtime state"):
                packages.validate_compiled_directory(self.app)
            path.unlink()
        with self.assertRaises(ValueError): self.generate(repository="https://evil.invalid/repo")
        with self.assertRaises(ValueError): self.generate(tag="v0.0.1")


@unittest.skipUnless(os.name == "nt", "Native setup is Windows-only")
class DistrictInstallerNativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.compiler = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework/v4.0.30319/csc.exe"
        if not cls.compiler.is_file():
            cls.temp.cleanup()
            raise unittest.SkipTest("C# compiler unavailable")
        config = (ROOT / "packaging/installer/GeneratedConfig.cs.in").read_text()
        for key, value in {
            "REPOSITORY": "owner/repository", "INSTALLER_VERSION": "1.0.1.0",
            "APPLICATION_ID": "MoSSLab.SchoolCSMDistrictManagementServer",
            "DISPLAY_NAME": manifests.PRODUCT_NAME, "APPLICATION_EXE": packages.ENTRY_POINT,
            "SETUP_EXE": manifests.INSTALLER_NAME, "MANIFEST_NAME": manifests.MANIFEST_NAME,
            "IS_DISTRICT": "true",
        }.items():
            config = config.replace("@@" + key + "@@", value)
        (cls.root / "Config.cs").write_text(config)
        # The harness never invokes Setup's Main, network requests, registry
        # changes, application launching, or destructive maintenance. All
        # engine filesystem fields are rebound to disposable paths first.
        (cls.root / "Harness.cs").write_text(r'''
using System;
using System.IO;
using System.Reflection;
namespace MoSSLab.SchoolCSM.Installer {
internal static class Harness {
  static void Set(string name, string value) {
    typeof(InstallerEngine).GetField(name, BindingFlags.Static|BindingFlags.NonPublic).SetValue(null,value);
  }
  static void MustFail(Action action) {
    try { action(); } catch (Exception) { Console.WriteLine("BLOCKED"); return; }
    throw new Exception("Unsafe operation was not blocked");
  }
  public static int Main(string[] args) {
    try {
      string root=Path.GetFullPath(args[0]);
      Directory.CreateDirectory(root);
      string vendor=Path.Combine(root,"programs");
      string install=Path.Combine(vendor,"district");
      string maintenance=Path.Combine(vendor,"district-maintenance");
      string data=Path.Combine(root,"district-data");
      Set("VendorProgramFilesRoot",vendor); Set("InstallRoot",install);
      Set("ApplicationExecutable",Path.Combine(install,InstallerEngine.ApplicationExeName));
      Set("MaintenanceRoot",maintenance); Set("DistrictDataRoot",data);
      Set("InstalledManifestPath",Path.Combine(maintenance,"installed-release.json"));
      Set("MaintenanceLogPath",Path.Combine(maintenance,"maintenance.log"));
      Directory.CreateDirectory(Path.Combine(data,"Data"));
      string marker=Path.Combine(data,"Data","registration.sqlite3");
      File.WriteAllText(marker,"KEEP DISTRICT DATA");
      InstallerEngine engine=new InstallerEngine();
      if(!engine.HasDistrictData || engine.CanRollback) throw new Exception("Invalid safety state");
      MustFail(delegate {engine.InstallOrUpdate();});
      MustFail(delegate {engine.Repair();});
      MustFail(delegate {engine.Uninstall(true);});
      MustFail(delegate {engine.Rollback();});
      MustFail(delegate {CommandLine.Parse(new string[]{"--uninstall","--remove-user-data"});});
      MustFail(delegate {CommandLine.Parse(new string[]{"--rollback"});});
      if(File.ReadAllText(marker)!="KEEP DISTRICT DATA") throw new Exception("District data changed");
      // These district profile guards must return before any school firewall
      // or Credential Manager API can be touched.
      WindowsIntegration.ConfigureFirewallRule();
      WindowsIntegration.RemoveFirewallRules();
      if(WindowsIntegration.DeleteCurrentUserCredentials()) throw new Exception("Credential deletion allowed");
      engine.DistrictBackupConfirmed=true;
      Environment.SetEnvironmentVariable("SCHOOL_CSM_DISTRICT_DATA_ROOT",Path.Combine(install,"runtime"));
      MustFail(delegate {engine.InstallOrUpdate();});
      Environment.SetEnvironmentVariable("SCHOOL_CSM_DISTRICT_DATA_ROOT",null);
      Console.WriteLine("SAFE"); return 0;
    } catch(Exception error) {Console.Error.WriteLine(error); return 1;}
  }
}}
''')
        cls.exe = cls.root / "harness.exe"
        files = [ROOT / "packaging/installer" / name for name in (
            "Program.cs", "InstallerEngine.cs", "ReleaseManifest.cs", "MaintenanceForm.cs",
            "WindowsIntegration.cs", "ProviderConfiguration.cs", "ResilientDownloader.cs",
        )]
        command = [str(cls.compiler), "/nologo", "/target:exe", "/platform:x86",
            "/main:MoSSLab.SchoolCSM.Installer.Harness", f"/out:{cls.exe}",
            "/reference:System.dll", "/reference:System.Core.dll", "/reference:System.Drawing.dll",
            "/reference:System.Windows.Forms.dll", "/reference:System.Runtime.Serialization.dll",
            "/reference:System.IO.Compression.dll", "/reference:System.IO.Compression.FileSystem.dll",
            *(str(file) for file in files), str(cls.root / "Config.cs"), str(cls.root / "Harness.cs")]
        result = subprocess.run(command, capture_output=True, text=True, timeout=60)
        if result.returncode:
            cls.temp.cleanup()
            raise AssertionError(result.stdout + result.stderr)

    @classmethod
    def tearDownClass(cls): cls.temp.cleanup()

    def test_native_safety_gates_preserve_existing_district_state(self):
        result = subprocess.run([str(self.exe), str(self.root / "isolated-state")],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("SAFE", result.stdout)
        self.assertEqual(result.stdout.count("BLOCKED"), 7)


if __name__ == "__main__": unittest.main()
