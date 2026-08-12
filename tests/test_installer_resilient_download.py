from __future__ import annotations

import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
DOWNLOADER_SOURCE = REPO_ROOT / "packaging" / "installer" / "ResilientDownloader.cs"


class _FlakyDownloadHandler(BaseHTTPRequestHandler):
    manifest = b'{"release":"test"}'
    package = bytes((index * 37) % 251 for index in range(512 * 1024))
    manifest_requests = 0
    package_ranges: list[str | None] = []

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path == "/manifest":
            type(self).manifest_requests += 1
            if type(self).manifest_requests == 1:
                self.send_response(503)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(self.manifest)))
            self.end_headers()
            self.wfile.write(self.manifest)
            return

        if self.path == "/package":
            requested_range = self.headers.get("Range")
            type(self).package_ranges.append(requested_range)
            if requested_range:
                prefix = "bytes="
                if not requested_range.startswith(prefix) or not requested_range.endswith("-"):
                    self.send_error(400)
                    return
                start = int(requested_range[len(prefix) : -1])
                body = self.package[start:]
                self.send_response(206)
                self.send_header(
                    "Content-Range",
                    f"bytes {start}-{len(self.package) - 1}/{len(self.package)}",
                )
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            cutoff = 96 * 1024
            self.send_response(200)
            self.send_header("Content-Length", str(len(self.package)))
            self.end_headers()
            self.wfile.write(self.package[:cutoff])
            self.wfile.flush()
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.connection.close()
            return

        self.send_error(404)

    def log_message(self, *_args: object) -> None:
        return


@unittest.skipUnless(os.name == "nt", "The maintenance installer is Windows-only.")
class InstallerResilientDownloadTests(unittest.TestCase):
    def setUp(self) -> None:
        _FlakyDownloadHandler.manifest_requests = 0
        _FlakyDownloadHandler.package_ranges = []

    def test_retries_manifest_and_resumes_interrupted_package(self) -> None:
        windows = Path(os.environ["WINDIR"])
        compiler = windows / "Microsoft.NET/Framework/v4.0.30319/csc.exe"
        if not compiler.is_file():
            self.skipTest("The .NET Framework C# compiler is unavailable.")

        server = ThreadingHTTPServer(("127.0.0.1", 0), _FlakyDownloadHandler)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        try:
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                harness = root / "Harness.cs"
                harness.write_text(
                    r'''
using System;
using System.IO;
using System.Security.Cryptography;

namespace MoSSLab.SchoolCSM.Installer
{
    internal static class BuildConfig
    {
        internal const string InstallerVersion = "test";
    }

    internal static class Harness
    {
        private static int Main(string[] args)
        {
            ResilientDownloader downloader = new ResilientDownloader(
                delegate(string message) { Console.WriteLine("STATUS=" + message); });
            byte[] manifest = downloader.DownloadBytes(args[0], 1024, "the release manifest");
            Console.WriteLine("MANIFEST=" + Convert.ToBase64String(manifest));
            downloader.DownloadFile(
                args[1], args[2], Int64.Parse(args[3]), "the application package");
            using (FileStream stream = File.OpenRead(args[2]))
            using (SHA256 algorithm = SHA256.Create())
            {
                Console.WriteLine(
                    "PACKAGE=" + BitConverter.ToString(algorithm.ComputeHash(stream)).Replace("-", String.Empty));
            }
            return 0;
        }
    }
}
'''.strip(),
                    encoding="utf-8",
                )
                executable = root / "download-harness.exe"
                compile_result = subprocess.run(
                    [
                        str(compiler),
                        "/nologo",
                        "/target:exe",
                        f"/out:{executable}",
                        "/reference:System.dll",
                        str(DOWNLOADER_SOURCE),
                        str(harness),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                self.assertEqual(
                    compile_result.returncode,
                    0,
                    compile_result.stdout + compile_result.stderr,
                )

                base_url = f"http://127.0.0.1:{server.server_port}"
                package_path = root / "package.zip"
                run_result = subprocess.run(
                    [
                        str(executable),
                        f"{base_url}/manifest",
                        f"{base_url}/package",
                        str(package_path),
                        str(len(_FlakyDownloadHandler.package)),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                self.assertEqual(
                    run_result.returncode,
                    0,
                    run_result.stdout + run_result.stderr,
                )
                self.assertEqual(package_path.read_bytes(), _FlakyDownloadHandler.package)
                expected_hash = hashlib.sha256(_FlakyDownloadHandler.package).hexdigest().upper()
                self.assertIn(f"PACKAGE={expected_hash}", run_result.stdout)
                self.assertGreaterEqual(_FlakyDownloadHandler.manifest_requests, 2)
                self.assertGreaterEqual(len(_FlakyDownloadHandler.package_ranges), 2)
                self.assertIsNone(_FlakyDownloadHandler.package_ranges[0])
                self.assertRegex(_FlakyDownloadHandler.package_ranges[1] or "", r"^bytes=\d+-$")
                self.assertIn("attempt 2 of 5", run_result.stdout)
        finally:
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
