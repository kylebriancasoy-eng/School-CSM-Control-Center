"""Create the deterministic, checksummed District Management Server archive."""

from __future__ import annotations

import argparse
from pathlib import Path, PurePosixPath
import runpy

try:
    from scripts.create_release import sha256, write_deterministic_zip
except ModuleNotFoundError:  # Direct execution: python scripts/package_....py
    from create_release import sha256, write_deterministic_zip


REPO_ROOT = Path(__file__).resolve().parents[1]
VERSION_DATA = runpy.run_path(
    str(REPO_ROOT / "school_csm_control_center" / "version.py")
)
RELEASE_TAG = str(VERSION_DATA["RELEASE_TAG"])
ENTRY_POINT = "School CSM District Management Server.exe"
ARCHIVE_PREFIX = "School-CSM-District-Management-Server"
CLOUDFLARED_ENTRY = PurePosixPath("vendor/cloudflared/cloudflared.exe")
CLOUDFLARED_WINDOWS_AMD64_SHA256 = (
    "20b9638f685333d623798e733effbad2487093f15ba592f6c7752360ff3b7ab7"
)
REQUIRED_RUNTIME_ASSETS = (
    PurePosixPath("district_gateway_worker/src/index.js"),
    PurePosixPath("cloudflare_worker/src/index.js"),
    PurePosixPath("registration_service/static/manage.html"),
    PurePosixPath("registration_service/static/manage.js"),
    PurePosixPath("registration_service/static/manage.css"),
    PurePosixPath("registration_service/static/district.html"),
    PurePosixPath("registration_service/static/district.js"),
    PurePosixPath("registration_service/static/district.css"),
    PurePosixPath("registration_service/static/district-seal.png"),
)
FORBIDDEN_TOP_LEVEL_DIRECTORIES = {
    "backups",
    "school deployment",
    "maintenance",
    "configuration",
    "data",
    "locks",
    "logs",
}
FORBIDDEN_NAMES = {
    ".env",
    "server.json",
    "registration.sqlite3",
    "registration.sqlite3-shm",
    "registration.sqlite3-wal",
}
FORBIDDEN_SUFFIXES = {
    ".mossdistrict",
    ".db",
    ".db-shm",
    ".db-wal",
    ".log",
    ".sqlite",
    ".sqlite-shm",
    ".sqlite-wal",
    ".sqlite3",
    ".sqlite3-shm",
    ".sqlite3-wal",
}
FORBIDDEN_SOURCE_SUFFIXES = {".py", ".pyw", ".pyc", ".pyo", ".cmd", ".bat", ".ps1", ".vbs", ".sh"}


def _native_path(root: Path, relative: PurePosixPath) -> Path:
    return root.joinpath(*relative.parts)


def _require_file(app_dir: Path, relative: PurePosixPath) -> Path:
    path = _native_path(app_dir, relative)
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"District release is missing a required component: {relative}")
    return path


def _validate_required_components(app_dir: Path) -> None:
    cloudflared = _require_file(app_dir, CLOUDFLARED_ENTRY)
    if sha256(cloudflared).casefold() != CLOUDFLARED_WINDOWS_AMD64_SHA256.casefold():
        raise ValueError(
            f"District release component failed its integrity check: {CLOUDFLARED_ENTRY}"
        )

    for relative in REQUIRED_RUNTIME_ASSETS:
        source = _native_path(REPO_ROOT, relative)
        if not source.is_file() or source.is_symlink():
            raise ValueError(f"Required district release source asset is missing: {relative}")
        bundled = _require_file(app_dir, relative)
        if sha256(bundled) != sha256(source):
            raise ValueError(
                f"District release component failed its integrity check: {relative}"
            )


def validate_compiled_directory(app_dir: Path) -> Path:
    app_dir = app_dir.resolve()
    if not app_dir.is_dir() or app_dir.is_symlink():
        raise ValueError(f"District application directory is missing or redirected: {app_dir}")
    entry_point = app_dir / ENTRY_POINT
    if not entry_point.is_file() or entry_point.is_symlink():
        raise ValueError(f"Missing compiled district entry point: {entry_point}")

    for path in sorted(app_dir.rglob("*")):
        relative = PurePosixPath(path.relative_to(app_dir).as_posix())
        if path.is_symlink():
            raise ValueError(f"District application directory contains a symbolic link: {relative}")
        if not path.is_file():
            continue
        folded_parts = tuple(part.casefold() for part in relative.parts)
        folded_name = relative.name.casefold()
        if folded_parts and folded_parts[0] in FORBIDDEN_TOP_LEVEL_DIRECTORIES:
            raise ValueError(f"District release contains runtime state: {relative}")
        if folded_name in FORBIDDEN_NAMES or any(
            folded_name.endswith(suffix) for suffix in FORBIDDEN_SUFFIXES
        ):
            raise ValueError(f"District release contains runtime state: {relative}")
        if relative.suffix.casefold() in FORBIDDEN_SOURCE_SUFFIXES:
            raise ValueError(f"District release contains a development launcher: {relative}")
    _validate_required_components(app_dir)
    return app_dir


def package_district_server(
    app_dir: Path,
    output_dir: Path,
    *,
    tag: str = RELEASE_TAG,
) -> tuple[Path, Path]:
    if tag != RELEASE_TAG:
        raise ValueError(
            f"Release tag {tag!r} does not match application version {RELEASE_TAG!r}"
        )
    source = validate_compiled_directory(app_dir)
    output = output_dir.resolve()
    try:
        output.relative_to(source)
    except ValueError:
        pass
    else:
        raise ValueError("District release output must not be inside the application directory")
    output.mkdir(parents=True, exist_ok=True)

    archive = output / f"{ARCHIVE_PREFIX}-{tag}-win-x64.zip"
    checksum = output / f"{archive.name}.sha256"
    write_deterministic_zip(source, archive)
    checksum.write_text(
        f"{sha256(archive)}  {archive.name}\n",
        encoding="ascii",
        newline="\n",
    )
    return archive, checksum


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--tag", default=RELEASE_TAG)
    args = parser.parse_args()
    try:
        archive, checksum = package_district_server(
            args.app_dir,
            args.output,
            tag=args.tag,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(archive)
    print(checksum)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
