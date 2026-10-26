"""Publish a separate, product-bound manifest for District Setup."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import re
import shutil
from urllib.parse import quote
import zipfile

try:
    from scripts.create_release import atomic_json, published_time, sha256
    from scripts.package_district_management_server import (
        ARCHIVE_PREFIX, ENTRY_POINT, RELEASE_TAG, VERSION_DATA, validate_compiled_directory,
    )
except ModuleNotFoundError:
    from create_release import atomic_json, published_time, sha256
    from package_district_management_server import (
        ARCHIVE_PREFIX, ENTRY_POINT, RELEASE_TAG, VERSION_DATA, validate_compiled_directory,
    )


APPLICATION_ID = "MoSSLab.SchoolCSMDistrictManagementServer"
PRODUCT_NAME = "School CSM District Management Server"
INSTALLER_NAME = "School-CSM-District-Management-Server-Setup.exe"
MANIFEST_NAME = "district-release.json"


def check_native_metadata(path: Path, expected_version: str | None = None) -> None:
    try:
        import pefile
        with pefile.PE(str(path)) as executable:
            info = executable.VS_FIXEDFILEINFO[0]
            version = ".".join(str(value) for value in (
                info.FileVersionMS >> 16, info.FileVersionMS & 65535,
                info.FileVersionLS >> 16, info.FileVersionLS & 65535,
            ))
            products = [table.entries.get(b"ProductName", b"").decode("utf-8")
                for group in executable.FileInfo for record in group
                if record.Key == b"StringFileInfo" for table in record.StringTable]
            if PRODUCT_NAME not in products or (expected_version is not None and version != expected_version):
                raise ValueError("Native product/version does not match the district release")
    except Exception as exc:
        raise ValueError(f"District executable metadata verification failed: {path.name}") from exc


def verify_archive(archive: Path, app: Path) -> None:
    files = {path.relative_to(app).as_posix(): path for path in app.rglob("*") if path.is_file()}
    with zipfile.ZipFile(archive) as package:
        names = package.namelist()
        if len(names) != len(set(names)) or set(names) != set(files):
            raise ValueError("District archive entries do not match the verified application")
        for name, path in files.items():
            digest = hashlib.sha256()
            with package.open(name) as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
            if digest.hexdigest().upper() != sha256(path):
                raise ValueError(f"District archive content does not match: {name}")


def create_manifest(app_dir: Path, archive: Path, installer: Path, output: Path,
                    repository: str, *, tag: str = RELEASE_TAG) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("Repository must be owner/repository")
    if tag != RELEASE_TAG or archive.name != f"{ARCHIVE_PREFIX}-{tag}-win-x64.zip":
        raise ValueError("District release tag or archive name does not match the current version")
    app = validate_compiled_directory(app_dir)
    if installer.name != INSTALLER_NAME or not installer.is_file() or installer.is_symlink():
        raise ValueError("A verified district setup executable is required")
    if not archive.is_file() or archive.is_symlink():
        raise ValueError("District archive is missing or redirected")
    check_native_metadata(app / ENTRY_POINT, str(VERSION_DATA["WINDOWS_FILE_VERSION_TEXT"]))
    check_native_metadata(installer)
    verify_archive(archive, app)
    output = output.resolve()
    if output == app or app in output.parents:
        raise ValueError("District release output must not be inside the application")
    output.mkdir(parents=True, exist_ok=True)
    setup_output = output / INSTALLER_NAME
    if installer.resolve() != setup_output:
        shutil.copy2(installer, setup_output)
    archive_output = output / archive.name
    if archive.resolve() != archive_output:
        shutil.copy2(archive, archive_output)
    base = f"https://github.com/{repository}/releases/download/{tag}"
    manifest = {
        "schemaVersion": 1, "applicationId": APPLICATION_ID,
        "version": str(VERSION_DATA["__version__"]),
        "windowsVersion": str(VERSION_DATA["WINDOWS_FILE_VERSION_TEXT"]),
        "tag": tag, "publishedAt": published_time(),
        "releasePage": f"https://github.com/{repository}/releases/tag/{tag}",
        "package": {"fileName": archive.name, "url": f"{base}/{quote(archive.name)}",
            "sha256": sha256(archive_output), "sizeBytes": archive_output.stat().st_size,
            "format": "zip", "entryPoint": ENTRY_POINT,
            "entryPointSha256": sha256(app / ENTRY_POINT)},
        "installer": {"fileName": INSTALLER_NAME, "url": f"{base}/{INSTALLER_NAME}",
            "sha256": sha256(setup_output), "sizeBytes": setup_output.stat().st_size},
    }
    destination = output / MANIFEST_NAME
    atomic_json(destination, manifest)
    (output / "District-SHA256SUMS.txt").write_text("".join(
        f"{sha256(path)}  {path.name}\n" for path in (archive_output, setup_output, destination)
    ), encoding="ascii", newline="\n")
    return destination


def main() -> int:
    parser = argparse.ArgumentParser()
    for argument in ("app-dir", "archive", "installer", "output"):
        parser.add_argument("--" + argument, type=Path, required=True)
    parser.add_argument("--repository", required=True)
    args = parser.parse_args()
    print(create_manifest(args.app_dir, args.archive, args.installer, args.output, args.repository))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
