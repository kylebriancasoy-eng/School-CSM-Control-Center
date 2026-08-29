"""Verify a locally assembled release before it is uploaded."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import runpy
import stat
from urllib.parse import quote, urlparse
import zipfile


SHA256_PATTERN = re.compile(r"^[A-Fa-f0-9]{64}$")
APPLICATION_ID = "MoSSLab.SchoolCSMControlCenter"
INSTALLER_NAME = "School-CSM-Control-Center-Setup.exe"
CLOUDFLARED_ENTRY = PurePosixPath("vendor/cloudflared/cloudflared.exe")
PROVIDER_CONFIG_ENTRY = PurePosixPath("internet_gateway_provider.json")
MAXIMUM_ARCHIVE_ENTRIES = 200_000
MAXIMUM_EXTRACTED_BYTES = 4 * 1024 * 1024 * 1024
VERSION_DATA = runpy.run_path(
    str(Path(__file__).resolve().parents[1] / "school_csm_control_center" / "version.py")
)
EXPECTED_VERSION = str(VERSION_DATA["__version__"])
EXPECTED_WINDOWS_VERSION = str(VERSION_DATA["WINDOWS_FILE_VERSION_TEXT"])
EXPECTED_TAG = str(VERSION_DATA["RELEASE_TAG"])
TOP_LEVEL_KEYS = {
    "schemaVersion", "applicationId", "version", "windowsVersion", "tag",
    "publishedAt", "releasePage", "package", "installer",
}
BASE_DOWNLOAD_KEYS = {"fileName", "url", "sha256", "sizeBytes"}
FORBIDDEN_RUNTIME_SUFFIXES = {
    ".py", ".pyw", ".pyc", ".pyo", ".cmd", ".bat", ".vbs", ".ps1", ".psm1", ".sh"
}
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
    return (
        relative.suffix.casefold() in FORBIDDEN_RUNTIME_SUFFIXES
        and str(relative) not in ALLOWED_OPENCV_RUNTIME_MODULES
    )


def sha256_stream(handle) -> str:
    digest = hashlib.sha256()
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
        digest.update(chunk)
    return digest.hexdigest().upper()


def sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return sha256_stream(handle)


def require_asset(directory: Path, descriptor: dict) -> Path:
    name = str(descriptor.get("fileName") or "")
    if not name or Path(name).name != name:
        raise ValueError(f"Unsafe asset file name: {name!r}")
    expected = str(descriptor.get("sha256") or "")
    if not SHA256_PATTERN.fullmatch(expected):
        raise ValueError(f"Invalid SHA-256 for {name}")
    path = directory / name
    if not path.is_file():
        raise ValueError(f"Missing release asset: {path}")
    expected_size = descriptor.get("sizeBytes")
    if isinstance(expected_size, bool) or not isinstance(expected_size, int) or expected_size <= 0:
        raise ValueError(f"Invalid size metadata for {name}")
    if path.stat().st_size != expected_size:
        raise ValueError(f"Size mismatch for {name}")
    if sha256(path) != expected.upper():
        raise ValueError(f"SHA-256 mismatch for {name}")
    if not str(descriptor.get("url") or "").startswith("https://"):
        raise ValueError(f"Release URL is not HTTPS for {name}")
    return path


def validate_manifest(manifest: object) -> tuple[dict, str]:
    if not isinstance(manifest, dict) or set(manifest) != TOP_LEVEL_KEYS:
        raise ValueError("The release manifest has missing or unexpected fields")
    if manifest.get("schemaVersion") != 1:
        raise ValueError("Unsupported release schema")
    if manifest.get("applicationId") != APPLICATION_ID:
        raise ValueError("Unexpected application ID")
    if manifest.get("version") != EXPECTED_VERSION:
        raise ValueError("Manifest version does not match the repository version")
    if manifest.get("windowsVersion") != EXPECTED_WINDOWS_VERSION:
        raise ValueError("Manifest Windows version does not match the repository version")
    if manifest.get("tag") != EXPECTED_TAG:
        raise ValueError("Manifest tag does not match the repository version")

    release_page = urlparse(str(manifest.get("releasePage") or ""))
    page_parts = release_page.path.strip("/").split("/")
    if (release_page.scheme != "https" or release_page.hostname != "github.com" or
            release_page.username is not None or release_page.password is not None or
            release_page.port not in {None, 443} or release_page.query or
            release_page.fragment or len(page_parts) != 5 or
            page_parts[2:4] != ["releases", "tag"] or page_parts[4] != EXPECTED_TAG):
        raise ValueError("The release page is not a matching GitHub tag URL")
    repository = "/".join(page_parts[:2])
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("The release repository name is invalid")

    for key, required_keys in (("package", {"format", "entryPoint", "entryPointSha256"}), ("installer", set())):
        descriptor = manifest.get(key)
        if not isinstance(descriptor, dict):
            raise ValueError(f"Manifest {key} descriptor is invalid")
        if set(descriptor) != BASE_DOWNLOAD_KEYS | required_keys:
            raise ValueError(f"Manifest {key} descriptor has missing or unexpected fields")
        name = str(descriptor.get("fileName") or "")
        parsed = urlparse(str(descriptor.get("url") or ""))
        expected_path = f"/{repository}/releases/download/{EXPECTED_TAG}/{quote(name, safe='')}"
        if (parsed.scheme != "https" or parsed.hostname != "github.com" or
                parsed.username is not None or parsed.password is not None or
                parsed.port not in {None, 443} or parsed.query or parsed.fragment or
                parsed.path != expected_path):
            raise ValueError(f"Manifest {key} URL does not match the GitHub release")

    package = manifest["package"]
    if package.get("format") != "zip":
        raise ValueError("Application package format must be zip")
    if package.get("entryPoint") != "School CSM Control Center.exe":
        raise ValueError("Application package entry point is invalid")
    if manifest["installer"].get("fileName") != INSTALLER_NAME:
        raise ValueError("Installer file name is invalid")
    return manifest, repository


def normalized_zip_name(info: zipfile.ZipInfo) -> str:
    name = info.filename
    if not name or "\\" in name or "\x00" in name:
        raise ValueError(f"Invalid ZIP entry: {name!r}")
    trimmed = name.rstrip("/")
    pure = PurePosixPath(trimmed)
    if (not trimmed or pure.is_absolute() or ":" in trimmed or
            any(part in {"", ".", ".."} for part in trimmed.split("/"))):
        raise ValueError(f"Unsafe ZIP entry: {name}")
    if any(part.endswith((" ", ".")) for part in pure.parts):
        raise ValueError(f"Windows-ambiguous ZIP entry: {name}")
    reserved = {"CON", "PRN", "AUX", "NUL"}
    for part in pure.parts:
        stem = part.split(".", 1)[0].upper()
        if stem in reserved or (len(stem) == 4 and stem[:3] in {"COM", "LPT"} and stem[3] in "123456789"):
            raise ValueError(f"Reserved Windows ZIP entry: {name}")
    mode = (info.external_attr >> 16) & 0xFFFF
    if stat.S_ISLNK(mode) or info.external_attr & 0x0400:
        raise ValueError(f"Symbolic-link or reparse-point ZIP entry: {name}")
    return "/".join(pure.parts)


def verify_package(package_path: Path, descriptor: dict) -> None:
    entry_point = str(descriptor.get("entryPoint") or "")
    expected_entry_hash = str(descriptor.get("entryPointSha256") or "")
    if not entry_point or not SHA256_PATTERN.fullmatch(expected_entry_hash):
        raise ValueError("Package entry-point metadata is invalid")
    with zipfile.ZipFile(package_path) as archive:
        if len(archive.infolist()) > MAXIMUM_ARCHIVE_ENTRIES:
            raise ValueError("The package contains unexpectedly many entries")
        names: set[str] = set()
        extracted_size = 0
        for info in archive.infolist():
            name = normalized_zip_name(info)
            collision_key = name.casefold()
            if collision_key in names:
                raise ValueError(f"Duplicate or Windows-colliding ZIP entry: {name}")
            names.add(collision_key)
            extracted_size += info.file_size
            if info.file_size < 0 or extracted_size > MAXIMUM_EXTRACTED_BYTES:
                raise ValueError("The package expands beyond the supported size")
            pure = PurePosixPath(name)
            if str(pure).casefold() == str(PROVIDER_CONFIG_ENTRY).casefold():
                raise ValueError(
                    "Production Internet Gateway provider configuration in public release"
                )
            if is_forbidden_runtime_path(pure):
                raise ValueError(f"Development launch file in release: {name}")
        if entry_point.casefold() not in names:
            raise ValueError(f"Package is missing entry point: {entry_point}")
        if str(CLOUDFLARED_ENTRY).casefold() not in names:
            raise ValueError(
                "Package is missing the verified Internet Gateway tunnel component"
            )
        with archive.open(entry_point) as handle:
            if sha256_stream(handle) != expected_entry_hash.upper():
                raise ValueError("Packaged entry-point SHA-256 does not match the manifest")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("release_directory", type=Path)
    args = parser.parse_args()
    directory = args.release_directory.resolve()
    manifest_path = directory / "release.json"
    manifest, _repository = validate_manifest(json.loads(manifest_path.read_text(encoding="utf-8")))
    package = require_asset(directory, manifest["package"])
    require_asset(directory, manifest["installer"])
    verify_package(package, manifest["package"])

    expected_lines = {
        f"{sha256(path)}  {path.name}"
        for path in (package, directory / manifest["installer"]["fileName"], manifest_path)
    }
    actual_lines = {
        line.strip() for line in (directory / "SHA256SUMS.txt").read_text(encoding="ascii").splitlines()
        if line.strip()
    }
    if actual_lines != expected_lines:
        raise SystemExit("SHA256SUMS.txt does not match the release assets")
    print(f"Verified release {manifest['version']} in {directory}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
