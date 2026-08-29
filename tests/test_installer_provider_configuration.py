from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
VALIDATOR_SOURCE = REPO_ROOT / "packaging" / "installer" / "ProviderConfiguration.cs"
INSTALLER_ENGINE_SOURCE = REPO_ROOT / "packaging" / "installer" / "InstallerEngine.cs"


@unittest.skipUnless(os.name == "nt", "The maintenance installer is Windows-only.")
class InstallerProviderConfigurationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        windows = Path(os.environ["WINDIR"])
        compiler = windows / "Microsoft.NET/Framework/v4.0.30319/csc.exe"
        if not compiler.is_file():
            raise unittest.SkipTest("The .NET Framework C# compiler is unavailable.")
        cls._temporary = tempfile.TemporaryDirectory()
        root = Path(cls._temporary.name)
        harness = root / "Harness.cs"
        harness.write_text(
            r'''
using System;

namespace MoSSLab.SchoolCSM.Installer
{
    internal static class Harness
    {
        private static int Main(string[] args)
        {
            try
            {
                byte[] exact = ProviderConfiguration.ReadValidated(args[0]);
                Console.WriteLine("VALID=" + exact.Length);
                return 0;
            }
            catch (Exception error)
            {
                Console.Error.WriteLine(error.Message);
                return 2;
            }
        }
    }
}
'''.strip(),
            encoding="utf-8",
        )
        cls.executable = root / "provider-validator.exe"
        compiled = subprocess.run(
            [
                str(compiler),
                "/nologo",
                "/target:exe",
                f"/out:{cls.executable}",
                "/reference:System.dll",
                "/reference:System.Runtime.Serialization.dll",
                str(VALIDATOR_SOURCE),
                str(harness),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if compiled.returncode != 0:
            cls._temporary.cleanup()
            raise AssertionError(compiled.stdout + compiled.stderr)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._temporary.cleanup()

    @staticmethod
    def _document() -> dict:
        return {
            "schema_version": "1.0",
            "provider_name": "Division Gateway",
            "registration_service_url": "https://register.example.gov.ph/base",
            "managed_domain": "csm.example.gov.ph",
            "authorization_signing_keys": [
                {
                    "key_id": "provider-2026",
                    "public_key_base64": base64.b64encode(b"k" * 32).decode("ascii"),
                }
            ],
            "trusted_proxy_addresses": ["127.0.0.1", "::1"],
        }

    def _validate(self, document: dict) -> subprocess.CompletedProcess[str]:
        path = Path(self._temporary.name) / "internet_gateway_provider.json"
        path.write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return subprocess.run(
            [str(self.executable), str(path)],
            capture_output=True,
            text=True,
            timeout=15,
        )

    def test_accepts_valid_non_secret_provider_configuration(self) -> None:
        result = self._validate(self._document())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertRegex(result.stdout, r"VALID=\d+")

    def test_rejects_non_https_registration_service(self) -> None:
        document = self._document()
        document["registration_service_url"] = "http://register.example.gov.ph"
        result = self._validate(document)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("registration service address is invalid", result.stderr)

    def test_rejects_omitted_trusted_proxy_addresses(self) -> None:
        document = self._document()
        del document["trusted_proxy_addresses"]
        result = self._validate(document)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no trusted proxy addresses", result.stderr)

    def test_rejects_empty_trusted_proxy_addresses(self) -> None:
        document = self._document()
        document["trusted_proxy_addresses"] = []
        result = self._validate(document)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no trusted proxy addresses", result.stderr)

    def test_uninstall_stops_if_an_installed_invalid_config_cannot_be_preserved(self) -> None:
        source = INSTALLER_ENGINE_SOURCE.read_text(encoding="utf-8")
        method_start = source.index(
            "private void QuarantineInvalidProviderConfiguration"
        )
        method_end = source.index("private static void CopyDirectory", method_start)
        method = source[method_start:method_end]
        self.assertIn("sourceWillBeDeleted = IsPathWithin(source, InstallRoot)", method)
        self.assertIn("Uninstall was stopped", method)
        self.assertIn("throw new InvalidOperationException", method)
        self.assertIn("already outside the application removal boundary", method)


if __name__ == "__main__":
    unittest.main()
