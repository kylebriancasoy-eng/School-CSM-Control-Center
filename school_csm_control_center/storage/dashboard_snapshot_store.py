"""Immutable, content-verified evidence for successful Dashboard prints.

The desktop UI owns rendering.  This module deliberately has no Qt imports: it
stores the already-rendered PNG together with the exact aggregate values,
filters, school identity, report metadata, component bounds, and any auxiliary
graph assets that produced the printed Dashboard.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
import unicodedata
from typing import Any, Mapping
from uuid import NAMESPACE_URL, UUID, uuid5

from school_csm_control_center.runtime_paths import storage_root_for
from school_csm_control_center.storage.file_safety import (
    InterProcessRLock,
    atomic_write_json,
    preserve_corrupt_file,
)


class DashboardSnapshotStoreError(RuntimeError):
    """Base error for immutable Dashboard snapshot persistence."""


class SnapshotConflictError(DashboardSnapshotStoreError):
    """Raised when an existing print identity is given different evidence."""


class SnapshotIntegrityError(DashboardSnapshotStoreError):
    """Raised when a manifest, reference, or stored blob fails verification."""


class DashboardSnapshotStore:
    """Persist and verify the complete evidence behind one Dashboard print.

    ``save`` is idempotent for the same print record and identical evidence. A
    second attempt to use that print identity with different content is refused
    instead of replacing the first snapshot.
    """

    FILE_TYPE = "School CSM Control Center Dashboard Print Snapshot"
    SCHEMA_VERSION = "1.0"
    REFERENCE_TYPE = "dashboard_snapshot"
    SUCCESS_STATUSES = frozenset({"submitted", "confirmed"})
    MAIN_IMAGE_KEY = "dashboard_image"

    def __init__(
        self,
        project_root: str | Path,
        path: str | Path | None = None,
        *,
        data_root: str | Path | None = None,
    ) -> None:
        self.install_root = Path(project_root)
        self.project_root = storage_root_for(project_root, data_root)
        self.csm_data_dir = self.project_root / "data" / "csm_survey"
        self.path = (
            Path(path)
            if path is not None
            else self.csm_data_dir / "dashboard_snapshots"
        )
        self.path = self.path.expanduser().resolve()
        self._lock = InterProcessRLock(self.path / "snapshot-store.guard")

    def save(
        self,
        print_record_id: str,
        control_number: str,
        *,
        analysis: Mapping[str, Any],
        filters: Mapping[str, Any],
        school: Mapping[str, Any],
        report: Mapping[str, Any],
        image_png: bytes,
        image_meta: Mapping[str, Any],
        asset_bytes: Mapping[str, bytes] | None = None,
    ) -> dict[str, Any]:
        """Save immutable print evidence and return its portable reference."""

        print_id = _required_identity(print_record_id, "Print record ID")
        control = _required_text(control_number, "Dashboard control number")
        if not isinstance(image_png, bytes) or not image_png:
            raise ValueError("The rendered Dashboard image must be non-empty PNG bytes.")
        if not image_png.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("The rendered Dashboard image is not a PNG file.")
        if not isinstance(analysis, Mapping):
            raise TypeError("Dashboard analysis must be a mapping.")
        if not isinstance(filters, Mapping):
            raise TypeError("Dashboard filters must be a mapping.")
        if not isinstance(school, Mapping):
            raise TypeError("School information must be a mapping.")
        if not isinstance(report, Mapping):
            raise TypeError("Dashboard report metadata must be a mapping.")
        if not isinstance(image_meta, Mapping):
            raise TypeError("Dashboard image metadata must be a mapping.")
        report_control = " ".join(str(report.get("control_number") or "").split())
        if report_control and report_control != control:
            raise ValueError(
                "Dashboard report metadata does not match the reserved control number."
            )

        snapshot_id = str(
            uuid5(
                NAMESPACE_URL,
                f"mosslab.school-csm.dashboard-snapshot/{print_id}",
            )
        )
        directory = self.path / snapshot_id
        manifest_path = directory / "manifest.mossjson"

        blobs: dict[str, dict[str, Any]] = {}
        pending_blobs: dict[str, bytes] = {}
        self._add_blob(
            blobs,
            pending_blobs,
            self.MAIN_IMAGE_KEY,
            image_png,
            filename="dashboard.png",
            media_type="image/png",
            metadata=image_meta,
        )
        for raw_key, raw_bytes in sorted((asset_bytes or {}).items(), key=lambda item: str(item[0])):
            key = _safe_blob_key(raw_key)
            if key == self.MAIN_IMAGE_KEY:
                raise ValueError(f"Asset key {key!r} is reserved for the Dashboard image.")
            if not isinstance(raw_bytes, bytes) or not raw_bytes:
                raise ValueError(f"Snapshot asset {key!r} must contain non-empty bytes.")
            self._add_blob(
                blobs,
                pending_blobs,
                key,
                raw_bytes,
                filename=f"asset-{key}.bin",
                media_type="application/octet-stream",
                metadata={},
            )

        immutable = {
            "file_type": self.FILE_TYPE,
            "schema_version": self.SCHEMA_VERSION,
            "snapshot_id": snapshot_id,
            "captured_at": _local_now(),
            "source": {
                "print_record_id": print_id,
                "dashboard_control_number": control,
            },
            "analysis": _json_value(analysis),
            "filters": _json_value(filters),
            "school": _json_value(school),
            "report": _json_value(report),
            "blobs": blobs,
        }
        immutable_digest = sha256_canonical_json(immutable)
        manifest = {**immutable, "digest_sha256": immutable_digest}
        reference = self._reference(snapshot_id, immutable_digest)

        with self._lock:
            if manifest_path.exists():
                existing = self._load_manifest_path(manifest_path)
                existing_reference = self._reference(
                    snapshot_id,
                    str(existing.get("digest_sha256") or ""),
                )
                self._verify_loaded(existing_reference, existing, manifest_path)
                if _manifest_comparable(existing) != _manifest_comparable(manifest):
                    raise SnapshotConflictError(
                        "That Dashboard print already has different immutable evidence."
                    )
                return existing_reference

            directory.mkdir(parents=True, exist_ok=True)
            try:
                for key, value in pending_blobs.items():
                    _atomic_write_bytes(directory / blobs[key]["path"], value)
                atomic_write_json(manifest_path, manifest, allow_nan=False)
                self._verify_loaded(reference, manifest, manifest_path)
            except Exception:
                # Written content is intentionally not deleted here. A partial
                # directory is recoverable evidence and a later identical save
                # can finish it after an operator inspects the fault.
                raise
        return reference

    def load(self, reference: Mapping[str, Any] | str) -> dict[str, Any]:
        """Load and fully verify a snapshot manifest."""

        normalized = self._normalize_reference(reference)
        manifest_path = self._manifest_path(normalized)
        with self._lock:
            manifest = self._load_manifest_path(manifest_path)
            self._verify_loaded(normalized, manifest, manifest_path)
        return deepcopy(manifest)

    def verify(self, reference: Mapping[str, Any] | str) -> bool:
        """Return ``True`` when the manifest and every referenced blob verify.

        Integrity failures raise :class:`SnapshotIntegrityError`; callers can
        therefore show an actionable reason instead of treating corruption as
        an ordinary missing snapshot.
        """

        self.load(reference)
        return True

    def read_blob(
        self,
        reference: Mapping[str, Any] | str,
        blob: str = MAIN_IMAGE_KEY,
    ) -> bytes:
        """Read a verified blob by manifest key or its relative path."""

        normalized = self._normalize_reference(reference)
        manifest_path = self._manifest_path(normalized)
        with self._lock:
            manifest = self._load_manifest_path(manifest_path)
            self._verify_loaded(normalized, manifest, manifest_path)
            blobs = manifest["blobs"]
            wanted = str(blob or "").strip()
            selected = blobs.get(wanted)
            if not isinstance(selected, Mapping):
                selected = next(
                    (
                        item
                        for item in blobs.values()
                        if isinstance(item, Mapping) and item.get("path") == wanted
                    ),
                    None,
                )
            if not isinstance(selected, Mapping):
                raise KeyError(f"Snapshot blob {wanted!r} does not exist.")
            value = (manifest_path.parent / str(selected["path"])).read_bytes()
            if (
                len(value) != int(selected.get("bytes") or -1)
                or hashlib.sha256(value).hexdigest()
                != str(selected.get("sha256") or "").casefold()
            ):
                raise SnapshotIntegrityError(
                    f"Snapshot blob {wanted!r} changed while it was being read."
                )
            return value

    @classmethod
    def narrative_eligibility(
        cls,
        print_record: Mapping[str, Any],
    ) -> tuple[bool, str]:
        """Return whether a history row can be the source of a narrative.

        Legacy successful rows remain valid print audits but are deliberately
        ineligible when they lack the immutable evidence needed to reproduce
        and narrate the exact Dashboard.
        """

        if not isinstance(print_record, Mapping):
            return False, "The Dashboard print-history record is unavailable."
        status = str(print_record.get("status") or "").strip().casefold()
        if status not in cls.SUCCESS_STATUSES:
            return False, "Narrative reports require a successful Dashboard print."
        reference = cls.reference_from_print_record(print_record)
        if not reference:
            return (
                False,
                "This older print record has no immutable Dashboard snapshot.",
            )
        required = {"snapshot_id", "manifest_path", "digest_sha256"}
        if not required.issubset(reference):
            return False, "The Dashboard snapshot reference is incomplete."
        return True, ""

    @classmethod
    def is_print_record_narrative_eligible(
        cls,
        print_record: Mapping[str, Any],
    ) -> bool:
        return cls.narrative_eligibility(print_record)[0]

    @staticmethod
    def reference_from_print_record(
        print_record: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        for key in ("dashboard_snapshot", "snapshot_reference", "snapshot"):
            value = print_record.get(key)
            if isinstance(value, Mapping):
                return deepcopy(dict(value))
        return None

    def _add_blob(
        self,
        manifest_blobs: dict[str, dict[str, Any]],
        pending_blobs: dict[str, bytes],
        key: str,
        value: bytes,
        *,
        filename: str,
        media_type: str,
        metadata: Mapping[str, Any],
    ) -> None:
        if key in manifest_blobs:
            raise ValueError(f"Duplicate snapshot blob key: {key!r}.")
        manifest_blobs[key] = {
            "path": filename,
            "media_type": media_type,
            "bytes": len(value),
            "sha256": hashlib.sha256(value).hexdigest(),
            "metadata": _json_value(metadata),
        }
        pending_blobs[key] = value

    def _reference(self, snapshot_id: str, digest: str) -> dict[str, Any]:
        return {
            "type": self.REFERENCE_TYPE,
            "schema_version": self.SCHEMA_VERSION,
            "snapshot_id": snapshot_id,
            "manifest_path": f"dashboard_snapshots/{snapshot_id}/manifest.mossjson",
            "digest_sha256": digest,
        }

    def _normalize_reference(
        self,
        reference: Mapping[str, Any] | str,
    ) -> dict[str, Any]:
        if isinstance(reference, str):
            snapshot_id = _required_identity(reference, "Snapshot ID")
            return self._reference(snapshot_id, "")
        if not isinstance(reference, Mapping):
            raise TypeError("Dashboard snapshot reference must be a mapping or snapshot ID.")
        value = deepcopy(dict(reference))
        snapshot_id = _required_identity(value.get("snapshot_id"), "Snapshot ID")
        manifest_path = str(value.get("manifest_path") or "").strip()
        expected = f"dashboard_snapshots/{snapshot_id}/manifest.mossjson"
        if not manifest_path:
            manifest_path = expected
        if Path(manifest_path).is_absolute() or manifest_path.replace("\\", "/") != expected:
            raise SnapshotIntegrityError("The Dashboard snapshot path is not portable or safe.")
        digest = str(value.get("digest_sha256") or "").strip().casefold()
        if digest and not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise SnapshotIntegrityError("The Dashboard snapshot digest is invalid.")
        return {
            "type": self.REFERENCE_TYPE,
            "schema_version": str(value.get("schema_version") or self.SCHEMA_VERSION),
            "snapshot_id": snapshot_id,
            "manifest_path": expected,
            "digest_sha256": digest,
        }

    def _manifest_path(self, reference: Mapping[str, Any]) -> Path:
        path = (
            self.path
            / str(reference["snapshot_id"])
            / "manifest.mossjson"
        ).resolve()
        try:
            path.relative_to(self.path)
        except ValueError as exc:
            raise SnapshotIntegrityError("The Dashboard snapshot path leaves its data folder.") from exc
        return path

    def _load_manifest_path(self, path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise SnapshotIntegrityError("The Dashboard snapshot manifest is missing.") from exc
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            preserve_corrupt_file(path)
            raise SnapshotIntegrityError("The Dashboard snapshot manifest cannot be read.") from exc
        if not isinstance(value, dict):
            raise SnapshotIntegrityError("The Dashboard snapshot manifest is not an object.")
        return value

    def _verify_loaded(
        self,
        reference: Mapping[str, Any],
        manifest: Mapping[str, Any],
        manifest_path: Path,
    ) -> None:
        if manifest.get("file_type") != self.FILE_TYPE:
            raise SnapshotIntegrityError("The file is not a Dashboard snapshot manifest.")
        if str(manifest.get("schema_version") or "") != self.SCHEMA_VERSION:
            raise SnapshotIntegrityError("The Dashboard snapshot schema is unsupported.")
        if str(manifest.get("snapshot_id") or "") != str(reference["snapshot_id"]):
            raise SnapshotIntegrityError("The Dashboard snapshot identity does not match its reference.")
        stored_digest = str(manifest.get("digest_sha256") or "").casefold()
        unsigned = {key: value for key, value in manifest.items() if key != "digest_sha256"}
        actual_digest = sha256_canonical_json(unsigned)
        expected_digest = str(reference.get("digest_sha256") or "").casefold()
        if not re.fullmatch(r"[0-9a-f]{64}", stored_digest):
            raise SnapshotIntegrityError("The Dashboard snapshot has no valid digest.")
        if actual_digest != stored_digest or (expected_digest and expected_digest != stored_digest):
            raise SnapshotIntegrityError("The Dashboard snapshot manifest failed SHA-256 verification.")

        blobs = manifest.get("blobs")
        if not isinstance(blobs, Mapping) or self.MAIN_IMAGE_KEY not in blobs:
            raise SnapshotIntegrityError("The Dashboard snapshot image reference is missing.")
        for key, raw in blobs.items():
            if not isinstance(raw, Mapping):
                raise SnapshotIntegrityError(f"Snapshot blob {key!r} has invalid metadata.")
            relative = str(raw.get("path") or "")
            blob_path = (manifest_path.parent / relative).resolve()
            try:
                blob_path.relative_to(manifest_path.parent.resolve())
            except ValueError as exc:
                raise SnapshotIntegrityError(f"Snapshot blob {key!r} has an unsafe path.") from exc
            if blob_path.parent != manifest_path.parent.resolve() or not blob_path.is_file():
                raise SnapshotIntegrityError(f"Snapshot blob {key!r} is missing.")
            size = blob_path.stat().st_size
            if size != int(raw.get("bytes") or -1):
                raise SnapshotIntegrityError(f"Snapshot blob {key!r} has the wrong size.")
            if _sha256_file(blob_path) != str(raw.get("sha256") or "").casefold():
                raise SnapshotIntegrityError(f"Snapshot blob {key!r} failed SHA-256 verification.")


def canonical_json_bytes(value: Any) -> bytes:
    """Return normalized, deterministic UTF-8 JSON bytes for hashing."""

    normalized = _json_value(value)
    return json.dumps(
        normalized,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_canonical_json(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        if isinstance(value, str):
            if _API_SECRET_PATTERN.search(value):
                raise ValueError("Dashboard snapshots cannot contain API credentials.")
            return unicodedata.normalize("NFC", value)
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Snapshot JSON cannot contain NaN or infinity.")
        return 0.0 if value == 0 else value
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            if not isinstance(raw_key, str):
                raise TypeError("Snapshot JSON object keys must be strings.")
            key = unicodedata.normalize("NFC", raw_key)
            if _sensitive_key(key):
                raise ValueError("Dashboard snapshots cannot contain API credentials.")
            if key in result:
                raise ValueError(f"Snapshot JSON contains duplicate normalized key {key!r}.")
            result[key] = _json_value(raw_value)
        return result
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    raise TypeError(f"Snapshot JSON cannot contain {type(value).__name__} values.")


def _manifest_comparable(manifest: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: deepcopy(value)
        for key, value in manifest.items()
        if key not in {"captured_at", "digest_sha256"}
    }


def _required_text(value: Any, label: str) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        raise ValueError(f"{label} is required.")
    return text


def _required_identity(value: Any, label: str) -> str:
    text = _required_text(value, label)
    try:
        return str(UUID(text))
    except (ValueError, TypeError, AttributeError):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", text):
            raise ValueError(f"{label} contains unsupported characters.")
        return text


def _safe_blob_key(value: Any) -> str:
    key = str(value or "").strip().casefold().replace(" ", "_")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", key):
        raise ValueError(f"Snapshot asset key {value!r} is not safe.")
    return key


def _sensitive_key(value: Any) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", str(value).casefold()).strip("_")
    parts = set(normalized.split("_"))
    return (
        "api_key" in normalized
        or normalized in {"apikey", "clientsecret", "accesstoken"}
        or bool(parts & {"secret", "token", "password", "credential", "credentials"})
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(value)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            try:
                temporary_path.unlink()
            except OSError:
                pass


def _local_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


_API_SECRET_PATTERN = re.compile(
    r"(?:\bsk-[A-Za-z0-9_-]{8,}\b|\bBearer\s+[A-Za-z0-9._~-]{8,})",
    re.IGNORECASE,
)
