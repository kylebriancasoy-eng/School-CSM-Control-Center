from __future__ import annotations

import importlib.util
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import zipfile


ROOT = Path(__file__).parents[1]


class CleanReleaseContractTests(unittest.TestCase):
    def test_release_archive_excludes_mutable_and_development_artifacts(self) -> None:
        path = ROOT / "tools" / "build_release.py"
        spec = importlib.util.spec_from_file_location("build_release", path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        with TemporaryDirectory() as temporary:
            archive, manifest = module.build_release(ROOT, Path(temporary))
            self.assertTrue(archive.is_file())
            self.assertTrue(manifest.is_file())
            with zipfile.ZipFile(archive) as bundle:
                names = bundle.namelist()
                prefix = f"{module.PACKAGE_NAME}/"
                embedded_name = f"{prefix}PACKAGE_MANIFEST.json"
                self.assertIn(embedded_name, names)
                embedded_bytes = bundle.read(embedded_name)
                embedded = json.loads(embedded_bytes.decode("utf-8"))
                sidecar_bytes = manifest.read_bytes()
                self.assertEqual(embedded_bytes, sidecar_bytes)
                self.assertEqual(embedded["schema_version"], 1)
                self.assertEqual(embedded["package"], module.PACKAGE_NAME)

                payload_names = {
                    name.removeprefix(prefix)
                    for name in names
                    if name.startswith(prefix)
                    and not name.endswith("/")
                    and name != embedded_name
                }
                declared_names = {entry["path"] for entry in embedded["files"]}
                self.assertEqual(payload_names, declared_names)
                self.assertEqual(embedded["file_count"], len(declared_names))
                for entry in embedded["files"]:
                    payload = bundle.read(f"{prefix}{entry['path']}")
                    self.assertEqual(len(payload), entry["bytes"], entry["path"])
                    self.assertEqual(
                        hashlib.sha256(payload).hexdigest(),
                        entry["sha256"],
                        entry["path"],
                    )

                forbidden = (
                    "/data/",
                    "/exports/",
                    "/logs/",
                    "/release/",
                    "/tests/",
                    "/tmp/",
                    "/.venv/",
                    "/__pycache__/",
                    "debug_log.txt",
                    "test_report_",
                )
                folded_names = [name.casefold() for name in names]
                for token in forbidden:
                    self.assertFalse(
                        any(token in name for name in folded_names),
                        token,
                    )


if __name__ == "__main__":
    unittest.main()
