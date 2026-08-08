"""Stable per-user runtime paths and one-time legacy data migration.

The application installation may be replaced by a newer build at any time.
Mutable records therefore live below the user's Documents known folder instead
of beside the Python sources. Tests and embedders can inject a different data
root without changing global environment state.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from threading import RLock
from typing import Any
from uuid import UUID


APP_DATA_FOLDER = ("MoSSLab Data", "School CSM Control Center")
DATA_ROOT_ENVIRONMENT_VARIABLE = "SCHOOL_CSM_DATA_ROOT"
SHARED_DATA_ROOT_ENVIRONMENT_VARIABLE = "MOSS_DATA_ROOT"
MIGRATION_VERSION = 1


@dataclass(frozen=True)
class RuntimePaths:
    """Resolved immutable installation and mutable runtime locations."""

    install_root: Path
    data_root: Path

    @property
    def csm_data_dir(self) -> Path:
        return self.data_root / "data" / "csm_survey"

    @property
    def logs_dir(self) -> Path:
        return self.data_root / "logs"

    @property
    def log_file(self) -> Path:
        return self.logs_dir / "control-center.log"

    @property
    def exports_dir(self) -> Path:
        return self.data_root / "exports"

    @property
    def backups_dir(self) -> Path:
        return self.data_root / "backups"

    @property
    def migration_dir(self) -> Path:
        return self.data_root / "migration"

    @property
    def migration_manifest(self) -> Path:
        return self.migration_dir / f"legacy-migration-v{MIGRATION_VERSION}.json"

    @property
    def locks_dir(self) -> Path:
        return self.data_root / "locks"

    @property
    def single_instance_lock(self) -> Path:
        return self.locks_dir / "control-center.lock"

    @property
    def scanner_jobs_dir(self) -> Path:
        return self.csm_data_dir / "scanner_jobs"

    @property
    def scanner_trash_dir(self) -> Path:
        return self.csm_data_dir / "scanner_trash"

    def ensure_directories(self) -> None:
        for directory in (
            self.data_root,
            self.csm_data_dir,
            self.logs_dir,
            self.exports_dir,
            self.backups_dir,
            self.migration_dir,
            self.locks_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class MigrationResult:
    manifest_path: Path
    copied_files: int
    existing_files: int
    conflicts: tuple[str, ...]
    source_fingerprint: str
    already_completed: bool = False


_CONFIGURATION_LOCK = RLock()
_CONFIGURED_PATHS: RuntimePaths | None = None


def windows_documents_directory() -> Path:
    """Return the Windows Documents known folder, including redirected paths."""

    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            class _GUID(ctypes.Structure):
                _fields_ = (
                    ("Data1", wintypes.DWORD),
                    ("Data2", wintypes.WORD),
                    ("Data3", wintypes.WORD),
                    ("Data4", ctypes.c_ubyte * 8),
                )

            guid_bytes = UUID("FDD39AD0-238F-46AF-ADB4-6C85480369C7").bytes_le
            folder_id = _GUID.from_buffer_copy(guid_bytes)
            result_path = ctypes.c_wchar_p()
            shell32 = ctypes.windll.shell32
            shell32.SHGetKnownFolderPath.argtypes = (
                ctypes.POINTER(_GUID),
                wintypes.DWORD,
                wintypes.HANDLE,
                ctypes.POINTER(ctypes.c_wchar_p),
            )
            shell32.SHGetKnownFolderPath.restype = ctypes.c_long
            result = shell32.SHGetKnownFolderPath(
                ctypes.byref(folder_id),
                0,
                None,
                ctypes.byref(result_path),
            )
            if result == 0 and result_path.value:
                resolved = Path(result_path.value)
                ctypes.windll.ole32.CoTaskMemFree(result_path)
                return resolved
        except Exception:
            pass
    return Path.home() / "Documents"


def resolve_runtime_paths(
    install_root: str | Path,
    data_root_override: str | Path | None = None,
) -> RuntimePaths:
    """Resolve stable runtime paths without changing process-global state."""

    install = Path(install_root).expanduser().resolve()
    override = data_root_override
    if override is None:
        override = os.environ.get(DATA_ROOT_ENVIRONMENT_VARIABLE) or None
    if override is not None:
        # An explicit application override is useful for tests and managed
        # deployments that assign this program a dedicated location.
        data = Path(override).expanduser()
    else:
        shared_override = os.environ.get(SHARED_DATA_ROOT_ENVIRONMENT_VARIABLE) or None
        if shared_override:
            data = Path(shared_override).expanduser() / APP_DATA_FOLDER[-1]
        else:
            data = windows_documents_directory().joinpath(*APP_DATA_FOLDER)
    return RuntimePaths(install_root=install, data_root=data.resolve())


def configure_runtime_paths(
    install_root: str | Path,
    data_root_override: str | Path | None = None,
) -> RuntimePaths:
    """Configure runtime paths for constructors reached through the desktop UI."""

    paths = resolve_runtime_paths(install_root, data_root_override)
    paths.ensure_directories()
    global _CONFIGURED_PATHS
    with _CONFIGURATION_LOCK:
        _CONFIGURED_PATHS = paths
    return paths


def configured_runtime_paths() -> RuntimePaths | None:
    with _CONFIGURATION_LOCK:
        return _CONFIGURED_PATHS


def storage_root_for(
    project_root: str | Path,
    data_root_override: str | Path | None = None,
) -> Path:
    """Return the storage root while preserving explicit temporary test roots.

    The normal desktop entry point configures the installation root once.
    Directly constructed stores, as used by tests and utilities, retain the
    historical ``<passed root>/data/csm_survey`` behavior.
    """

    if data_root_override is not None:
        return Path(data_root_override).expanduser().resolve()
    candidate = Path(project_root).expanduser().resolve()
    paths = configured_runtime_paths()
    if paths is not None and candidate in {paths.install_root, paths.data_root}:
        return paths.data_root
    return candidate


def migrate_legacy_data(
    paths: RuntimePaths,
    *,
    source_root: str | Path | None = None,
) -> MigrationResult:
    """Copy legacy build data into the stable root with verified SHA-256 hashes.

    Existing destination files are never overwritten. A conflict is recorded
    in the manifest so an operator or support technician can reconcile it
    explicitly. The source build remains unchanged.
    """

    paths.ensure_directories()
    source = Path(source_root).expanduser().resolve() if source_root else paths.install_root
    sources = _legacy_sources(source, paths)
    fingerprint = _source_fingerprint(sources)
    existing_manifest = _read_manifest(paths.migration_manifest)
    if (
        existing_manifest
        and bool(existing_manifest.get("completed"))
        and int(existing_manifest.get("migration_version") or 0) == MIGRATION_VERSION
        and str(existing_manifest.get("source_fingerprint") or "") == fingerprint
        and str(existing_manifest.get("source_root") or "") == str(source)
    ):
        return MigrationResult(
            manifest_path=paths.migration_manifest,
            copied_files=int(existing_manifest.get("copied_files") or 0),
            existing_files=int(existing_manifest.get("existing_files") or 0),
            conflicts=tuple(str(value) for value in existing_manifest.get("conflicts") or ()),
            source_fingerprint=str(existing_manifest.get("source_fingerprint") or ""),
            already_completed=True,
        )

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_root = paths.backups_dir / "migration" / f"{timestamp}-{source.name}"
    copied = 0
    existing = 0
    conflicts: list[str] = []
    entries: list[dict[str, Any]] = []

    for source_path, destination, logical_name in sources:
        source_hash = _sha256(source_path)
        backup_path = backup_root / logical_name
        _copy_verified(source_path, backup_path, source_hash)
        action = "copied"
        if destination.exists():
            destination_hash = _sha256(destination)
            if destination_hash == source_hash:
                action = "already_present"
                existing += 1
            else:
                action = "conflict_preserved_destination"
                conflicts.append(logical_name.as_posix())
        else:
            _copy_verified(source_path, destination, source_hash)
            destination_hash = source_hash
            copied += 1
        entries.append(
            {
                "logical_path": logical_name.as_posix(),
                "source_path": str(source_path),
                "destination_path": str(destination),
                "backup_path": str(backup_path),
                "sha256": source_hash,
                "destination_sha256": destination_hash,
                "bytes": source_path.stat().st_size,
                "action": action,
            }
        )

    manifest = {
        "migration_version": MIGRATION_VERSION,
        "completed": True,
        "completed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_root": str(source),
        "destination_root": str(paths.data_root),
        "source_fingerprint": fingerprint,
        "copied_files": copied,
        "existing_files": existing,
        "conflicts": conflicts,
        "backup_root": str(backup_root),
        "files": entries,
    }
    _write_json_atomic(paths.migration_manifest, manifest)
    return MigrationResult(
        manifest_path=paths.migration_manifest,
        copied_files=copied,
        existing_files=existing,
        conflicts=tuple(conflicts),
        source_fingerprint=fingerprint,
    )


def _legacy_sources(
    source_root: Path,
    paths: RuntimePaths,
) -> list[tuple[Path, Path, Path]]:
    result: list[tuple[Path, Path, Path]] = []
    mappings = (
        (source_root / "data" / "csm_survey", paths.csm_data_dir, Path("data/csm_survey")),
        (source_root / "exports", paths.exports_dir, Path("exports")),
    )
    ignored_parts = {".locks", "__pycache__"}
    for source_directory, destination_directory, logical_prefix in mappings:
        if not source_directory.is_dir():
            continue
        for source_path in sorted(path for path in source_directory.rglob("*") if path.is_file()):
            relative = source_path.relative_to(source_directory)
            if any(part in ignored_parts for part in relative.parts):
                continue
            if source_path.suffix.casefold() in {".tmp", ".pyc"}:
                continue
            result.append(
                (
                    source_path,
                    destination_directory / relative,
                    logical_prefix / relative,
                )
            )
    return result


def _source_fingerprint(
    sources: list[tuple[Path, Path, Path]],
) -> str:
    digest = hashlib.sha256()
    for source_path, _destination, logical_name in sources:
        digest.update(logical_name.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(_sha256(source_path).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_verified(source: Path, destination: Path, expected_hash: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            with source.open("rb") as source_handle:
                shutil.copyfileobj(source_handle, temporary, length=1024 * 1024)
            temporary.flush()
            os.fsync(temporary.fileno())
        if _sha256(temporary_path) != expected_hash:
            raise OSError(f"Hash verification failed while copying {source}.")
        os.replace(temporary_path, destination)
        try:
            shutil.copystat(source, destination)
        except OSError:
            pass
    finally:
        if temporary_path is not None and temporary_path.exists():
            try:
                temporary_path.unlink()
            except OSError:
                pass


def _read_manifest(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(value, temporary, ensure_ascii=False, indent=2)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            try:
                temporary_path.unlink()
            except OSError:
                pass
