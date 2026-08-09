"""Revisioned Narrative Report lifecycle and immutable audit history."""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
import json
from pathlib import Path
import re
from typing import Any, Mapping
from uuid import uuid4

from school_csm_control_center.runtime_paths import storage_root_for
from school_csm_control_center.services.narrative_report import (
    build_narrative_source,
    validate_narrative_document,
)
from school_csm_control_center.storage.dashboard_snapshot_store import (
    DashboardSnapshotStore,
    sha256_canonical_json,
)
from school_csm_control_center.storage.file_safety import (
    InterProcessRLock,
    atomic_write_json,
    preserve_corrupt_file,
)


class NarrativeReportStoreError(RuntimeError):
    """Raised when a narrative audit record cannot be persisted safely."""


_EXPECTATION_NOT_SET = object()


class NarrativeReportStore:
    """Keep one revisioned narrative record per immutable Dashboard snapshot."""

    FILE_TYPE = "School CSM Control Center Narrative Reports"
    SCHEMA_VERSION = "1.0"
    CONTROL_PREFIX = "CSMS-NAR"
    STATUSES = frozenset({"draft", "approved"})
    GENERATION_METHODS = frozenset({"local", "openai"})

    def __init__(
        self,
        project_root: str | Path,
        path: str | Path | None = None,
        *,
        data_root: str | Path | None = None,
        snapshot_store: DashboardSnapshotStore | None = None,
    ) -> None:
        self.install_root = Path(project_root)
        self.project_root = storage_root_for(project_root, data_root)
        self.path = (
            Path(path)
            if path is not None
            else self.project_root
            / "data"
            / "csm_survey"
            / "narrative_reports.mossjson"
        )
        self._lock = InterProcessRLock(self.path)
        self.snapshot_store = snapshot_store or DashboardSnapshotStore(
            project_root,
            data_root=data_root,
        )
        self.last_read_error: str | None = None
        self.last_recovery_copy: Path | None = None

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            records = [
                deepcopy(record)
                for record in self._read_document()["records"]
                if isinstance(record, dict)
            ]
        records.sort(key=_record_sort_key, reverse=True)
        return records

    def list_all(self) -> list[dict[str, Any]]:
        return self.list()

    def get(self, report_id_or_control: str) -> dict[str, Any] | None:
        wanted = str(report_id_or_control or "").strip()
        if not wanted:
            return None
        return next(
            (
                row
                for row in self.list()
                if str(row.get("id") or "") == wanted
                or str(row.get("control_number") or "").casefold() == wanted.casefold()
            ),
            None,
        )

    def get_for_snapshot(self, snapshot_id: str) -> dict[str, Any] | None:
        wanted = str(snapshot_id or "").strip()
        return next(
            (
                row
                for row in self.list()
                if str(_mapping(row.get("source")).get("snapshot_id") or "") == wanted
            ),
            None,
        )

    def current_document(self, report_id_or_control: str) -> dict[str, Any] | None:
        record = self.get(report_id_or_control)
        if record is None:
            return None
        revision = _current_revision(record)
        if revision is not None:
            _verify_revision(revision)
        return deepcopy(revision.get("document")) if revision else None

    def verify(self, report_id_or_control: str) -> bool:
        """Verify revision hashes, source snapshot, approvals, and print links."""

        with self._lock:
            store = self._read_document()
            self._raise_if_corrupt()
            record = _find_record(store["records"], report_id_or_control)
            if record is None:
                raise KeyError("Narrative report was not found.")
            source = _mapping(record.get("source"))
            manifest = self.snapshot_store.load(_snapshot_reference(source))
            revisions = record.get("revisions")
            if not isinstance(revisions, list) or not revisions:
                raise NarrativeReportStoreError("Narrative report has no revisions.")
            by_number: dict[int, Mapping[str, Any]] = {}
            for expected_number, revision in enumerate(revisions, 1):
                if not isinstance(revision, Mapping) or int(revision.get("number") or 0) != expected_number:
                    raise NarrativeReportStoreError(
                        "Narrative revision sequence is invalid."
                    )
                _verify_revision(revision)
                validate_narrative_document(revision["document"], manifest)
                by_number[expected_number] = revision
            if int(record.get("current_revision") or 0) != len(revisions):
                raise NarrativeReportStoreError(
                    "Narrative current revision pointer is invalid."
                )

            approval = record.get("approval")
            expected_status = "approved" if approval is not None else "draft"
            if record.get("status") != expected_status:
                raise NarrativeReportStoreError("Narrative lifecycle status is invalid.")
            if approval is not None:
                if not isinstance(approval, Mapping):
                    raise NarrativeReportStoreError("Narrative approval record is invalid.")
                number = int(approval.get("revision_number") or 0)
                revision = by_number.get(number)
                if (
                    number != len(revisions)
                    or revision is None
                    or approval.get("revision_id") != revision.get("revision_id")
                    or approval.get("content_sha256") != revision.get("content_sha256")
                ):
                    raise NarrativeReportStoreError(
                        "Narrative approval is not linked to its revision."
                    )

            approval_history = record.get("approval_history")
            if not isinstance(approval_history, list):
                raise NarrativeReportStoreError("Narrative approval history is invalid.")

            approval_ids = {
                str(item.get("approval_id") or "")
                for item in [approval, *approval_history]
                if isinstance(item, Mapping)
            }
            audits = record.get("print_audits")
            if not isinstance(audits, list):
                raise NarrativeReportStoreError("Narrative print audit history is invalid.")
            for expected_sequence, audit in enumerate(audits, 1):
                if not isinstance(audit, Mapping) or int(audit.get("sequence") or 0) != expected_sequence:
                    raise NarrativeReportStoreError(
                        "Narrative print audit sequence is invalid."
                    )
                expected_kind = "original" if expected_sequence == 1 else "reprint"
                if audit.get("kind") != expected_kind:
                    raise NarrativeReportStoreError(
                        "Narrative print audit kind is invalid."
                    )
                revision = by_number.get(int(audit.get("revision_number") or 0))
                if (
                    revision is None
                    or audit.get("revision_id") != revision.get("revision_id")
                    or audit.get("content_sha256") != revision.get("content_sha256")
                    or str(audit.get("approval_id") or "") not in approval_ids
                    or audit.get("dashboard_control_number")
                    != source.get("dashboard_control_number")
                    or audit.get("snapshot_sha256") != source.get("snapshot_sha256")
                ):
                    raise NarrativeReportStoreError(
                        "Narrative print audit is not linked to verified source evidence."
                    )
            return True

    def create(
        self,
        snapshot_reference: Mapping[str, Any] | str,
        document: Mapping[str, Any],
        *,
        generation_method: str = "local",
        generation_metadata: Mapping[str, Any] | None = None,
        operator: str = "",
        created_at: Any = None,
    ) -> dict[str, Any]:
        """Create an initial draft, or return the identical existing draft."""

        manifest = self.snapshot_store.load(snapshot_reference)
        validate_narrative_document(document, manifest)
        source = build_narrative_source(manifest)["source"]
        method = str(generation_method or "").strip().casefold()
        if method not in self.GENERATION_METHODS:
            raise ValueError("Narrative generation method must be local or openai.")
        generation = _safe_generation(method, generation_metadata)
        timestamp = _normalize_timestamp(created_at)
        content = deepcopy(dict(document))
        content_digest = sha256_canonical_json(content)

        with self._lock:
            store = self._read_document()
            self._raise_if_corrupt()
            existing = next(
                (
                    row
                    for row in store["records"]
                    if isinstance(row, dict)
                    and str(_mapping(row.get("source")).get("snapshot_id") or "")
                    == source["snapshot_id"]
                ),
                None,
            )
            if existing is not None:
                current = _current_revision(existing)
                if current is not None:
                    _verify_revision(current)
                if current and current.get("content_sha256") == content_digest:
                    return deepcopy(existing)
                raise NarrativeReportStoreError(
                    "That Dashboard snapshot already has a narrative report; edit it "
                    "to append a revision instead of creating another report."
                )

            report_id = str(uuid4())
            control = self._next_control_number(store["records"], timestamp[:7])
            revision = _revision(
                number=1,
                document=content,
                content_digest=content_digest,
                created_at=timestamp,
                editor=operator,
                reason="Initial narrative draft",
                generation=generation,
            )
            now = _local_now()
            record = {
                "id": report_id,
                "control_number": control,
                "created_at": timestamp,
                "updated_at": now,
                "status": "draft",
                "source": deepcopy(source),
                "generation": generation,
                "current_revision": 1,
                "revisions": [revision],
                "approval": None,
                "approval_history": [],
                "print_audits": [],
                "lifecycle": [
                    _event(
                        "created",
                        timestamp,
                        operator,
                        "Narrative draft created from immutable Dashboard snapshot.",
                        revision_number=1,
                    )
                ],
            }
            store["records"].append(record)
            self._write_document(store)
            return deepcopy(record)

    def append_revision(
        self,
        report_id_or_control: str,
        document: Mapping[str, Any],
        *,
        editor: str = "",
        reason: str = "Narrative edited",
        generation_method: str = "manual",
        generation_metadata: Mapping[str, Any] | None = None,
        created_at: Any = None,
        expected_current_revision_id: str = "",
        expected_content_sha256: str = "",
        expected_approval_id: Any = _EXPECTATION_NOT_SET,
    ) -> dict[str, Any]:
        """Append a full immutable revision and invalidate current approval."""

        timestamp = _normalize_timestamp(created_at)
        with self._lock:
            store = self._read_document()
            self._raise_if_corrupt()
            record = _find_record(store["records"], report_id_or_control)
            if record is None:
                raise KeyError("Narrative report was not found.")
            _require_expected_state(
                record,
                revision_id=expected_current_revision_id,
                content_sha256=expected_content_sha256,
                approval_id=expected_approval_id,
            )
            manifest = self.snapshot_store.load(_snapshot_reference(record["source"]))
            validate_narrative_document(document, manifest)
            content = deepcopy(dict(document))
            content_digest = sha256_canonical_json(content)
            current = _current_revision(record)
            if current is not None:
                _verify_revision(current)
            if current and current.get("content_sha256") == content_digest:
                raise ValueError("The edited narrative is unchanged.")

            if generation_method == "manual":
                generation = {"method": "manual"}
            else:
                method = str(generation_method or "").strip().casefold()
                if method not in self.GENERATION_METHODS:
                    raise ValueError("Revision method must be manual, local, or openai.")
                generation = _safe_generation(method, generation_metadata)
            number = int(record.get("current_revision") or 0) + 1
            revision = _revision(
                number=number,
                document=content,
                content_digest=content_digest,
                created_at=timestamp,
                editor=editor,
                reason=reason,
                generation=generation,
            )
            record.setdefault("revisions", []).append(revision)
            record["current_revision"] = number
            now = _local_now()
            approval = record.get("approval")
            if isinstance(approval, Mapping):
                invalidated = deepcopy(dict(approval))
                invalidated.update(
                    {
                        "invalidated_at": timestamp,
                        "invalidated_by": _clean_text(editor),
                        "invalidation_reason": _clean_text(reason) or "Narrative edited",
                    }
                )
                record.setdefault("approval_history", []).append(invalidated)
            record["approval"] = None
            record["status"] = "draft"
            record["updated_at"] = now
            record.setdefault("lifecycle", []).append(
                _event(
                    "revision_appended",
                    timestamp,
                    editor,
                    _clean_text(reason) or "Narrative edited",
                    revision_number=number,
                )
            )
            self._write_document(store)
            return deepcopy(record)

    def approve(
        self,
        report_id_or_control: str,
        approver: str,
        *,
        approved_at: Any = None,
        expected_current_revision_id: str = "",
        expected_content_sha256: str = "",
        expected_approval_id: Any = _EXPECTATION_NOT_SET,
    ) -> dict[str, Any]:
        """Approve only the current revision; a later edit invalidates it."""

        approved_by = _clean_text(approver)
        if not approved_by:
            raise ValueError("Approver name is required.")
        timestamp = _normalize_timestamp(approved_at)
        with self._lock:
            store = self._read_document()
            self._raise_if_corrupt()
            record = _find_record(store["records"], report_id_or_control)
            if record is None:
                raise KeyError("Narrative report was not found.")
            _require_expected_state(
                record,
                revision_id=expected_current_revision_id,
                content_sha256=expected_content_sha256,
                approval_id=expected_approval_id,
            )
            manifest = self.snapshot_store.load(_snapshot_reference(record["source"]))
            current = _current_revision(record)
            if current is None:
                raise NarrativeReportStoreError("Narrative report has no current revision.")
            _verify_revision(current)
            validate_narrative_document(current["document"], manifest)
            existing = record.get("approval")
            if (
                isinstance(existing, Mapping)
                and int(existing.get("revision_number") or 0) == int(current["number"])
                and str(existing.get("content_sha256") or "") == current["content_sha256"]
                and str(existing.get("approved_by") or "") == approved_by
            ):
                return deepcopy(record)

            if isinstance(existing, Mapping):
                superseded = deepcopy(dict(existing))
                superseded.update(
                    {
                        "superseded_at": timestamp,
                        "superseded_by": approved_by,
                    }
                )
                record.setdefault("approval_history", []).append(superseded)
            approval = {
                "approval_id": str(uuid4()),
                "approved_at": timestamp,
                "approved_by": approved_by,
                "revision_number": current["number"],
                "revision_id": current["revision_id"],
                "content_sha256": current["content_sha256"],
            }
            record["approval"] = approval
            record["status"] = "approved"
            record["updated_at"] = _local_now()
            record.setdefault("lifecycle", []).append(
                _event(
                    "approved",
                    timestamp,
                    approved_by,
                    "Current narrative revision approved.",
                    revision_number=current["number"],
                )
            )
            self._write_document(store)
            return deepcopy(record)

    def record_print(
        self,
        report_id_or_control: str,
        *,
        printer_name: str,
        page_count: int,
        copies: int = 1,
        operator: str = "",
        settings: Mapping[str, Any] | None = None,
        printed_at: Any = None,
        kind: str | None = None,
        expected_current_revision_id: str = "",
        expected_content_sha256: str = "",
        expected_approval_id: Any = _EXPECTATION_NOT_SET,
        expected_snapshot_sha256: str = "",
    ) -> dict[str, Any]:
        """Append a print or reprint audit tied to the approved revision."""

        printer = _clean_text(printer_name)
        if not printer:
            raise ValueError("Printer name is required.")
        pages = _positive_int(page_count, "Page count")
        copy_count = _positive_int(copies, "Copies")
        timestamp = _normalize_timestamp(printed_at)
        with self._lock:
            store = self._read_document()
            self._raise_if_corrupt()
            record = _find_record(store["records"], report_id_or_control)
            if record is None:
                raise KeyError("Narrative report was not found.")
            _require_expected_state(
                record,
                revision_id=expected_current_revision_id,
                content_sha256=expected_content_sha256,
                approval_id=expected_approval_id,
                snapshot_sha256=expected_snapshot_sha256,
            )
            current = _current_revision(record)
            approval = record.get("approval")
            if current is None or not isinstance(approval, Mapping):
                raise NarrativeReportStoreError(
                    "Narrative report must be approved before printing."
                )
            _verify_revision(current)
            if (
                int(approval.get("revision_number") or 0) != int(current["number"])
                or str(approval.get("content_sha256") or "") != current["content_sha256"]
            ):
                raise NarrativeReportStoreError(
                    "Narrative approval does not match the current revision."
                )
            manifest = self.snapshot_store.load(_snapshot_reference(record["source"]))
            validate_narrative_document(current["document"], manifest)

            audits = record.setdefault("print_audits", [])
            inferred_kind = "original" if not audits else "reprint"
            audit_kind = str(kind or inferred_kind).strip().casefold()
            if audit_kind not in {"original", "reprint"}:
                raise ValueError("Narrative print kind must be original or reprint.")
            if not audits and audit_kind == "reprint":
                raise ValueError(
                    "The first Narrative Report print must be recorded as original."
                )
            if audits and audit_kind == "original":
                raise ValueError("A later Narrative Report print must be recorded as a reprint.")
            audit = {
                "audit_id": str(uuid4()),
                "sequence": len(audits) + 1,
                "kind": audit_kind,
                "printed_at": timestamp,
                "printed_by": _clean_text(operator),
                "printer_name": printer,
                "page_count": pages,
                "copies": copy_count,
                "settings": _json_value(settings or {}),
                "revision_number": current["number"],
                "revision_id": current["revision_id"],
                "content_sha256": current["content_sha256"],
                "approval_id": approval["approval_id"],
                "dashboard_control_number": record["source"]["dashboard_control_number"],
                "snapshot_sha256": record["source"]["snapshot_sha256"],
            }
            audits.append(audit)
            record["updated_at"] = _local_now()
            record.setdefault("lifecycle", []).append(
                _event(
                    "printed" if audit_kind == "original" else "reprinted",
                    timestamp,
                    operator,
                    f"Narrative Report {audit_kind} audit recorded.",
                    revision_number=current["number"],
                )
            )
            self._write_document(store)
            return deepcopy(record)

    def record_reprint(self, report_id_or_control: str, **print_details: Any) -> dict[str, Any]:
        print_details["kind"] = "reprint"
        return self.record_print(report_id_or_control, **print_details)

    def _next_control_number(
        self,
        records: list[Any],
        year_month: str,
    ) -> str:
        pattern = re.compile(
            rf"^{re.escape(self.CONTROL_PREFIX)}-{re.escape(year_month)}-(\d{{4}})$",
            re.IGNORECASE,
        )
        highest = 0
        for record in records:
            if not isinstance(record, Mapping):
                continue
            match = pattern.fullmatch(str(record.get("control_number") or ""))
            if match:
                highest = max(highest, int(match.group(1)))
        return f"{self.CONTROL_PREFIX}-{year_month}-{highest + 1:04d}"

    def _read_document(self) -> dict[str, Any]:
        self.last_read_error = None
        self.last_recovery_copy = None
        if not self.path.exists():
            return self._empty_document()
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._set_read_error(str(exc))
            return self._empty_document()
        if not isinstance(document, dict) or not isinstance(document.get("records"), list):
            self._set_read_error("Narrative report file has an invalid structure.")
            return self._empty_document()
        if document.get("file_type") != self.FILE_TYPE:
            self._set_read_error("Narrative report file type is not recognized.")
            return self._empty_document()
        if str(document.get("schema_version") or "") != self.SCHEMA_VERSION:
            self._set_read_error("Narrative report schema version is not supported.")
            return self._empty_document()
        return document

    def _empty_document(self) -> dict[str, Any]:
        return {
            "file_type": self.FILE_TYPE,
            "schema_version": self.SCHEMA_VERSION,
            "updated_at": None,
            "records": [],
        }

    def _write_document(self, document: Mapping[str, Any]) -> None:
        self._raise_if_corrupt()
        payload = deepcopy(dict(document))
        payload["file_type"] = self.FILE_TYPE
        payload["schema_version"] = self.SCHEMA_VERSION
        payload["updated_at"] = _local_now()
        try:
            atomic_write_json(self.path, payload, allow_nan=False)
        except (OSError, TypeError, ValueError) as exc:
            raise NarrativeReportStoreError(
                f"Unable to save Narrative Report history to {self.path}: {exc}"
            ) from exc

    def _raise_if_corrupt(self) -> None:
        if self.path.exists() and self.last_read_error:
            raise NarrativeReportStoreError(
                "The existing Narrative Report file could not be read and was left "
                "unchanged. A recovery copy was preserved."
            )

    def _set_read_error(self, message: str) -> None:
        self.last_read_error = str(message)
        self.last_recovery_copy = preserve_corrupt_file(self.path)


def _revision(
    *,
    number: int,
    document: Mapping[str, Any],
    content_digest: str,
    created_at: str,
    editor: str,
    reason: str,
    generation: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "revision_id": str(uuid4()),
        "number": number,
        "created_at": created_at,
        "editor": _clean_text(editor),
        "reason": _clean_text(reason),
        "generation": deepcopy(dict(generation)),
        "content_sha256": content_digest,
        "document": deepcopy(dict(document)),
    }


def _event(
    event_type: str,
    timestamp: str,
    actor: str,
    message: str,
    *,
    revision_number: int,
) -> dict[str, Any]:
    return {
        "event_id": str(uuid4()),
        "type": event_type,
        "at": timestamp,
        "actor": _clean_text(actor),
        "message": _clean_text(message),
        "revision_number": int(revision_number),
    }


def _safe_generation(
    method: str,
    metadata: Mapping[str, Any] | None,
) -> dict[str, Any]:
    raw = dict(metadata or {})
    if any(_sensitive_key(key) for key in raw):
        raise ValueError("Generation metadata cannot contain credentials or secrets.")
    if any(_API_SECRET_PATTERN.search(str(value)) for value in raw.values()):
        raise ValueError("Generation metadata cannot contain credentials or secrets.")
    allowed = ("model", "response_id", "provider")
    result = {"method": method}
    for key in allowed:
        if key in raw and _clean_text(raw[key]):
            result[key] = _clean_text(raw[key])
    return result


def _snapshot_reference(source: Mapping[str, Any]) -> dict[str, Any]:
    snapshot_id = str(source.get("snapshot_id") or "")
    return {
        "type": DashboardSnapshotStore.REFERENCE_TYPE,
        "schema_version": DashboardSnapshotStore.SCHEMA_VERSION,
        "snapshot_id": snapshot_id,
        "manifest_path": f"dashboard_snapshots/{snapshot_id}/manifest.mossjson",
        "digest_sha256": str(source.get("snapshot_sha256") or ""),
    }


def _current_revision(record: Mapping[str, Any]) -> dict[str, Any] | None:
    wanted = int(record.get("current_revision") or 0)
    revisions = record.get("revisions")
    if not isinstance(revisions, list):
        return None
    return next(
        (
            item
            for item in revisions
            if isinstance(item, dict) and int(item.get("number") or 0) == wanted
        ),
        None,
    )


def _require_expected_state(
    record: Mapping[str, Any],
    *,
    revision_id: str = "",
    content_sha256: str = "",
    approval_id: Any = _EXPECTATION_NOT_SET,
    snapshot_sha256: str = "",
) -> None:
    """Reject writes based on a stale preview or editor state."""

    current = _current_revision(record)
    expected_revision = _clean_text(revision_id)
    if expected_revision and (
        current is None
        or _clean_text(current.get("revision_id")) != expected_revision
    ):
        raise NarrativeReportStoreError(
            "The Narrative Report revision changed; reopen it before continuing."
        )
    expected_content = _clean_text(content_sha256).casefold()
    if expected_content and (
        current is None
        or _clean_text(current.get("content_sha256")).casefold()
        != expected_content
    ):
        raise NarrativeReportStoreError(
            "The Narrative Report content changed; reopen it before continuing."
        )
    if approval_id is not _EXPECTATION_NOT_SET:
        approval = record.get("approval")
        actual_approval = (
            _clean_text(approval.get("approval_id"))
            if isinstance(approval, Mapping)
            else ""
        )
        expected_approval = _clean_text(approval_id)
        if actual_approval != expected_approval:
            raise NarrativeReportStoreError(
                "The Narrative Report approval changed; reopen it before continuing."
            )
    expected_snapshot = _clean_text(snapshot_sha256).casefold()
    actual_snapshot = _clean_text(
        _mapping(record.get("source")).get("snapshot_sha256")
    ).casefold()
    if expected_snapshot and actual_snapshot != expected_snapshot:
        raise NarrativeReportStoreError(
            "The Narrative Report source snapshot changed; reopen it before continuing."
        )


def _verify_revision(revision: Mapping[str, Any]) -> None:
    document = revision.get("document")
    expected = str(revision.get("content_sha256") or "").casefold()
    if not isinstance(document, Mapping) or not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise NarrativeReportStoreError("Narrative revision integrity metadata is invalid.")
    if sha256_canonical_json(document) != expected:
        raise NarrativeReportStoreError(
            "Narrative revision content failed SHA-256 verification."
        )


def _find_record(records: list[Any], identity: str) -> dict[str, Any] | None:
    wanted = str(identity or "").strip()
    return next(
        (
            record
            for record in records
            if isinstance(record, dict)
            and (
                str(record.get("id") or "") == wanted
                or str(record.get("control_number") or "").casefold() == wanted.casefold()
            )
        ),
        None,
    )


def _normalize_timestamp(value: Any = None) -> str:
    if value in (None, ""):
        return _local_now()
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, datetime.min.time()).astimezone()
    else:
        try:
            parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("Timestamp must use ISO 8601 format.") from exc
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed.isoformat(timespec="seconds")


def _local_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a positive whole number.")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a positive whole number.") from exc
    if number < 1 or str(value).strip() not in {str(number), f"{number}.0"}:
        raise ValueError(f"{label} must be a positive whole number.")
    return number


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        if isinstance(value, str) and _API_SECRET_PATTERN.search(value):
            raise ValueError("Print settings cannot contain API credentials.")
        return deepcopy(value)
    if isinstance(value, Mapping):
        if any(_sensitive_key(key) for key in value):
            raise ValueError("Print settings cannot contain API credentials.")
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    raise TypeError(f"Print settings cannot contain {type(value).__name__} values.")


def _record_sort_key(record: Mapping[str, Any]) -> tuple[str, str]:
    return (
        str(record.get("created_at") or ""),
        str(record.get("id") or ""),
    )


def _sensitive_key(value: Any) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", str(value).casefold()).strip("_")
    parts = set(normalized.split("_"))
    return (
        "api_key" in normalized
        or normalized in {"key", "apikey", "clientsecret", "accesstoken"}
        or bool(parts & {"secret", "token", "password", "credential", "credentials"})
    )


_API_SECRET_PATTERN = re.compile(
    r"(?:\bsk-[A-Za-z0-9_-]{8,}\b|\bBearer\s+[A-Za-z0-9._~-]{8,})",
    re.IGNORECASE,
)
