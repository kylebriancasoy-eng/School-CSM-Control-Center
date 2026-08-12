"""Persistent settings for the School CSM Control Center local survey service."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import re
from typing import Any, Mapping

from school_csm_control_center.runtime_paths import storage_root_for
from school_csm_control_center.storage.file_safety import (
    InterProcessRLock,
    atomic_write_json,
    preserve_corrupt_file,
)


DEFAULT_SETTINGS: dict[str, Any] = {
    "school_name": "",
    "school_id": "",
    "school_region": "",
    "school_division": "",
    "school_district": "",
    "school_address": "",
    "school_email": "",
    "school_contact": "",
    "school_head": "",
    "csm_focal_person": "",
    "school_logo_path": "",
    "school_identifier": "school-csm",
    "survey_status": "offline",
    "status_message": "",
    "active_mode": "onsite",
    "survey_date": "",
    "preferred_port": 8080,
    "server_ip": "",
    "session_duration_seconds": 300,
    "network_mode": "hotspot",
    "hotspot_internet_mode": "survey_only",
    "access_mode": "captive_portal",
    "captive_portal_enabled": True,
    "network_ssid": "School-CSM",
    "network_security": "WPA",
    "network_password": "",
    "network_hidden": False,
    "public_access_key": "",
    "scanner_intake_enabled": True,
    "scanner_remote_enabled": True,
    "scanner_access_key": "",
    "scanner_store_preview": True,
    "scanner_processing_limit": 1,
    "scanner_session_inactivity_seconds": 1800,
    "scanner_session_max_seconds": 28800,
    "scanner_failed_login_limit": 5,
    "scanner_lockout_seconds": 900,
    "background_server_startup_enabled": False,
}

IDENTIFIER_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
SCHOOL_ID_PATTERN = re.compile(r"^\d{4,12}$")
RESERVED_IDENTIFIERS = {
    "admin", "api", "app", "backup", "dashboard", "dns", "gateway",
    "localhost", "maintenance", "network", "offline", "online", "router",
    "server", "settings", "status", "support", "system", "test", "www",
}


class ControlCenterSettingsStoreError(RuntimeError):
    pass


class ControlCenterSettingsStore:
    def __init__(
        self,
        project_root: str | Path,
        *,
        data_root: str | Path | None = None,
    ) -> None:
        self.install_root = Path(project_root)
        self.project_root = storage_root_for(project_root, data_root)
        self.path = self.project_root / "data" / "csm_survey" / "control_center_settings.json"
        self._lock = InterProcessRLock(self.path)
        self.last_read_error: str | None = None
        self.last_recovery_copy: Path | None = None

    def load(self) -> dict[str, Any]:
        with self._lock:
            self.last_read_error = None
            settings = deepcopy(DEFAULT_SETTINGS)
            if not self.path.is_file():
                return settings
            try:
                document = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                self._set_read_error(str(exc))
                return settings
            if not isinstance(document, Mapping):
                self._set_read_error("The Control Center settings file root is not an object.")
                return settings
            settings.update({str(k): deepcopy(v) for k, v in document.items() if k in settings})
            return settings

    def save(self, settings: Mapping[str, Any]) -> dict[str, Any]:
        merged = deepcopy(DEFAULT_SETTINGS)
        merged.update({str(k): deepcopy(v) for k, v in settings.items() if k in merged})
        validate_school_identifier(str(merged["school_identifier"]))
        merged["school_id"] = validate_school_id(merged.get("school_id"), required=False)
        merged["scanner_processing_limit"] = max(
            1, min(10, int(merged.get("scanner_processing_limit") or 1))
        )
        with self._lock:
            self.load()
            if self.path.exists() and self.last_read_error:
                raise ControlCenterSettingsStoreError(
                    "The existing settings file could not be read and was left unchanged. "
                    "A recovery copy was preserved."
                )
            try:
                atomic_write_json(self.path, merged)
            except (OSError, TypeError, ValueError) as exc:
                raise ControlCenterSettingsStoreError(
                    f"Unable to save Control Center settings to {self.path}: {exc}"
                ) from exc
        return deepcopy(merged)

    def _set_read_error(self, message: str) -> None:
        self.last_read_error = str(message)
        self.last_recovery_copy = preserve_corrupt_file(self.path)


def normalize_school_identifier(value: str) -> str:
    text = str(value or "").strip().casefold()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return re.sub(r"-+", "-", text).strip("-")


def validate_school_identifier(value: str) -> str:
    identifier = str(value or "").strip().casefold()
    if not IDENTIFIER_PATTERN.fullmatch(identifier):
        raise ValueError("Use lowercase letters, numbers, and internal hyphens only (maximum 63 characters).")
    if identifier in RESERVED_IDENTIFIERS:
        raise ValueError("That school address identifier is reserved by the system.")
    return identifier


def normalize_school_id(value: Any) -> str:
    """Return a compact School ID without silently changing its digits."""

    return re.sub(r"\s+", "", str(value or "").strip())


def validate_school_id(value: Any, *, required: bool = True) -> str:
    """Validate the official School ID used in response control numbers."""

    school_id = normalize_school_id(value)
    if not school_id:
        if required:
            raise ValueError(
                "School ID is required before CSM responses can be recorded. "
                "Open School Information and provide the official School ID."
            )
        return ""
    if not SCHOOL_ID_PATTERN.fullmatch(school_id):
        raise ValueError("School ID must contain 4 to 12 digits only.")
    return school_id
