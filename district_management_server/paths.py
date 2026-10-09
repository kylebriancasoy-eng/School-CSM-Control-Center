"""Stable machine-wide paths for the district management server."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Mapping


DATA_ROOT_ENVIRONMENT_VARIABLE = "SCHOOL_CSM_DISTRICT_DATA_ROOT"
PROGRAM_DATA_PARTS = ("MoSSLab", "School CSM District Management Server")


class RuntimePathError(RuntimeError):
    """Raised when the machine-wide runtime location cannot be resolved."""


@dataclass(frozen=True)
class DistrictRuntimePaths:
    """Immutable locations kept outside the replaceable application folder."""

    root: Path

    @property
    def configuration_dir(self) -> Path:
        return self.root / "Configuration"

    @property
    def configuration_file(self) -> Path:
        return self.configuration_dir / "server.json"

    @property
    def data_dir(self) -> Path:
        return self.root / "Data"

    @property
    def database_file(self) -> Path:
        return self.data_dir / "registration.sqlite3"

    @property
    def logs_dir(self) -> Path:
        return self.root / "Logs"

    @property
    def log_file(self) -> Path:
        return self.logs_dir / "district-management-server.log"

    @property
    def locks_dir(self) -> Path:
        return self.root / "Locks"

    @property
    def single_instance_lock(self) -> Path:
        return self.locks_dir / "district-management-server.lock"

    @property
    def backups_dir(self) -> Path:
        return self.root / "Backups"

    @property
    def deployment_dir(self) -> Path:
        return self.root / "School Deployment"

    @property
    def provider_export_file(self) -> Path:
        return self.deployment_dir / "internet_gateway_provider.json"

    def ensure_directories(self) -> None:
        for directory in (
            self.root,
            self.configuration_dir,
            self.data_dir,
            self.logs_dir,
            self.locks_dir,
            self.backups_dir,
            self.deployment_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)


def resolve_runtime_paths(
    override: str | Path | None = None,
    *,
    environment: Mapping[str, str] | None = None,
) -> DistrictRuntimePaths:
    """Resolve the ProgramData root, with an explicit test/deployment override.

    Installed Windows builds default to ``%ProgramData%``.  The environment
    override is intentionally application-specific and is useful for automated
    tests and managed deployments; it never changes the school application's
    data root.
    """

    values = os.environ if environment is None else environment
    candidate = override or values.get(DATA_ROOT_ENVIRONMENT_VARIABLE, "")
    if candidate:
        root = Path(candidate).expanduser()
    else:
        program_data = str(values.get("PROGRAMDATA", "")).strip()
        if os.name == "nt" and not program_data:
            raise RuntimePathError("Windows ProgramData could not be resolved.")
        if program_data:
            root = Path(program_data).joinpath(*PROGRAM_DATA_PARTS)
        else:
            # Source development on non-Windows systems remains possible.  A
            # packaged Windows build never reaches this fallback.
            root = Path.home() / ".local" / "share" / Path(*PROGRAM_DATA_PARTS)
    return DistrictRuntimePaths(root=root.resolve())
