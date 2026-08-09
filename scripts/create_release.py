"""Create deterministic, checksummed GitHub release assets."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import runpy
import shutil
import tempfile
import zipfile


REPO_ROOT = Path(__file__).resolve().parents[1]
VERSION_DATA = runpy.run_path(str(REPO_ROOT / "school_csm_control_center" / "version.py"))
APP_VERSION = str(VERSION_DATA["__version__"])
WINDOWS_VERSION = str(VERSION_DATA["WINDOWS_FILE_VERSION_TEXT"])
RELEASE_TAG = str(VERSION_DATA["RELEASE_TAG"])
APPLICATION_ID = "MoSSLab.SchoolCSMControlCenter"
ENTRY_POINT = "School CSM Control Center.exe"
INSTALLER_NAME = "School-CSM-Control-Center-Setup.exe"
REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
FORBIDDEN_RUNTIME_SUFFIXES = {
    ".py", ".pyw", ".pyc", ".pyo", ".cmd", ".bat", ".vbs", ".ps1", ".psm1", ".sh"
}
# OpenCV's Windows wheel deliberately loads these package-local configuration
# modules at runtime.  They are library internals, not application entry points,
# and PyInstaller cannot move them into its embedded module archive.
ALLOWED_OPENCV_RUNTIME_MODULES = {
    "cv2/__init__.py",
    "cv2/config-3.py",
    "cv2/config.py",
    "cv2/data/__init__.py",
    "cv2/gapi/__init__.py",
    "cv2/load_config_py3.py",
    "cv2/mat_wrapper/__init__.py",
    "cv2/misc/__init__.py",
    "cv2/misc/version.py",
    "cv2/typing/__init__.py",
    "cv2/utils/__init__.py",
    "cv2/version.py",
}


def is_forbidden_runtime_path(relative: PurePosixPath) -> bool:
    normalized = str(relative)
    return (
        relative.suffix.casefold() in FORBIDDEN_RUNTIME_SUFFIXES
        and normalized not in ALLOWED_OPENCV_RUNTIME_MODULES
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def zip_timestamp() -> tuple[int, int, int, int, int, int]:
    raw = os.environ.get("SOURCE_DATE_EPOCH")
    if raw:
        moment = datetime.fromtimestamp(int(raw), tz=timezone.utc)
        year = max(1980, moment.year)
        return (year, moment.month, moment.day, moment.hour, moment.minute, moment.second)
    return (2026, 1, 1, 0, 0, 0)


def write_deterministic_zip(source: Path, destination: Path) -> None:
    if not source.is_dir() or source.is_symlink():
        raise ValueError(f"Application directory is missing or redirected: {source}")
    entries = sorted(source.rglob("*"))
    redirected = [path for path in entries if path.is_symlink()]
    if redirected:
        raise ValueError(f"Application directory contains a symbolic link: {redirected[0]}")
    files = [path for path in entries if path.is_file()]
    if not files:
        raise ValueError(f"Application directory is empty: {source}")
    bad = [
        path
        for path in files
        if is_forbidden_runtime_path(PurePosixPath(path.relative_to(source).as_posix()))
    ]
    if bad:
        raise ValueError(f"End-user package contains development files: {bad[0]}")
    timestamp = zip_timestamp()
    with zipfile.ZipFile(
        destination,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
        allowZip64=True,
    ) as archive:
        for path in files:
            relative = PurePosixPath(path.relative_to(source).as_posix())
            info = zipfile.ZipInfo(str(relative), date_time=timestamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 0
            info.external_attr = 0o100644 << 16
            info.file_size = path.stat().st_size
            with path.open("rb") as source_handle:
                with archive.open(info, mode="w", force_zip64=True) as archive_handle:
                    shutil.copyfileobj(source_handle, archive_handle, length=1024 * 1024)


def published_time() -> str:
    raw = os.environ.get("SOURCE_DATE_EPOCH")
    if raw:
        moment = datetime.fromtimestamp(int(raw), tz=timezone.utc)
    else:
        moment = datetime.now(tz=timezone.utc)
    return moment.isoformat(timespec="seconds").replace("+00:00", "Z")


def atomic_json(path: Path, document: object) -> None:
    text = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", newline="\n", delete=False, dir=path.parent
    ) as handle:
        handle.write(text)
        temp_path = Path(handle.name)
    temp_path.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app-dir", required=True, type=Path)
    parser.add_argument("--installer", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--repository", required=True, help="GitHub owner/repository")
    parser.add_argument("--tag", default=RELEASE_TAG)
    args = parser.parse_args()

    if not REPOSITORY_PATTERN.fullmatch(args.repository):
        raise SystemExit("--repository must be in the form owner/repository")
    if args.tag != RELEASE_TAG:
        raise SystemExit(f"Release tag {args.tag!r} does not match application version {RELEASE_TAG!r}")

    app_dir = args.app_dir.resolve()
    installer_source = args.installer.resolve()
    output = args.output.resolve()
    try:
        output.relative_to(app_dir)
    except ValueError:
        pass
    else:
        raise SystemExit("--output must not be inside --app-dir")
    entry_point = app_dir / ENTRY_POINT
    if not entry_point.is_file() or entry_point.is_symlink():
        raise SystemExit(f"Missing compiled application entry point: {entry_point}")
    if not installer_source.is_file() or installer_source.is_symlink():
        raise SystemExit(f"Missing maintenance installer: {installer_source}")

    output.mkdir(parents=True, exist_ok=True)
    package_name = f"School-CSM-Control-Center-{APP_VERSION}-windows-x64.zip"
    package_path = output / package_name
    installer_path = output / INSTALLER_NAME
    write_deterministic_zip(app_dir, package_path)
    if installer_source != installer_path:
        shutil.copy2(installer_source, installer_path)

    asset_base = f"https://github.com/{args.repository}/releases/download/{args.tag}"
    release_page = f"https://github.com/{args.repository}/releases/tag/{args.tag}"
    manifest = {
        "schemaVersion": 1,
        "applicationId": APPLICATION_ID,
        "version": APP_VERSION,
        "windowsVersion": WINDOWS_VERSION,
        "tag": args.tag,
        "publishedAt": published_time(),
        "releasePage": release_page,
        "package": {
            "fileName": package_name,
            "url": f"{asset_base}/{package_name}",
            "sha256": sha256(package_path),
            "sizeBytes": package_path.stat().st_size,
            "format": "zip",
            "entryPoint": ENTRY_POINT,
            "entryPointSha256": sha256(entry_point),
        },
        "installer": {
            "fileName": INSTALLER_NAME,
            "url": f"{asset_base}/{INSTALLER_NAME}",
            "sha256": sha256(installer_path),
            "sizeBytes": installer_path.stat().st_size,
        },
    }
    manifest_path = output / "release.json"
    atomic_json(manifest_path, manifest)

    checksum_paths = [package_path, installer_path, manifest_path]
    checksums = "".join(f"{sha256(path)}  {path.name}\n" for path in checksum_paths)
    (output / "SHA256SUMS.txt").write_text(checksums, encoding="ascii", newline="\n")
    print(manifest_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
