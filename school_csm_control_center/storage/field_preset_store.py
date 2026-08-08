"""Persistent prefill and lock preferences for survey drawer fields."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from typing import Any, Iterable, Mapping

from school_csm_control_center.runtime_paths import storage_root_for
from school_csm_control_center.school_services import normalize_service_values
from school_csm_control_center.storage.file_safety import (
    InterProcessRLock,
    atomic_write_json,
    preserve_corrupt_file,
)


SUPPORTED_PRESET_FIELDS = (
    "mode",
    "survey_date",
    "agency_visited",
    "service_availed",
    "client_type",
    "sex",
    "age",
    "region",
    "email",
)


class FieldPresetStoreError(RuntimeError):
    """Raised when a validated preset document cannot be saved safely."""


class FieldPresetStore:
    """Read and atomically write reusable survey field preferences.

    ``load`` returns only the normalized field mapping; schema metadata stays
    inside the backing document.  Save-time validation is strict so UI errors
    are visible immediately.  Read-time validation is defensive: a malformed
    document or entry is ignored without preventing the app from opening.
    """

    FILE_TYPE = "CSM Survey Field Presets"
    SCHEMA_VERSION = "1.0"

    def __init__(
        self,
        project_root: str | Path,
        supported_fields: Iterable[str] | None = None,
        *,
        data_root: str | Path | None = None,
    ) -> None:
        self.install_root = Path(project_root)
        self.project_root = storage_root_for(project_root, data_root)
        self.path = (
            self.project_root
            / "data"
            / "csm_survey"
            / "field_presets.mossjson"
        )
        selected = (
            tuple(str(field).strip() for field in supported_fields)
            if supported_fields is not None
            else SUPPORTED_PRESET_FIELDS
        )
        self.supported_fields = tuple(
            field for index, field in enumerate(selected) if field and field not in selected[:index]
        )
        self._supported_set = set(self.supported_fields)
        self._lock = InterProcessRLock(self.path)
        self.last_read_error: str | None = None
        self.last_recovery_copy: Path | None = None

    def load(self) -> dict[str, dict[str, Any]]:
        """Return valid saved field presets, or an empty mapping on read failure."""

        with self._lock:
            self.last_read_error = None
            if not self.path.exists():
                return {}
            try:
                document = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                self._set_read_error(str(exc))
                return {}

            if not isinstance(document, dict):
                self._set_read_error("The field preset file root is not an object.")
                return {}
            fields = document.get("fields")
            if fields is None and any(key in self._supported_set for key in document):
                # Tolerate an early, unwrapped mapping while always writing the
                # versioned document shape on the next save.
                fields = document
            if not isinstance(fields, dict):
                self._set_read_error("The field preset fields value is not an object.")
                return {}

            normalized: dict[str, dict[str, Any]] = {}
            invalid: list[str] = []
            for name in self.supported_fields:
                if name not in fields:
                    continue
                try:
                    normalized[name] = self._normalize_entry(name, fields[name])
                except (TypeError, ValueError) as exc:
                    invalid.append(str(exc))
            if invalid:
                self._set_read_error("; ".join(invalid))
            return deepcopy(normalized)

    def save(
        self,
        fields: Mapping[str, Mapping[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        """Validate and atomically replace the complete preset mapping."""

        if not isinstance(fields, Mapping):
            raise TypeError("Field presets must be a mapping.")

        unknown = [str(name) for name in fields if str(name) not in self._supported_set]
        if unknown:
            raise ValueError(
                "Unsupported preset field(s): " + ", ".join(sorted(unknown))
            )

        normalized: dict[str, dict[str, Any]] = {}
        for name in self.supported_fields:
            if name in fields:
                normalized[name] = self._normalize_entry(name, fields[name])

        document = {
            "file_type": self.FILE_TYPE,
            "schema_version": self.SCHEMA_VERSION,
            "updated_at": _utc_now(),
            "fields": normalized,
        }
        with self._lock:
            self.load()
            if self.path.exists() and self.last_read_error:
                raise FieldPresetStoreError(
                    "The existing field-preset file could not be read and was left unchanged. "
                    "A recovery copy was preserved."
                )
            self._write_atomic(document)
            self.last_read_error = None
        return deepcopy(normalized)

    def _normalize_entry(
        self,
        name: str,
        entry: Mapping[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(entry, Mapping):
            raise TypeError(f"Preset '{name}' must be an object.")
        if "value" not in entry:
            raise ValueError(f"Preset '{name}' must include a value.")
        value = entry.get("value")
        if name == "service_availed" and isinstance(value, list):
            value = normalize_service_values(value, strict=True)
        elif not _is_json_scalar(value):
            raise ValueError(
                f"Preset '{name}' value must be a finite JSON scalar or null."
            )
        prefill = entry.get("prefill", False)
        locked = entry.get("locked", False)
        if not isinstance(prefill, bool):
            raise ValueError(f"Preset '{name}' prefill must be true or false.")
        if not isinstance(locked, bool):
            raise ValueError(f"Preset '{name}' locked must be true or false.")
        if locked:
            prefill = True
        return {
            "value": deepcopy(value),
            "prefill": prefill,
            "locked": locked,
        }

    def _write_atomic(self, document: dict[str, Any]) -> None:
        try:
            atomic_write_json(self.path, document, allow_nan=False)
        except (OSError, ValueError) as exc:
            raise FieldPresetStoreError(
                f"Unable to save field presets to {self.path}: {exc}"
            ) from exc

    def _set_read_error(self, message: str) -> None:
        self.last_read_error = str(message)
        self.last_recovery_copy = preserve_corrupt_file(self.path)


def _is_json_scalar(value: Any) -> bool:
    if value is None or isinstance(value, (str, bool, int)):
        return True
    return isinstance(value, float) and math.isfinite(value)


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )
