"""Persistent settings for the School CSM Control Center local survey service."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import re
from typing import Any, Mapping

from PIL import Image

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
    "school_division": "Schools Division of Samar",
    "school_district": "Schools District of Motiong",
    "school_address": "",
    "school_email": "",
    "school_contact": "",
    "school_head": "",
    "school_administrator": "",
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

DEFAULT_SCHOOL_DISTRICT = "Schools District of Motiong"
DEFAULT_SCHOOL_DIVISION = "Schools Division of Samar"

SCHOOL_REGISTRATION_SECTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "School identity",
        (
            "school_id",
            "school_name",
            "school_district",
            "school_division",
            "school_logo_path",
        ),
    ),
    (
        "Essential personnel",
        ("school_head", "school_administrator", "csm_focal_person"),
    ),
    (
        "Contact details",
        ("school_address", "school_email", "school_contact"),
    ),
)

SCHOOL_REGISTRATION_FIELD_LABELS = {
    "school_id": "School ID",
    "school_name": "School Name",
    "school_district": "Schools District",
    "school_division": "Schools Division",
    "school_logo_path": "School Seal / Logo",
    "school_head": "School Head",
    "school_administrator": "School Administrator",
    "csm_focal_person": "CSM Coordinator",
    "school_address": "School Address",
    "school_email": "School Email Address",
    "school_contact": "School Contact Number",
}

IDENTIFIER_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
SCHOOL_ID_PATTERN = re.compile(r"^\d{4,12}$")
EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
CONTACT_ALLOWED_PATTERN = re.compile(r"^[0-9+().\-\s]+$")
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


def clean_registration_text(value: Any) -> str:
    """Normalize operator-entered profile text without changing its meaning."""

    return " ".join(str(value or "").split())


def school_registration_errors(
    settings: Mapping[str, Any],
    *,
    data_root: str | Path | None = None,
) -> dict[str, str]:
    """Return every invalid required registration field in display order.

    Completion is derived from the saved values and the durable logo file; it
    is never trusted from a boolean flag that could become stale after an
    interrupted update or a manually damaged settings file.
    """

    errors: dict[str, str] = {}
    try:
        validate_school_id(settings.get("school_id"), required=True)
    except ValueError as exc:
        errors["school_id"] = str(exc)

    text_limits = {
        "school_name": 180,
        "school_district": 180,
        "school_division": 180,
        "school_head": 180,
        "school_administrator": 180,
        "csm_focal_person": 180,
        "school_address": 300,
    }
    for key, maximum in text_limits.items():
        value = clean_registration_text(settings.get(key))
        label = SCHOOL_REGISTRATION_FIELD_LABELS[key]
        if not value:
            errors[key] = f"{label} is required."
        elif len(value) < 2:
            errors[key] = f"{label} must contain at least 2 characters."
        elif len(value) > maximum:
            errors[key] = f"{label} must not exceed {maximum} characters."

    email = clean_registration_text(settings.get("school_email"))
    if not email:
        errors["school_email"] = "School Email Address is required."
    elif len(email) > 254 or not EMAIL_PATTERN.fullmatch(email):
        errors["school_email"] = "Enter a valid School Email Address."

    contact = clean_registration_text(settings.get("school_contact"))
    contact_digits = re.sub(r"\D", "", contact)
    if not contact:
        errors["school_contact"] = "School Contact Number is required."
    elif (
        not CONTACT_ALLOWED_PATTERN.fullmatch(contact)
        or contact.count("+") > 1
        or ("+" in contact and not contact.lstrip().startswith("+"))
        or not 7 <= len(contact_digits) <= 15
    ):
        errors["school_contact"] = (
            "Enter a valid mobile or telephone number containing 7 to 15 digits."
        )

    logo_relative = str(settings.get("school_logo_path") or "").strip()
    if not logo_relative:
        errors["school_logo_path"] = "School Seal / Logo is required."
    elif data_root is not None:
        root = Path(data_root).expanduser().resolve()
        try:
            logo_path = (root / logo_relative).resolve()
            logo_path.relative_to(root)
        except (OSError, ValueError):
            errors["school_logo_path"] = "The saved School Seal / Logo path is invalid."
        else:
            try:
                valid_file = bool(
                    logo_path.is_file()
                    and 0 < logo_path.stat().st_size <= 12 * 1024 * 1024
                )
                if valid_file:
                    with Image.open(logo_path) as image:
                        valid_file = image.format in {"PNG", "JPEG", "BMP", "WEBP"}
                        image.verify()
            except (OSError, SyntaxError, ValueError, Image.DecompressionBombError):
                valid_file = False
            if not valid_file:
                errors["school_logo_path"] = (
                    "The saved School Seal / Logo is missing or invalid. Upload it again."
                )

    return errors


def school_registration_section_errors(
    settings: Mapping[str, Any],
    section_index: int,
    *,
    data_root: str | Path | None = None,
) -> dict[str, str]:
    """Return errors belonging to one of the three ordered registration steps."""

    if not 0 <= int(section_index) < len(SCHOOL_REGISTRATION_SECTIONS):
        raise ValueError("School registration section is out of range.")
    keys = set(SCHOOL_REGISTRATION_SECTIONS[int(section_index)][1])
    return {
        key: message
        for key, message in school_registration_errors(
            settings,
            data_root=data_root,
        ).items()
        if key in keys
    }


def school_registration_complete(
    settings: Mapping[str, Any],
    *,
    data_root: str | Path | None = None,
) -> bool:
    """Return whether all required school-registration data remains valid."""

    return not school_registration_errors(settings, data_root=data_root)


def first_incomplete_registration_section(
    settings: Mapping[str, Any],
    *,
    data_root: str | Path | None = None,
) -> int:
    """Return the first incomplete step, or the final step when complete."""

    errors = school_registration_errors(settings, data_root=data_root)
    for index, (_title, keys) in enumerate(SCHOOL_REGISTRATION_SECTIONS):
        if any(key in errors for key in keys):
            return index
    return len(SCHOOL_REGISTRATION_SECTIONS) - 1


def validate_school_registration(
    settings: Mapping[str, Any],
    *,
    data_root: str | Path | None = None,
) -> None:
    """Raise one concise error when the mandatory registration is incomplete."""

    errors = school_registration_errors(settings, data_root=data_root)
    if errors:
        first_message = next(iter(errors.values()))
        raise ValueError(f"Complete School Registration before continuing. {first_message}")
