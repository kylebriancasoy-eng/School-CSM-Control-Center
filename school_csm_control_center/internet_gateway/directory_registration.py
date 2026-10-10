"""Durable, offline-first district review requests, separate from authorization."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import secrets
from threading import RLock, Thread
from typing import Any, Mapping
from uuid import UUID, uuid4

from PySide6.QtCore import QObject, Signal

from school_csm_control_center.storage.file_safety import InterProcessRLock, atomic_write_json


IDENTITY_FIELDS = ("school_id", "school_name", "school_district", "school_division")
DECISIONS = {"pending", "approved", "rejected"}


class DistrictRegistrationQueue:
    """Only identity/receipt status is written locally; receipt secrets stay protected.

    This machine-specific file lives in gateway/, excluded from school data
    transfers and portable backups. A receipt can check its own pending request,
    not authenticate a school or change an approved directory record.
    """

    def __init__(self, data_root: Path, credentials: Any) -> None:
        self.path = Path(data_root) / "gateway" / "directory_request.json"
        self.credentials = credentials
        self.lock = InterProcessRLock(self.path)

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        if self.path.stat().st_size > 16384:
            raise ValueError("The district request file is invalid.")
        value = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("schema_version") != "1.0":
            raise ValueError("The district request file is invalid.")
        payload = value.get("payload")
        if not isinstance(payload, dict) or set(payload) != set(IDENTITY_FIELDS) | {
            "request_id", "installation_id"
        }:
            raise ValueError("The district request file is invalid.")
        if value.get("status") not in DECISIONS | {"queued"}:
            raise ValueError("The district request status is invalid.")
        for key in ("request_id", "installation_id"):
            if str(UUID(payload[key])) != payload[key]:
                raise ValueError("The district receipt identity is invalid.")
        return value

    def load(self) -> dict[str, Any]:
        with self.lock:
            return deepcopy(self._read())

    def queue(self, settings: Mapping[str, Any], installation_id: str, service_url: str,
              *, resubmit: bool = False) -> dict[str, Any]:
        identity = {key: " ".join(str(settings.get(key) or "").split()) for key in IDENTITY_FIELDS}
        if not re.fullmatch(r"[0-9]{4,12}", identity["school_id"]):
            raise ValueError("Enter the official School ID before sending a district request.")
        for key, maximum in (("school_name", 200), ("school_district", 160), ("school_division", 160)):
            if not identity[key] or len(identity[key]) > maximum:
                raise ValueError("School identity is incomplete or exceeds the district field limit.")
        if str(UUID(installation_id)) != installation_id or not service_url.startswith("https://"):
            raise ValueError("District registration configuration is invalid.")
        with self.lock:
            state = self._read()
            previous = state.get("payload", {})
            protected = self.credentials.load_directory_request_receipt(previous.get("request_id"))
            same_receipt = bool(
                state and protected
                and state.get("service_url") == service_url
                and previous.get("school_id") == identity["school_id"]
                and previous.get("installation_id") == installation_id
                and protected.get("request_id") == previous.get("request_id")
                and protected.get("service_url") == service_url
                and re.fullmatch(r"[0-9a-f]{64}", str(protected.get("request_secret") or ""))
            )
            changed = any(previous.get(key) != identity[key] for key in IDENTITY_FIELDS)
            new_receipt = not same_receipt or (
                state.get("status") == "rejected" and (resubmit or changed)
            )
            if new_receipt:
                request_id = str(uuid4())
                self.credentials.save_directory_request_receipt({
                    "request_id": request_id, "request_secret": secrets.token_hex(32),
                    "service_url": service_url,
                }, retain_request_id=previous.get("request_id"))
                state = {
                    "schema_version": "1.0", "service_url": service_url, "status": "queued",
                    "payload": {**identity, "installation_id": installation_id, "request_id": request_id},
                    "last_checked_at": "",
                }
            elif state["status"] in {"queued", "pending"} and changed:
                state["payload"].update(identity)
            # Approved/rejected receipt fields are frozen. Editing local school
            # information cannot rewrite the district's authoritative roster.
            try:
                atomic_write_json(self.path, state)
            except Exception:
                if new_receipt and protected:
                    try:
                        # The staged two-receipt value already preserves this
                        # secret if rollback itself fails or the process stops.
                        self.credentials.save_directory_request_receipt(
                            protected, retain_request_id=request_id,
                        )
                    except Exception:
                        pass
                raise
            return deepcopy(state)

    def send(self, client: Any) -> dict[str, Any]:
        with self.lock:
            state = self._read()
            if not state:
                raise ValueError("No district request is queued.")
            receipt = self.credentials.load_directory_request_receipt(state["payload"]["request_id"]) or {}
            if receipt.get("request_id") != state["payload"]["request_id"] or (
                receipt.get("service_url") != state["service_url"]
                or getattr(client.config, "service_url", "") != state["service_url"]
            ):
                raise ValueError("The protected district receipt does not match this request.")
            payload = {**state["payload"], "request_secret": receipt.get("request_secret", "")}
        # Never hold the local file lock during network access.
        response = client.submit_directory_request(payload)
        if not isinstance(response, Mapping) or set(response) != {"request_id", "school_id", "status"}:
            raise ValueError("The district service returned an invalid receipt.")
        if response["request_id"] != payload["request_id"] or response["school_id"] != payload["school_id"] or (
            response["status"] not in DECISIONS
        ):
            raise ValueError("The district service returned a mismatched receipt.")
        with self.lock:
            current = self._read()
            # A later profile save wins over a response from an older request.
            if current.get("payload") != state["payload"]:
                return deepcopy(current)
            current["status"] = response["status"]
            current["last_checked_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            atomic_write_json(self.path, current)
            return deepcopy(current)


class DistrictRegistrationController(QObject):
    state_changed = Signal(object)
    _finished = Signal(object)

    def __init__(self, queue: DistrictRegistrationQueue, client: Any | None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.queue = queue
        self.client = client
        self._lock = RLock()
        self._busy = False
        self._closed = False
        self._generation = 0
        self._finished.connect(self._on_async_finished)
        self._state: dict[str, Any] = {"status": "incomplete", "busy": False, "detail": "Complete School Registration first."}

    def state(self) -> dict[str, Any]:
        return deepcopy(self._state)

    def _show(self, status: str, detail: str) -> None:
        self._state = {"status": status, "detail": detail, "busy": self._busy}
        self.state_changed.emit(self.state())

    def prepare(self, settings: Mapping[str, Any], installation_id: str, *, resubmit: bool = False) -> bool:
        if self._closed:
            return False
        # Invalidate even a failed prepare: an old completion must not replace
        # a newer profile's storage-error or provider-unavailable indication.
        self._generation += 1
        if self.client is None:
            self._show("provider_unavailable", "District provider settings are not installed. Local registration remains complete; restart after the district provider is configured.")
            return False
        try:
            state = self.queue.queue(settings, installation_id, self.client.config.service_url, resubmit=resubmit)
        except Exception:
            self._show("storage_error", "The district request could not be safely queued. Your local school information is saved. Check protected storage, then try again; existing receipt files are preserved.")
            return False
        self._on_finished(state)
        return True

    def send_async(self) -> bool:
        with self._lock:
            if self._closed or self._busy or self.client is None:
                return False
            self._busy = True
            generation = self._generation
        self._state["busy"] = True
        self.state_changed.emit(self.state())

        def work() -> None:
            try:
                state = self.queue.send(self.client)
            except Exception:
                state = {"retry_failed": True}
            with self._lock:
                self._busy = False
                if self._closed:
                    return
                self._finished.emit({"generation": generation, "state": state})

        Thread(target=work, daemon=True, name="district-registration-request").start()
        return True

    def _on_async_finished(self, result: Mapping[str, Any]) -> None:
        if self._closed:
            return
        if result.get("generation") != self._generation:
            self._show(self._state["status"], self._state["detail"])
            return
        state = result["state"]
        try:
            # Another process may have changed the file after send() returned
            # or failed but before Qt delivered this queued signal.
            current = self.queue.load()
        except Exception:
            self._show("storage_error", "The district request status could not be safely read. Local school information remains available; check protected storage, then try again.")
            return
        if current:
            self._on_finished(current)
        if state.get("retry_failed"):
            self._on_finished(state)
        elif not current:
            self._show("storage_error", "The district request status is no longer available. Local school information remains saved; queue a new request before trying again.")

    def _on_finished(self, state: Mapping[str, Any]) -> None:
        if self._closed:
            return
        if state.get("retry_failed"):
            self._show(self._state["status"], "District synchronization could not be confirmed. It will retry while this app is open and after restart. Local surveys remain available; an older district app may need updating.")
            return
        status = str(state.get("status") or "queued")
        detail = {
            "queued": "Saved locally. The school identity is queued for district review and will retry when Internet access returns.",
            "pending": "Submitted to the district directory. Awaiting district approval; this does not enable Internet Gateway access.",
            "approved": "District request approved. Ask the district administrator for the one-time activation code, then complete Internet Gateway setup. Later official identity changes require district review.",
            "rejected": "The district did not approve this request. Contact the district administrator, correct School Information, and submit again.",
        }.get(status, "District registration status is unavailable.")
        self._show(status, detail)

    def close(self) -> None:
        with self._lock:
            self._closed = True
