"""Build a clean, hash-manifested School CSM Control Center release archive."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import runpy
import shutil
import tempfile
import zipfile


_VERSION = str(
    runpy.run_path(
        Path(__file__).resolve().parents[1]
        / "school_csm_control_center"
        / "version.py"
    )["__version__"]
)
PACKAGE_NAME = f"School_CSM_Control_Center_v{_VERSION}_Source"
EXCLUDED_DIRECTORIES = {
    ".git",
    ".locks",
    ".venv",
    "__pycache__",
    "backups",
    "data",
    "dist",
    "exports",
    "logs",
    "migration",
    "release",
    "recovery",
    "scanner_jobs",
    "scanner_trash",
    "tests",
    "tmp",
}
EXCLUDED_FILES = {
    "DEBUG_LOG.txt",
    "MRS_FIELD_TEST_REPORT.txt",
    "PACKAGE_MANIFEST.json",
}
EXCLUDED_PREFIXES = ("TEST_REPORT_",)
EXCLUDED_SUFFIXES = (".pyc", ".pyo", ".tmp")


def _included_files(source_root: Path):
    for path in sorted(item for item in source_root.rglob("*") if item.is_file()):
        relative = path.relative_to(source_root)
        if any(part.casefold() in EXCLUDED_DIRECTORIES for part in relative.parts):
            continue
        if relative.name.casefold() in {
            item.casefold() for item in EXCLUDED_FILES
        }:
            continue
        if relative.name.casefold().startswith(
            tuple(item.casefold() for item in EXCLUDED_PREFIXES)
        ):
            continue
        if relative.suffix.casefold() in EXCLUDED_SUFFIXES:
            continue
        if path.is_symlink():
            raise ValueError(f"Release sources must not be symbolic links: {relative}")
        yield path, relative


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_release(source_root: Path, output_directory: Path) -> tuple[Path, Path]:
    source_root = source_root.resolve()
    output_directory = output_directory.resolve()
    if not source_root.is_dir():
        raise FileNotFoundError(f"Release source directory does not exist: {source_root}")
    if output_directory == source_root:
        raise ValueError("Release output directory must not be the source directory.")
    if output_directory.is_relative_to(source_root):
        relative_output = output_directory.relative_to(source_root)
        if not relative_output.parts or (
            relative_output.parts[0].casefold() not in EXCLUDED_DIRECTORIES
        ):
            raise ValueError(
                "A release output inside the source must be under an excluded directory."
            )
    output_directory.mkdir(parents=True, exist_ok=True)
    archive = output_directory / f"{PACKAGE_NAME}.zip"
    manifest_path = output_directory / f"{PACKAGE_NAME}.manifest.json"
    entries = []

    with tempfile.TemporaryDirectory(prefix="school-csm-release-") as temporary:
        package_root = Path(temporary) / PACKAGE_NAME
        for source, relative in _included_files(source_root):
            destination = package_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            entries.append(
                {
                    "path": relative.as_posix(),
                    "bytes": destination.stat().st_size,
                    "sha256": _sha256(destination),
                }
            )
        manifest = {
            "schema_version": 1,
            "package": PACKAGE_NAME,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "file_count": len(entries),
            "files": entries,
        }
        manifest_text = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
        embedded_manifest = package_root / "PACKAGE_MANIFEST.json"
        embedded_manifest.write_text(manifest_text, encoding="utf-8")
        temporary_manifest = output_directory / f".{manifest_path.name}.tmp"
        temporary_manifest.write_text(manifest_text, encoding="utf-8")
        temporary_archive = output_directory / f".{archive.name}.tmp"
        try:
            with zipfile.ZipFile(
                temporary_archive,
                "w",
                compression=zipfile.ZIP_DEFLATED,
                compresslevel=9,
            ) as bundle:
                for path in sorted(
                    item for item in package_root.rglob("*") if item.is_file()
                ):
                    bundle.write(path, path.relative_to(package_root.parent))
            temporary_archive.replace(archive)
            temporary_manifest.replace(manifest_path)
        finally:
            temporary_archive.unlink(missing_ok=True)
            temporary_manifest.unlink(missing_ok=True)
    return archive, manifest_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "dist",
    )
    arguments = parser.parse_args()
    archive, manifest = build_release(arguments.source, arguments.output)
    print(archive)
    print(manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
