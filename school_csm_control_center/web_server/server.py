"""Standard-library local web server for the School CSM Control Center."""

from __future__ import annotations

from collections import OrderedDict
import base64
import binascii
from copy import deepcopy
from datetime import date, datetime, timezone
from email.utils import format_datetime
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
from pathlib import Path
import secrets
from threading import RLock
from time import time
from typing import Any, Callable, Mapping
from urllib.parse import parse_qs, unquote, urlparse

from school_csm_control_center.demographics import (
    AGE_BRACKETS,
    REGION_CODES,
    REGION_OPTIONS,
    normalize_demographics,
)
from school_csm_control_center.questionnaire import CC_QUESTIONS, SQD_QUESTIONS, apply_cc_branching, questionnaire_for
from school_csm_control_center.runtime_paths import storage_root_for
from school_csm_control_center.school_services import (
    SCHOOL_SERVICE_SECTIONS,
    SERVICE_BY_MRS_CODE,
    normalize_service_values,
    service_catalog_payload,
)
from school_csm_control_center.storage.control_center_settings import validate_school_id
from school_csm_control_center.storage.mrs_registry import MRSRegistryStore, normalize_mrs_control_number
from school_csm_control_center.storage.mrs_field_tests import MRSFieldTestStore
from school_csm_control_center.storage.scanner_security import (
    ScannerAuthenticationError,
    ScannerOperatorStore,
)
from school_csm_control_center.storage.survey_store import DuplicateControlNumberError, SurveyStore
from school_csm_control_center.web_server.scanner_jobs import (
    ScannerJobError,
    ScannerJobManager,
    ScannerJobNotFoundError,
)


SESSION_COOKIE = "SCHOOL_CSM_SESSION"
SCANNER_SESSION_COOKIE = "SCHOOL_CSM_SCANNER_SESSION"
PRIVACY_NOTICE_VERSION = "2026-07-01"

SCANNER_ENDPOINT = "/api/scanner/submissions"
SCANNER_MAX_BODY_SIZE = 3 * 1024 * 1024
SCANNER_SERVICE_MAP = {
    code: definition.label for code, definition in SERVICE_BY_MRS_CODE.items()
}
SCANNER_CLIENT_TYPE_MAP = {"1": "Citizen", "2": "Business", "3": "Government"}
SCANNER_SEX_MAP = {"1": "Male", "2": "Female", "0": "Did not specify"}


class SurveyHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        *,
        store: SurveyStore,
        static_root: Path,
        settings_provider: Callable[[], Mapping[str, Any]],
        project_root: Path | None = None,
        response_callback: Callable[[dict[str, Any]], None] | None = None,
        session_callback: Callable[[int], None] | None = None,
        scanner_operator_store: ScannerOperatorStore | None = None,
    ) -> None:
        super().__init__(server_address, SurveyRequestHandler)
        self.store = store
        self.static_root = static_root.resolve()
        self.project_root = (project_root or self.static_root.parents[2]).resolve()
        self.data_root = storage_root_for(self.project_root)
        self.settings_provider = settings_provider
        self.response_callback = response_callback
        self.session_callback = session_callback
        self.submission_lock = RLock()
        self.submission_cache: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self.session_lock = RLock()
        self.sessions: dict[str, float] = {}
        self.scanner_operator_store = scanner_operator_store or ScannerOperatorStore(self.project_root)
        self.mrs_registry = MRSRegistryStore(self.project_root)
        self.mrs_field_tests = MRSFieldTestStore(self.project_root)
        self.scanner_session_lock = RLock()
        self.scanner_sessions: dict[str, dict[str, Any]] = {}
        self.scanner_job_manager = ScannerJobManager(
            self.project_root,
            processing_limit_provider=lambda: int(self.settings().get("scanner_processing_limit") or 1),
        )
        self.reconcile_scanner_registry()

    def settings(self) -> dict[str, Any]:
        return deepcopy(dict(self.settings_provider()))

    def reconcile_scanner_registry(self) -> int:
        """Repair survey/MRS links left incomplete by an interrupted save."""

        repaired = 0
        for record in self.store.list():
            if self.reconcile_scanner_record(record):
                repaired += 1
        return repaired

    def reconcile_scanner_record(self, record: Mapping[str, Any]) -> bool:
        if str(record.get("source_code") or "").upper() != "MRS":
            return False
        meta = record.get("meta") if isinstance(record.get("meta"), Mapping) else {}
        submission_id = str(meta.get("scanner_submission_id") or "").strip()
        source_control = normalize_mrs_control_number(
            meta.get("source_form_control_number") or record.get("control_number")
        )
        response_record_id = str(record.get("id") or "").strip()
        if not submission_id or not source_control or not response_record_id:
            return False
        existing = self.mrs_registry.find_scan_attempt_by_submission(submission_id)
        if existing is not None:
            if not str(existing.get("response_record_id") or "").strip():
                self.mrs_registry.attach_response_record(
                    submission_id,
                    response_record_id,
                )
                return True
            return False
        try:
            self.mrs_registry.record_scan_attempt(
                control_number=source_control,
                barcode_status=str(meta.get("barcode_status") or "Normal"),
                scanner_submission_id=submission_id,
                scanner_operator={
                    "user_id": meta.get("scanner_operator_user_id"),
                    "username": meta.get("scanner_operator_username"),
                    "display_name": meta.get("scanner_operator_display_name"),
                },
                validity="valid",
                analysis_status="included",
                response_record_id=response_record_id,
                print_attempt_reference=str(
                    meta.get("print_attempt_reference") or ""
                ),
                scanned_at=meta.get("scanner_verified_at")
                or record.get("created_at"),
                image_sha256=str(meta.get("scanner_image_sha256") or ""),
                remarks="Registry link repaired automatically after an interrupted finalization.",
            )
        except Exception:
            logging.getLogger("school_csm_startup.scanner").exception(
                "Unable to reconcile scanner submission %s with the MRS registry.",
                submission_id,
            )
            return False
        return True

    def remember_submission(self, token: str, response: dict[str, Any]) -> None:
        if not token:
            return
        self.submission_cache[token] = deepcopy(response)
        self.submission_cache.move_to_end(token)
        while len(self.submission_cache) > 500:
            self.submission_cache.popitem(last=False)

    def create_session(self) -> tuple[str, int]:
        duration = max(60, min(3600, int(self.settings().get("session_duration_seconds") or 300)))
        token = secrets.token_urlsafe(32)
        with self.session_lock:
            self._cleanup_sessions_locked()
            self.sessions[token] = time() + duration
            count = len(self.sessions)
        self._notify_session_count(count)
        return token, duration

    def validate_session(self, token: str) -> tuple[bool, int]:
        if not token:
            return False, 0
        with self.session_lock:
            self._cleanup_sessions_locked()
            expiry = self.sessions.get(token)
            remaining = max(0, int(expiry - time())) if expiry else 0
            valid = bool(expiry and remaining > 0)
        return valid, remaining

    def expire_session(self, token: str) -> None:
        if not token:
            return
        with self.session_lock:
            self.sessions.pop(token, None)
            self._cleanup_sessions_locked()
            count = len(self.sessions)
        self._notify_session_count(count)

    def revoke_all_sessions(self) -> None:
        with self.session_lock:
            self.sessions.clear()
        self._notify_session_count(0)

    def create_scanner_session(self, operator: Mapping[str, Any]) -> tuple[str, dict[str, Any], int]:
        settings = self.settings()
        inactivity = max(300, min(7200, int(settings.get("scanner_session_inactivity_seconds") or 1800)))
        maximum = max(inactivity, min(86400, int(settings.get("scanner_session_max_seconds") or 28800)))
        token = secrets.token_urlsafe(40)
        now = time()
        session_id = f"SCNS-{datetime.now().year}-{secrets.token_hex(6).upper()}"
        session = {
            "session_id": session_id,
            "operator": deepcopy(dict(operator)),
            "created_at": now,
            "last_seen_at": now,
            "inactivity_expires_at": now + inactivity,
            "maximum_expires_at": now + maximum,
        }
        with self.scanner_session_lock:
            self._cleanup_scanner_sessions_locked()
            self.scanner_sessions[token] = session
        return token, deepcopy(session), maximum

    def validate_scanner_session(self, token: str, *, refresh: bool = True) -> tuple[bool, dict[str, Any] | None, int]:
        if not token:
            return False, None, 0
        settings = self.settings()
        inactivity = max(300, min(7200, int(settings.get("scanner_session_inactivity_seconds") or 1800)))
        with self.scanner_session_lock:
            self._cleanup_scanner_sessions_locked()
            session = self.scanner_sessions.get(token)
            if session is None:
                return False, None, 0
            now = time()
            if refresh:
                session["last_seen_at"] = now
                session["inactivity_expires_at"] = min(now + inactivity, float(session["maximum_expires_at"]))
            remaining = max(
                0,
                int(min(float(session["inactivity_expires_at"]), float(session["maximum_expires_at"])) - now),
            )
            return remaining > 0, deepcopy(session), remaining

    def expire_scanner_session(self, token: str) -> None:
        if not token:
            return
        with self.scanner_session_lock:
            self.scanner_sessions.pop(token, None)

    def revoke_scanner_sessions_for_user(self, user_id: str) -> None:
        wanted = str(user_id or "")
        with self.scanner_session_lock:
            for token, session in list(self.scanner_sessions.items()):
                operator = session.get("operator") if isinstance(session.get("operator"), Mapping) else {}
                if str(operator.get("user_id") or "") == wanted:
                    self.scanner_sessions.pop(token, None)

    def scanner_queue_status(self) -> dict[str, int]:
        return self.scanner_job_manager.system_status()

    def scanner_limit_changed(self) -> None:
        self.scanner_job_manager.notify_limit_changed()

    def active_session_count(self) -> int:
        with self.session_lock:
            self._cleanup_sessions_locked()
            count = len(self.sessions)
        return count

    def _cleanup_sessions_locked(self) -> None:
        now = time()
        expired = [token for token, expiry in self.sessions.items() if expiry <= now]
        for token in expired:
            self.sessions.pop(token, None)

    def _cleanup_scanner_sessions_locked(self) -> None:
        now = time()
        expired = [
            token
            for token, session in self.scanner_sessions.items()
            if float(session.get("inactivity_expires_at") or 0) <= now
            or float(session.get("maximum_expires_at") or 0) <= now
        ]
        for token in expired:
            self.scanner_sessions.pop(token, None)

    def server_close(self) -> None:
        self.scanner_job_manager.close()
        super().server_close()

    def _notify_session_count(self, count: int) -> None:
        if self.session_callback is not None:
            self.session_callback(int(count))


class SurveyRequestHandler(BaseHTTPRequestHandler):
    server: SurveyHTTPServer
    protocol_version = "HTTP/1.1"
    max_body_size = 128 * 1024

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        return

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        if path.startswith("/access/"):
            self._begin_access(path.removeprefix("/access/"))
            return
        if path in {"/portal", "/captive-portal"}:
            self._begin_captive_portal_access()
            return
        if path in {"/", "/survey", "/survey/"}:
            self._serve_static("index.html")
            return
        if path in {"/scanner", "/scanner/"}:
            self._serve_static("scanner_remote.html")
            return
        if path == "/api/config":
            self._send_json(HTTPStatus.OK, self._configuration())
            return
        if path == "/api/status":
            config = self._configuration(include_questionnaire=False)
            self._send_json(HTTPStatus.OK, config)
            return
        if path == "/api/scanner/status":
            settings = self.server.settings()
            payload = {
                "available": bool(settings.get("scanner_remote_enabled", True)),
                "intake_enabled": bool(settings.get("scanner_intake_enabled", True)),
                **self.server.scanner_queue_status(),
            }
            self._send_json(HTTPStatus.OK, payload)
            return
        if path == "/api/scanner/auth/session":
            self._handle_scanner_session_status()
            return
        if path == "/api/scanner/registry":
            self._handle_scanner_registry_lookup(parse_qs(parsed.query))
            return
        if path == "/api/scanner/jobs":
            self._handle_scanner_job_list()
            return
        if path.startswith("/api/scanner/jobs/"):
            self._handle_scanner_job_get(path)
            return
        if path == "/school-logo.png":
            self._serve_school_logo()
            return
        if path.startswith("/static/"):
            self._serve_static(unquote(path.removeprefix("/static/")))
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Page not found."})

    def do_OPTIONS(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path != SCANNER_ENDPOINT:
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Endpoint not found."})
            return
        self.send_response(HTTPStatus.NO_CONTENT)
        self._send_scanner_cors_headers()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/api/scanner/auth/login":
            self._handle_scanner_login()
            return
        if path == "/api/scanner/auth/logout":
            self._handle_scanner_logout()
            return
        if path == "/api/scanner/jobs":
            self._handle_scanner_job_create()
            return
        if path.startswith("/api/scanner/jobs/") and path.endswith("/cancel"):
            self._handle_scanner_job_cancel(path)
            return
        if path.startswith("/api/scanner/jobs/") and path.endswith("/command"):
            self._handle_scanner_job_command(path)
            return
        if path.startswith("/api/scanner/jobs/") and path.endswith("/finalize"):
            self._handle_scanner_job_finalize(path)
            return
        if path == SCANNER_ENDPOINT:
            self._handle_scanner_submission()
            return
        if path != "/api/submit":
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Endpoint not found."})
            return
        try:
            payload = self._read_json_body()
            response = self._save_submission(payload, self.server.settings(), self._session_cookie())
        except SessionExpiredError as exc:
            self._clear_session_cookie()
            self._send_json(HTTPStatus.UNAUTHORIZED, {"ok": False, "code": "session_expired", "error": str(exc)})
            return
        except SurveyUnavailableError as exc:
            self._send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"ok": False, "code": exc.code, "error": str(exc)})
            return
        except RequestValidationError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
            return
        except Exception as exc:
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": f"The response could not be saved: {exc}"})
            return
        self._clear_session_cookie()
        self._send_json(HTTPStatus.CREATED, response)

    def _handle_scanner_login(self) -> None:
        settings = self.server.settings()
        try:
            if not bool(settings.get("scanner_remote_enabled", True)):
                raise ScannerAuthorizationError("CSM Sheet Scanner Remote is disabled.", code="scanner_remote_disabled")
            payload = self._read_json_body(32 * 1024)
            operator = self.server.scanner_operator_store.authenticate(
                str(payload.get("username") or ""),
                str(payload.get("password") or ""),
                failed_login_limit=int(settings.get("scanner_failed_login_limit") or 5),
                lockout_seconds=int(settings.get("scanner_lockout_seconds") or 900),
            )
            token, session, maximum = self.server.create_scanner_session(operator)
        except ScannerAuthenticationError as exc:
            self._send_json(HTTPStatus.UNAUTHORIZED, {"authenticated": False, "code": exc.code, "error": str(exc)})
            return
        except ScannerAuthorizationError as exc:
            self._send_json(HTTPStatus.FORBIDDEN, {"authenticated": False, "code": exc.code, "error": str(exc)})
            return
        except RequestValidationError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"authenticated": False, "code": "invalid_login_request", "error": str(exc)})
            return
        self._pending_scanner_cookie = (token, maximum)
        self._send_json(
            HTTPStatus.OK,
            {
                "authenticated": True,
                "role": "scanner_operator",
                "operator": operator,
                "session_id": session["session_id"],
                "session_expires_in": maximum,
                "scanner_url": "/scanner",
            },
        )

    def _handle_scanner_logout(self) -> None:
        token = self._scanner_session_cookie()
        self.server.expire_scanner_session(token)
        self._clear_scanner_session_cookie()
        self._send_json(HTTPStatus.OK, {"authenticated": False, "message": "Scanner Operator signed out."})

    def _handle_scanner_session_status(self) -> None:
        valid, session, remaining = self.server.validate_scanner_session(self._scanner_session_cookie())
        if not valid or session is None:
            self._clear_scanner_session_cookie()
            self._send_json(HTTPStatus.UNAUTHORIZED, {"authenticated": False, "code": "scanner_session_expired"})
            return
        self._send_json(
            HTTPStatus.OK,
            {
                "authenticated": True,
                "role": "scanner_operator",
                "operator": session.get("operator") or {},
                "session_id": session.get("session_id") or "",
                "session_remaining_seconds": remaining,
                **self.server.scanner_queue_status(),
            },
        )

    def _authenticated_scanner_session(self) -> tuple[dict[str, Any], dict[str, Any]]:
        valid, session, _remaining = self.server.validate_scanner_session(self._scanner_session_cookie())
        if not valid or session is None:
            raise ScannerAuthorizationError("Scanner Operator sign-in is required.", code="scanner_session_expired")
        operator = session.get("operator") if isinstance(session.get("operator"), Mapping) else {}
        if not operator or not bool(operator.get("enabled", True)):
            raise ScannerAuthorizationError("This Scanner Operator account is unavailable.", code="account_disabled")
        return dict(operator), dict(session)

    def _handle_scanner_registry_lookup(self, query: Mapping[str, list[str]]) -> None:
        try:
            self._authenticated_scanner_session()
            raw = (query.get("control") or [""])[0]
            control = normalize_mrs_control_number(raw)
            if not control:
                raise RequestValidationError("Enter the printed MRS control number.")
            form = self.server.mrs_registry.get_form(control)
            if form is None:
                payload = {
                    "ok": True, "registered": False, "control_number": control,
                    "match_status": "unknown",
                    "message": "This control number is not registered in the Control Center.",
                }
            elif bool(form.get("valid_response_received")):
                payload = {
                    "ok": True, "registered": True, "control_number": control,
                    "match_status": "previously_valid",
                    "message": "A valid response has already been accepted for this control number.",
                    "print_status": form.get("current_print_status"),
                    "scan_status": form.get("current_scan_status"),
                    "pending_reprint": bool(form.get("pending_reprint")),
                }
            elif str(form.get("current_print_status") or "") == "Printed":
                payload = {
                    "ok": True, "registered": True, "control_number": control,
                    "match_status": "eligible",
                    "message": "Registered and awaiting its first valid scan.",
                    "print_status": form.get("current_print_status"),
                    "scan_status": form.get("current_scan_status"),
                    "pending_reprint": bool(form.get("pending_reprint")),
                    "latest_print_attempt": next(iter(self.server.mrs_registry.list_print_attempts(control)), {}).get("print_attempt_id", ""),
                }
            else:
                payload = {
                    "ok": True, "registered": True, "control_number": control,
                    "match_status": "print_not_confirmed",
                    "message": "The control number exists, but no current successful printed copy is confirmed.",
                    "print_status": form.get("current_print_status"),
                    "scan_status": form.get("current_scan_status"),
                    "pending_reprint": bool(form.get("pending_reprint")),
                }
        except ScannerAuthorizationError as exc:
            self._send_json(HTTPStatus.UNAUTHORIZED, {"ok": False, "code": exc.code, "error": str(exc)})
            return
        except RequestValidationError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
            return
        self._send_json(HTTPStatus.OK, payload)

    def _handle_scanner_job_list(self) -> None:
        try:
            operator, _session = self._authenticated_scanner_session()
            jobs = self.server.scanner_job_manager.list_jobs(str(operator.get("user_id") or ""))
        except ScannerAuthorizationError as exc:
            self._send_json(HTTPStatus.UNAUTHORIZED, {"ok": False, "code": exc.code, "error": str(exc)})
            return
        self._send_json(HTTPStatus.OK, {"ok": True, "jobs": jobs, **self.server.scanner_queue_status()})

    def _handle_scanner_job_create(self) -> None:
        settings = self.server.settings()
        try:
            if not bool(settings.get("scanner_remote_enabled", True)):
                raise ScannerAuthorizationError("CSM Sheet Scanner Remote is disabled.", code="scanner_remote_disabled")
            if not bool(settings.get("scanner_intake_enabled", True)):
                raise ScannerAuthorizationError("Scanner image intake is disabled.", code="scanner_intake_disabled")
            operator, session = self._authenticated_scanner_session()
            payload = self._read_json_body(17 * 1024 * 1024)
            status = self.server.scanner_job_manager.create_job(
                image_data_url=str(payload.get("image_data_url") or ""),
                operator=operator,
                session_id=str(session.get("session_id") or ""),
                test_mode=bool(payload.get("test_mode")),
            )
        except ScannerAuthorizationError as exc:
            self._send_json(HTTPStatus.UNAUTHORIZED, {"ok": False, "code": exc.code, "error": str(exc)})
            return
        except (ScannerJobError, RequestValidationError) as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "code": "invalid_scanner_image", "error": str(exc)})
            return
        except Exception as exc:
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "code": "scanner_job_failed", "error": str(exc)})
            return
        self._send_json(HTTPStatus.ACCEPTED, {"ok": True, **status})

    def _handle_scanner_job_get(self, path: str) -> None:
        parts = [part for part in path.split("/") if part]
        if len(parts) not in {5, 6} or parts[:3] != ["api", "scanner", "jobs"]:
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Scanner job endpoint not found."})
            return
        job_id, action = parts[3], parts[4]
        try:
            operator, _session = self._authenticated_scanner_session()
            operator_id = str(operator.get("user_id") or "")
            if action == "status":
                status = self.server.scanner_job_manager.status(job_id, operator_id)
                self._send_json(HTTPStatus.OK, {"ok": True, **status})
                return
            if action == "source":
                source = self.server.scanner_job_manager.source_path(job_id, operator_id)
                mime_type = "image/png" if source.suffix.casefold() == ".png" else "image/webp" if source.suffix.casefold() == ".webp" else "image/jpeg"
                self._serve_file(source, mime_type, cache_control="no-store")
                return
            if action == "preview":
                preview = self.server.scanner_job_manager.preview_path(job_id, operator_id)
                self._serve_file(preview, "image/jpeg", cache_control="no-store")
                return
            if action == "canonical":
                canonical = self.server.scanner_job_manager.canonical_path(job_id, operator_id)
                self._serve_file(canonical, "image/jpeg", cache_control="no-store")
                return
            if action == "artifact" and len(parts) == 6:
                artifact = self.server.scanner_job_manager.artifact_path(job_id, operator_id, parts[5])
                self._serve_file(artifact, "image/jpeg", cache_control="no-store")
                return
        except ScannerAuthorizationError as exc:
            self._send_json(HTTPStatus.UNAUTHORIZED, {"ok": False, "code": exc.code, "error": str(exc)})
            return
        except (ScannerJobNotFoundError, ScannerJobError) as exc:
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": str(exc)})
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Scanner job endpoint not found."})

    def _handle_scanner_job_cancel(self, path: str) -> None:
        parts = [part for part in path.split("/") if part]
        if len(parts) != 5 or parts[:3] != ["api", "scanner", "jobs"] or parts[4] != "cancel":
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Scanner job endpoint not found."})
            return
        try:
            operator, _session = self._authenticated_scanner_session()
            status = self.server.scanner_job_manager.cancel(parts[3], str(operator.get("user_id") or ""))
        except ScannerAuthorizationError as exc:
            self._send_json(HTTPStatus.UNAUTHORIZED, {"ok": False, "code": exc.code, "error": str(exc)})
            return
        except ScannerJobNotFoundError as exc:
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": str(exc)})
            return
        except ScannerJobError as exc:
            self._send_json(HTTPStatus.CONFLICT, {"ok": False, "error": str(exc)})
            return
        self._send_json(HTTPStatus.OK, {"ok": True, **status})


    def _handle_scanner_job_command(self, path: str) -> None:
        parts = [part for part in path.split("/") if part]
        if len(parts) != 5 or parts[:3] != ["api", "scanner", "jobs"] or parts[4] != "command":
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Scanner job endpoint not found."})
            return
        try:
            operator, _session = self._authenticated_scanner_session()
            payload = self._read_json_body(128 * 1024)
            parameters = payload.get("parameters") if isinstance(payload.get("parameters"), Mapping) else {}
            status = self.server.scanner_job_manager.command(
                parts[3],
                str(operator.get("user_id") or ""),
                str(payload.get("command") or ""),
                parameters,
            )
        except ScannerAuthorizationError as exc:
            self._send_json(HTTPStatus.UNAUTHORIZED, {"ok": False, "code": exc.code, "error": str(exc)})
            return
        except ScannerJobNotFoundError as exc:
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": str(exc)})
            return
        except (ScannerJobError, RequestValidationError) as exc:
            self._send_json(HTTPStatus.CONFLICT, {"ok": False, "error": str(exc)})
            return
        self._send_json(HTTPStatus.ACCEPTED, {"ok": True, **status})

    def _handle_scanner_job_finalize(self, path: str) -> None:
        parts = [part for part in path.split("/") if part]
        if len(parts) != 5 or parts[:3] != ["api", "scanner", "jobs"] or parts[4] != "finalize":
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Scanner job endpoint not found."})
            return
        settings = self.server.settings()
        try:
            if not bool(settings.get("scanner_intake_enabled", True)):
                raise ScannerAuthorizationError("Scanner response intake is disabled.", code="scanner_intake_disabled")
            operator, session = self._authenticated_scanner_session()
            payload = self._read_json_body(512 * 1024)
            context = self.server.scanner_job_manager.finalization_context(
                parts[3],
                str(operator.get("user_id") or ""),
                payload,
            )
            if bool(context.get("already_completed")):
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "ok": True,
                        "accepted": True,
                        "duplicate": True,
                        "record_id": context.get("record_id") or "",
                        "control_number": context.get("control_number") or "",
                        "test_mode": bool(context.get("test_mode")),
                        "test_result_id": context.get("test_result_id") or "",
                        "message": "This scan was already finalized.",
                    },
                )
                return
            if bool(context.get("test_mode")):
                test_record = self.server.mrs_field_tests.record(
                    context=context,
                    operator=operator,
                    session_id=str(session.get("session_id") or ""),
                    notes=str((payload.get("manual") or {}).get("comments") or "") if isinstance(payload.get("manual"), Mapping) else "",
                    test_context=payload.get("test_context") if isinstance(payload.get("test_context"), Mapping) else {},
                )
                response = {
                    "accepted": True,
                    "duplicate": False,
                    "test_mode": True,
                    "test_result_id": test_record.get("test_result_id") or "",
                    "record_id": "",
                    "control_number": str(test_record.get("recognized_control_number") or ""),
                    "scanner_submission_id": context.get("scanner_submission_id") or "",
                    "included_in_analysis": False,
                    "message": "Field-test scan recorded. No official response or control number was created, and CSM analysis was not changed.",
                }
            else:
                try:
                    response = self._save_scanner_submission(context, settings, operator, session)
                except DuplicateScannerSubmissionError:
                    existing = self._find_scanner_record(str(context.get("scanner_submission_id") or ""))
                    if existing is None:
                        raise
                    self.server.reconcile_scanner_record(existing)
                    response = {
                        "accepted": True,
                        "duplicate": True,
                        "record_id": existing.get("id", ""),
                        "control_number": existing.get("control_number", ""),
                        "scanner_submission_id": context.get("scanner_submission_id") or "",
                        "included_in_analysis": True,
                        "message": "This scan was already finalized and its existing record was recovered.",
                    }
            status = self.server.scanner_job_manager.mark_completed(
                parts[3],
                str(operator.get("user_id") or ""),
                record_id=str(response.get("record_id") or ""),
                control_number=str(response.get("control_number") or ""),
                test_result_id=str(response.get("test_result_id") or ""),
            )
        except ScannerAuthorizationError as exc:
            self._send_json(HTTPStatus.UNAUTHORIZED, {"ok": False, "code": exc.code, "error": str(exc)})
            return
        except ScannerJobNotFoundError as exc:
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": str(exc)})
            return
        except DuplicateScannerSubmissionError as exc:
            self._send_json(HTTPStatus.CONFLICT, {"ok": False, "code": "duplicate_scanner_submission", "error": str(exc)})
            return
        except DuplicateControlNumberError as exc:
            self._send_json(HTTPStatus.CONFLICT, {"ok": False, "code": "duplicate_control_number", "error": str(exc)})
            return
        except (ScannerJobError, RequestValidationError) as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "code": "invalid_scanner_review", "error": str(exc)})
            return
        except Exception as exc:
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "code": "scanner_finalize_failed", "error": f"The scan could not be finalized: {exc}"})
            return
        result_status = HTTPStatus.OK if bool(response.get("duplicate")) else HTTPStatus.CREATED
        self._send_json(result_status, {"ok": True, **response, "job": status})

    def _find_scanner_record(self, scanner_submission_id: str) -> dict[str, Any] | None:
        wanted = " ".join(str(scanner_submission_id or "").split()).casefold()
        if not wanted:
            return None
        for record in self.server.store.list():
            meta = record.get("meta") if isinstance(record.get("meta"), Mapping) else {}
            if " ".join(str(meta.get("scanner_submission_id") or "").split()).casefold() == wanted:
                return record
        return None


    def _begin_captive_portal_access(self) -> None:
        settings = self.server.settings()
        enabled = bool(settings.get("captive_portal_enabled", True))
        network_mode = str(settings.get("network_mode") or "hotspot").casefold()
        access_mode = str(settings.get("access_mode") or "captive_portal").casefold()
        if not enabled or network_mode != "hotspot" or access_mode != "captive_portal":
            self._redirect("/?access=portal_disabled")
            return
        status = str(settings.get("survey_status") or "offline").casefold()
        if status != "online":
            self._redirect("/")
            return
        token, duration = self.server.create_session()
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", "/")
        self.send_header(
            "Set-Cookie",
            f"{SESSION_COOKIE}={token}; Path=/; Max-Age={duration}; HttpOnly; SameSite=Lax",
        )
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _begin_access(self, supplied_key: str) -> None:
        settings = self.server.settings()
        expected = str(settings.get("public_access_key") or "")
        if not expected or not secrets.compare_digest(str(supplied_key or ""), expected):
            self._redirect("/?access=invalid")
            return
        status = str(settings.get("survey_status") or "offline").casefold()
        if status != "online":
            self._redirect("/")
            return
        token, duration = self.server.create_session()
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", "/")
        self.send_header(
            "Set-Cookie",
            f"{SESSION_COOKIE}={token}; Path=/; Max-Age={duration}; HttpOnly; SameSite=Lax",
        )
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _configuration(self, *, include_questionnaire: bool = True) -> dict[str, Any]:
        settings = self.server.settings()
        mode = str(settings.get("active_mode") or "onsite").strip().casefold()
        try:
            questionnaire = questionnaire_for(mode)
        except ValueError:
            mode = "onsite"
            questionnaire = questionnaire_for(mode)
        status = str(settings.get("survey_status") or "offline").strip().casefold()
        session_valid, remaining = self.server.validate_session(self._session_cookie())
        local_ip = str(settings.get("local_ip") or self.server.server_address[0])
        port = int(self.server.server_address[1])
        suffix = "" if port == 80 else f":{port}"
        named_hostname = str(settings.get("named_hostname") or "")
        payload: dict[str, Any] = {
            "ok": True,
            "school_name": str(settings.get("school_name") or "School"),
            "school_id": str(settings.get("school_id") or ""),
            "school_region": str(settings.get("school_region") or ""),
            "school_division": str(settings.get("school_division") or ""),
            "school_district": str(settings.get("school_district") or ""),
            "school_address_text": str(settings.get("school_address") or ""),
            "school_email": str(settings.get("school_email") or ""),
            "school_contact": str(settings.get("school_contact") or ""),
            "school_head": str(settings.get("school_head") or ""),
            "csm_focal_person": str(settings.get("csm_focal_person") or ""),
            "school_logo_url": "/school-logo.png" if str(settings.get("school_logo_path") or "").strip() else "",
            "system_name": "School CSM Control Center",
            "form_name": "CSM Survey Form",
            "mode": mode,
            "survey_status": status,
            "status_message": str(settings.get("status_message") or ""),
            "session_valid": bool(session_valid),
            "session_remaining_seconds": remaining,
            "session_duration_seconds": int(settings.get("session_duration_seconds") or 300),
            "network_mode": str(settings.get("network_mode") or "hotspot"),
            "access_mode": str(settings.get("access_mode") or "captive_portal"),
            "captive_portal_enabled": bool(settings.get("captive_portal_enabled", True)),
            "survey_date": str(settings.get("survey_date") or date.today().isoformat()),
            "survey_address": f"http://{named_hostname}{suffix}" if named_hostname else "",
            "direct_address": f"http://{local_ip}{suffix}",
            "scanner_intake_enabled": bool(settings.get("scanner_intake_enabled", True)),
            "scanner_remote_enabled": bool(settings.get("scanner_remote_enabled", True)),
            "scanner_url": f"http://{local_ip}{suffix}/scanner",
            "scanner_processing_limit": int(settings.get("scanner_processing_limit") or 1),
            "scanner_endpoint": f"http://{local_ip}{suffix}{SCANNER_ENDPOINT}",
            "privacy_notice": (
                "Your answers are stored on the school's local computer and used for Client Satisfaction Measurement and service improvement. Personally identifying fields are optional."
            ),
            "privacy_notice_version": PRIVACY_NOTICE_VERSION,
            "service_sections": [
                {"label": label, "options": list(options)} for label, options in SCHOOL_SERVICE_SECTIONS
            ],
            "service_catalog": service_catalog_payload(),
            "age_brackets": [
                {"code": code, "label": label} for code, label in AGE_BRACKETS
            ],
            "regions": list(REGION_OPTIONS),
            "server_time": datetime.now(timezone.utc).isoformat(),
        }
        if include_questionnaire and status == "online" and session_valid:
            payload["questionnaire"] = questionnaire
        return payload

    def _read_json_body(self, max_body_size: int | None = None) -> dict[str, Any]:
        raw_length = self.headers.get("Content-Length", "")
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise RequestValidationError("Invalid request length.") from exc
        if length <= 0:
            raise RequestValidationError("No survey response was received.")
        limit = int(max_body_size or self.max_body_size)
        if length > limit:
            raise RequestValidationError("The survey response is too large.")
        raw = self.rfile.read(length)
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RequestValidationError("The survey response is not valid JSON.") from exc
        if not isinstance(parsed, dict):
            raise RequestValidationError("The survey response must be an object.")
        return parsed

    def _handle_scanner_submission(self) -> None:
        settings = self.server.settings()
        try:
            if not bool(settings.get("scanner_intake_enabled", True)):
                raise ScannerAuthorizationError("CSM Sheet Scanner intake is disabled.", code="scanner_intake_disabled")
            operator, session = self._authenticated_scanner_session()
            payload = self._read_json_body(SCANNER_MAX_BODY_SIZE)
            response = self._save_scanner_submission(payload, settings, operator, session)
        except ScannerAuthorizationError as exc:
            self._send_json(HTTPStatus.FORBIDDEN, {"accepted": False, "code": exc.code, "reason": str(exc)}, scanner_cors=True)
            return
        except DuplicateScannerSubmissionError as exc:
            submission_id = str(payload.get("scanner_submission_id") or "") if "payload" in locals() else ""
            existing = self._find_scanner_record(submission_id)
            if existing is not None:
                self.server.reconcile_scanner_record(existing)
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "accepted": True,
                        "duplicate": True,
                        "record_id": existing.get("id") or "",
                        "control_number": existing.get("control_number") or "",
                        "scanner_submission_id": submission_id,
                        "included_in_analysis": True,
                        "message": "This scan was already finalized and its existing record was recovered.",
                    },
                    scanner_cors=True,
                )
                return
            self._send_json(HTTPStatus.CONFLICT, {"accepted": False, "code": "duplicate_scanner_submission", "reason": str(exc)}, scanner_cors=True)
            return
        except DuplicateControlNumberError as exc:
            self._send_json(HTTPStatus.CONFLICT, {"accepted": False, "code": "duplicate_control_number", "reason": str(exc)}, scanner_cors=True)
            return
        except RequestValidationError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"accepted": False, "code": "invalid_scanner_record", "reason": str(exc)}, scanner_cors=True)
            return
        except Exception as exc:
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"accepted": False, "code": "scanner_save_failed", "reason": f"The verified scanner response could not be saved: {exc}"}, scanner_cors=True)
            return
        self._send_json(HTTPStatus.CREATED, response, scanner_cors=True)

    def _save_scanner_submission(
        self,
        payload: Mapping[str, Any],
        settings: Mapping[str, Any],
        operator: Mapping[str, Any],
        session: Mapping[str, Any],
    ) -> dict[str, Any]:
        cleaned, scanner_id, source_control, survey_date = self._validated_scanner_record(
            payload, settings, operator, session
        )
        source_control = normalize_mrs_control_number(source_control)
        validity = str(payload.get("response_validity") or "valid").strip().casefold()
        barcode_status = str(payload.get("barcode_status") or "Uncertain").strip()
        invalid_reason = str(payload.get("invalid_reason") or "").strip()
        scanner_job = payload.get("scanner_job") if isinstance(payload.get("scanner_job"), Mapping) else {}
        image_sha256 = str(scanner_job.get("image_sha256") or "").strip().casefold()
        operator_record = {
            "user_id": str(operator.get("user_id") or ""),
            "username": str(operator.get("username") or ""),
            "display_name": str(operator.get("display_name") or ""),
        }
        print_attempts = self.server.mrs_registry.list_print_attempts(source_control)
        print_reference = next(
            (str(row.get("print_attempt_id") or "") for row in print_attempts if str(row.get("outcome") or "") == "Successful"),
            str(print_attempts[0].get("print_attempt_id") or "") if print_attempts else "",
        )
        with self.server.submission_lock:
            for record in self.server.store.list():
                meta = record.get("meta") if isinstance(record.get("meta"), Mapping) else {}
                existing = " ".join(str(meta.get("scanner_submission_id") or "").split()).casefold()
                if existing and existing == scanner_id.casefold():
                    raise DuplicateScannerSubmissionError(f"Scanner submission already exists: {scanner_id}")
                existing_hash = str(meta.get("scanner_image_sha256") or "").strip().casefold()
                if image_sha256 and existing_hash and image_sha256 == existing_hash:
                    raise DuplicateScannerSubmissionError("This exact form image has already been finalized.")

            for attempt in self.server.mrs_registry.list_scan_attempts():
                existing_submission = str(attempt.get("scanner_submission_id") or "").strip().casefold()
                existing_hash = str(attempt.get("image_sha256") or "").strip().casefold()
                if existing_submission and existing_submission == scanner_id.casefold():
                    raise DuplicateScannerSubmissionError(f"Scanner submission already exists: {scanner_id}")
                if image_sha256 and existing_hash and image_sha256 == existing_hash:
                    raise DuplicateScannerSubmissionError("This exact form image has already been recorded as a scan attempt.")

            registered = self.server.mrs_registry.get_form(source_control)
            if registered is None:
                attempt = self.server.mrs_registry.record_scan_attempt(
                    control_number=source_control, barcode_status=barcode_status,
                    scanner_submission_id=scanner_id, scanner_operator=operator_record,
                    validity="unknown", invalid_reason="Unknown control number",
                    analysis_status="excluded", scanned_at=payload.get("verified_at"),
                    print_attempt_reference=print_reference, image_sha256=image_sha256,
                )
                return {
                    "accepted": False, "recorded": True, "held_for_review": True,
                    "control_number": source_control, "scanner_submission_id": scanner_id,
                    "scan_attempt_id": attempt.get("scan_attempt_id", ""),
                    "included_in_analysis": False,
                    "message": "The scan was recorded, but the control number is not registered and was excluded from analysis.",
                }

            if validity == "invalid" or barcode_status.casefold().replace("_", " ") == "crossed out":
                attempt = self.server.mrs_registry.record_scan_attempt(
                    control_number=source_control, barcode_status=barcode_status,
                    scanner_submission_id=scanner_id, scanner_operator=operator_record,
                    validity="invalid", invalid_reason=invalid_reason or "Respondent mistake or spoiled form",
                    analysis_status="excluded", scanned_at=payload.get("verified_at"),
                    print_attempt_reference=print_reference, image_sha256=image_sha256,
                )
                return {
                    "accepted": True, "recorded": True, "control_number": source_control,
                    "scanner_submission_id": scanner_id, "scan_attempt_id": attempt.get("scan_attempt_id", ""),
                    "included_in_analysis": False, "replacement_queued": True,
                    "message": "The spoiled form was recorded as invalid, excluded from analysis, and queued for reprinting.",
                }

            if bool(registered.get("valid_response_received")):
                attempt = self.server.mrs_registry.record_scan_attempt(
                    control_number=source_control, barcode_status=barcode_status,
                    scanner_submission_id=scanner_id, scanner_operator=operator_record,
                    validity="duplicate", invalid_reason="A valid response already exists for this control number",
                    analysis_status="excluded", scanned_at=payload.get("verified_at"),
                    print_attempt_reference=print_reference, image_sha256=image_sha256,
                )
                return {
                    "accepted": False, "recorded": True, "duplicate": True,
                    "control_number": source_control, "scanner_submission_id": scanner_id,
                    "scan_attempt_id": attempt.get("scan_attempt_id", ""),
                    "included_in_analysis": False,
                    "message": "The duplicate scan was logged and excluded from analysis.",
                }

            if str(registered.get("current_print_status") or "") != "Printed":
                attempt = self.server.mrs_registry.record_scan_attempt(
                    control_number=source_control, barcode_status=barcode_status,
                    scanner_submission_id=scanner_id, scanner_operator=operator_record,
                    validity="held", invalid_reason="No confirmed successful print attempt exists",
                    analysis_status="pending", scanned_at=payload.get("verified_at"),
                    print_attempt_reference=print_reference, image_sha256=image_sha256,
                )
                return {
                    "accepted": False, "recorded": True, "held_for_review": True,
                    "control_number": source_control, "scanner_submission_id": scanner_id,
                    "scan_attempt_id": attempt.get("scan_attempt_id", ""),
                    "included_in_analysis": False,
                    "message": "The scan was held because the registry has no confirmed successful print attempt.",
                }

            cleaned_meta = cleaned.get("meta") if isinstance(cleaned.get("meta"), dict) else {}
            cleaned_meta["source_form_control_number"] = source_control
            cleaned_meta["barcode_status"] = barcode_status
            cleaned_meta["response_validity"] = "Valid"
            cleaned_meta["analysis_status"] = "Included"
            cleaned_meta["print_attempt_reference"] = print_reference
            cleaned["meta"] = cleaned_meta
            cleaned["survey_date"] = survey_date
            cleaned["control_number"] = source_control
            saved = self.server.store.add(cleaned)
            attempt = self.server.mrs_registry.record_scan_attempt(
                control_number=source_control, barcode_status=barcode_status,
                scanner_submission_id=scanner_id, scanner_operator=operator_record,
                validity="valid", analysis_status="included", response_record_id=str(saved.get("id") or ""),
                scanned_at=payload.get("verified_at"), print_attempt_reference=print_reference, image_sha256=image_sha256,
            )
        if self.server.response_callback is not None:
            self.server.response_callback(deepcopy(saved))
        return {
            "accepted": True, "recorded": True, "record_id": saved.get("id", ""),
            "control_number": source_control, "scanner_submission_id": scanner_id,
            "scan_attempt_id": attempt.get("scan_attempt_id", ""),
            "included_in_analysis": True,
            "message": "The registered MRS response was verified and included in analysis.",
        }

    def _validated_scanner_record(
        self,
        payload: Mapping[str, Any],
        settings: Mapping[str, Any],
        operator: Mapping[str, Any],
        session: Mapping[str, Any],
    ) -> tuple[dict[str, Any], str, str, str]:
        if str(payload.get("source") or "").strip().casefold() != "scanned_hardcopy":
            raise RequestValidationError("The submission source must be scanned_hardcopy.")
        if str(payload.get("verification_status") or "").strip().casefold() != "verified":
            raise RequestValidationError("Only verified scanner records may be submitted.")
        verification = payload.get("verification") or {}
        if not isinstance(verification, Mapping) or not bool(verification.get("operator_review_completed")):
            raise RequestValidationError("Operator review must be completed in the CSM Sheet Scanner.")
        scanner_id = " ".join(str(payload.get("scanner_submission_id") or "").split())[:120]
        if not scanner_id:
            raise RequestValidationError("A scanner submission ID is required.")
        responses = payload.get("responses") or {}
        if not isinstance(responses, Mapping):
            raise RequestValidationError("Scanner responses must be a JSON object.")
        template = payload.get("template") or {}
        if not isinstance(template, Mapping):
            template = {}
        template_id = " ".join(str(template.get("template_id") or "").split())
        if not (template_id.startswith("CSM-MRS-A4-2026-04-") or template_id.startswith("CSM-A4-ONSITE-")):
            raise RequestValidationError("The scanner template is not a supported MRS form.")

        requested_control = normalize_mrs_control_number(responses.get("control_number"))[:120]
        if not requested_control:
            raise RequestValidationError("The printed MRS control number is required.")
        validity = str(payload.get("response_validity") or "valid").strip().casefold()
        barcode_status = str(payload.get("barcode_status") or responses.get("barcode_status") or "Uncertain").strip().casefold().replace("_", " ")
        spoiled = validity == "invalid" or barcode_status == "crossed out"
        if spoiled:
            survey_date = str(responses.get("survey_date") or date.today().isoformat())
            try:
                survey_date = date.fromisoformat(survey_date).isoformat()
            except ValueError as exc:
                raise RequestValidationError("Survey date must use YYYY-MM-DD.") from exc
            scanner_job = payload.get("scanner_job") if isinstance(payload.get("scanner_job"), Mapping) else {}
            recognition = payload.get("recognition") if isinstance(payload.get("recognition"), Mapping) else {}
            minimal_record = {
                "source_code": "MRS",
                "mode": "onsite",
                "meta": {
                    "submission_source": "scanned_hardcopy",
                    "scanner_submission_id": scanner_id,
                    "scanner_template_id": template_id,
                    "scanner_operator_user_id": str(operator.get("user_id") or ""),
                    "scanner_operator_username": str(operator.get("username") or ""),
                    "scanner_operator_display_name": str(operator.get("display_name") or ""),
                    "scanner_session_id": str(session.get("session_id") or ""),
                    "scanner_job_id": str(payload.get("scanner_job_id") or scanner_job.get("job_id") or ""),
                    "scanner_image_sha256": str(scanner_job.get("image_sha256") or ""),
                    "scanner_processing_engine_version": str(recognition.get("engine_version") or ""),
                    "response_validity": "Invalid",
                    "analysis_status": "Excluded",
                },
                "cc": {},
                "sqd": {},
                "feedback": {"comments": str(responses.get("comments") or ""), "email": ""},
            }
            return minimal_record, scanner_id, requested_control, survey_date

        client_type = SCANNER_CLIENT_TYPE_MAP.get(str(responses.get("client_type") or ""))
        sex = SCANNER_SEX_MAP.get(str(responses.get("sex") or ""))
        service = SCANNER_SERVICE_MAP.get(str(responses.get("service_availed") or ""))
        missing_meta = []
        if client_type is None:
            missing_meta.append("client type")
        if sex is None:
            missing_meta.append("sex")
        if service is None:
            missing_meta.append("service availed")
        if missing_meta:
            raise RequestValidationError("The verified scanner record is missing a valid " + ", ".join(missing_meta) + ".")

        cc: dict[str, int] = {}
        for key in ("cc1", "cc2", "cc3"):
            try:
                cc[key] = int(responses.get(key))
            except (TypeError, ValueError) as exc:
                raise RequestValidationError(f"{key.upper()} is missing or invalid.") from exc
        self._validate_cc("onsite", cc)
        cc = apply_cc_branching("onsite", cc)

        sqd: dict[str, int] = {}
        for number in range(9):
            key = f"sqd{number}"
            try:
                rating = int(responses.get(key))
            except (TypeError, ValueError) as exc:
                raise RequestValidationError(f"{key.upper()} is missing or invalid.") from exc
            if rating not in {0, 1, 2, 3, 4, 5}:
                raise RequestValidationError(f"{key.upper()} must be from 0 to 5.")
            sqd[key] = rating

        age_bracket = str(responses.get("age_bracket") or "").strip()
        valid_age_brackets = {"19_or_lower", "20_34", "35_49", "50_64", "65_or_higher", "did_not_specify"}
        if age_bracket not in valid_age_brackets:
            raise RequestValidationError("A valid age bracket is required.")
        region_value = str(responses.get("region") or "").strip()
        if region_value not in REGION_CODES:
            raise RequestValidationError("A valid region of residence is required.")
        clean_age = None
        recognition = payload.get("recognition") or {}
        if not isinstance(recognition, Mapping):
            recognition = {}
        scanner_app = payload.get("scanner_app") or {}
        if not isinstance(scanner_app, Mapping):
            scanner_app = {}
        scanner_job = payload.get("scanner_job") or {}
        if not isinstance(scanner_job, Mapping):
            scanner_job = {}
        scanner_job_id = " ".join(str(payload.get("scanner_job_id") or scanner_job.get("job_id") or "").split())[:120]
        survey_date = str(responses.get("survey_date") or settings.get("survey_date") or date.today().isoformat())
        try:
            survey_date = date.fromisoformat(survey_date).isoformat()
        except ValueError as exc:
            raise RequestValidationError("Survey date must use YYYY-MM-DD.") from exc
        preview_path = self._store_scanner_preview(payload, scanner_id, bool(settings.get("scanner_store_preview", True)))
        meta: dict[str, Any] = {
            "client_type": client_type,
            "sex": sex,
            "region": region_value,
            "age_bracket": age_bracket,
            "service_availed": [service],
            "submission_source": "scanned_hardcopy",
            "scanner_submission_id": scanner_id,
            "scanner_template_id": template_id,
            "scanner_language": str(template.get("language") or ""),
            "scanner_verified_at": str(payload.get("verified_at") or ""),
            "scanner_app_name": str(scanner_app.get("name") or "CSM MRS Sheet Scanner"),
            "scanner_app_version": str(scanner_app.get("version") or ""),
            "scanner_overall_confidence": recognition.get("overall_confidence"),
            "scanner_verification": "Verified in CSM Sheet Scanner",
            "scanner_operator_user_id": str(operator.get("user_id") or ""),
            "scanner_operator_username": str(operator.get("username") or ""),
            "scanner_operator_display_name": str(operator.get("display_name") or ""),
            "scanner_session_id": str(session.get("session_id") or ""),
            "scanner_capture_session_id": str(scanner_job.get("capture_session_id") or ""),
            "scanner_job_id": scanner_job_id,
            "scanner_image_uploaded_at": str(scanner_job.get("uploaded_at") or ""),
            "scanner_processing_started_at": str(scanner_job.get("processing_started_at") or ""),
            "scanner_processing_completed_at": str(scanner_job.get("processing_completed_at") or ""),
            "scanner_review_completed_at": str(scanner_job.get("review_completed_at") or ""),
            "scanner_finalized_at": str(payload.get("verified_at") or ""),
            "scanner_processing_engine_version": str(recognition.get("engine_version") or scanner_app.get("version") or ""),
            "scanner_image_sha256": str(scanner_job.get("image_sha256") or ""),
        }
        if clean_age is not None:
            meta["age"] = clean_age
        if preview_path:
            meta["scanner_preview_path"] = preview_path
        if scanner_job_id:
            safe_prefix = f"data/csm_survey/scanner_jobs/{scanner_job_id}/"
            for source_key, meta_key in (
                ("original_path", "scanner_original_image_path"),
                ("source_path", "scanner_source_image_path"),
                ("canonical_path", "scanner_canonical_image_path"),
                ("corrected_path", "scanner_corrected_image_path"),
            ):
                relative = str(scanner_job.get(source_key) or "").replace("\\", "/")
                if relative.startswith(safe_prefix) and ".." not in relative.split("/"):
                    meta[meta_key] = relative
            if bool(settings.get("scanner_store_preview", True)) and meta.get("scanner_corrected_image_path"):
                meta["scanner_preview_path"] = meta["scanner_corrected_image_path"]
        record = {
            "source_code": "MRS",
            "mode": "onsite",
            "meta": meta,
            "cc": cc,
            "sqd": sqd,
            "feedback": {"comments": str(responses.get("comments") or ""), "email": ""},
        }
        return record, scanner_id, requested_control, survey_date

    def _store_scanner_preview(self, payload: Mapping[str, Any], scanner_id: str, enabled: bool) -> str:
        value = str(payload.get("corrected_preview") or "")
        if not enabled or not value:
            return ""
        prefix = "data:image/jpeg;base64,"
        if not value.startswith(prefix):
            raise RequestValidationError("The corrected scanner preview must be a JPEG data URL.")
        try:
            data = base64.b64decode(value[len(prefix):], validate=True)
        except (ValueError, binascii.Error) as exc:
            raise RequestValidationError("The corrected scanner preview is invalid.") from exc
        if len(data) > 2 * 1024 * 1024:
            raise RequestValidationError("The corrected scanner preview is too large.")
        safe_name = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in scanner_id).strip("-")[:100] or secrets.token_hex(8)
        relative = Path("data") / "csm_survey" / "scanner_previews" / f"{safe_name}.jpg"
        target = (self.server.data_root / relative).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return relative.as_posix()

    def _save_submission(self, payload: dict[str, Any], settings: Mapping[str, Any], session_token: str) -> dict[str, Any]:
        token = " ".join(str(payload.get("submission_token") or "").split())[:120]
        with self.server.submission_lock:
            if token and token in self.server.submission_cache:
                cached = deepcopy(self.server.submission_cache[token])
                cached["duplicate"] = True
                return cached

            status = str(settings.get("survey_status") or "offline").casefold()
            if status == "maintenance":
                raise SurveyUnavailableError("The Survey Form is under maintenance.", code="maintenance")
            if status != "online":
                raise SurveyUnavailableError("The Survey Form is offline and is not accepting responses.", code="offline")
            valid_session, _ = self.server.validate_session(session_token)
            if not valid_session:
                raise SessionExpiredError("Your survey-access session has expired. Please scan the current survey QR code again.")

            mode = str(settings.get("active_mode") or "onsite").strip().casefold()
            submitted_mode = str(payload.get("mode") or mode).strip().casefold()
            if submitted_mode != mode:
                raise RequestValidationError("The active questionnaire changed. Reload the Survey Form.")

            acknowledgement = payload.get("privacy_acknowledgement")
            if not isinstance(acknowledgement, Mapping):
                raise RequestValidationError(
                    "Please acknowledge the privacy notice before submitting the survey."
                )
            acknowledged_version = str(
                acknowledgement.get("notice_version") or ""
            ).strip()
            if (
                not bool(acknowledgement.get("accepted"))
                or acknowledged_version != PRIVACY_NOTICE_VERSION
            ):
                raise RequestValidationError(
                    "The privacy notice changed. Review and acknowledge the current notice before submitting."
                )
            cleaned = self._validated_record(payload, mode)
            cleaned_meta = cleaned.get("meta")
            if isinstance(cleaned_meta, dict):
                cleaned_meta["privacy_notice_version"] = PRIVACY_NOTICE_VERSION
                cleaned_meta["privacy_acknowledged_at"] = datetime.now(
                    timezone.utc
                ).isoformat(timespec="seconds")
            survey_date = str(settings.get("survey_date") or date.today().isoformat())
            try:
                school_id = validate_school_id(settings.get("school_id"), required=True)
            except ValueError as exc:
                raise RequestValidationError(str(exc)) from exc
            cleaned["survey_date"] = survey_date
            cleaned["source_code"] = "WBS"
            saved = self.server.store.add_with_next_control_number(
                cleaned,
                survey_date,
                school_id,
                "WBS",
            )

            response = {
                "ok": True,
                "record_id": saved.get("id", ""),
                "control_number": saved.get("control_number", ""),
                "message": "Your response was recorded successfully.",
                "session_expired": True,
            }
            self.server.remember_submission(token, response)
            self.server.expire_session(session_token)

        if self.server.response_callback is not None:
            self.server.response_callback(deepcopy(saved))
        return response

    def _validated_record(self, payload: Mapping[str, Any], mode: str) -> dict[str, Any]:
        meta = payload.get("meta") or {}
        cc = payload.get("cc") or {}
        sqd = payload.get("sqd") or {}
        feedback = payload.get("feedback") or {}
        if not isinstance(meta, Mapping):
            raise RequestValidationError("Client information is invalid.")
        if not isinstance(cc, Mapping):
            raise RequestValidationError("Citizen's Charter answers are invalid.")
        if not isinstance(sqd, Mapping):
            raise RequestValidationError("Service-quality answers are invalid.")
        if not isinstance(feedback, Mapping):
            feedback = {"comments": str(feedback or "")}

        normalized_cc: dict[str, Any] = {}
        for key in ("cc1", "cc2", "cc3"):
            value = cc.get(key)
            if value in (None, ""):
                continue
            try:
                normalized_cc[key] = int(value)
            except (TypeError, ValueError) as exc:
                raise RequestValidationError(f"{key.upper()} has an invalid answer.") from exc
        reason = " ".join(str(cc.get("cc3_reason") or "").split())
        if reason:
            normalized_cc["cc3_reason"] = reason
        self._validate_cc(mode, normalized_cc)
        normalized_cc = apply_cc_branching(mode, normalized_cc)

        normalized_sqd: dict[str, int] = {}
        missing: list[str] = []
        allowed = {0, 1, 2, 3, 4, 5} if mode == "onsite" else {1, 2, 3, 4, 5}
        for key in SQD_QUESTIONS[mode]:
            value = sqd.get(key)
            try:
                number = int(value)
            except (TypeError, ValueError):
                missing.append(key.upper())
                continue
            if number not in allowed:
                missing.append(key.upper())
                continue
            normalized_sqd[key] = number
        if missing:
            raise RequestValidationError("Please answer all service-quality items: " + ", ".join(missing) + ".")

        clean_meta = deepcopy(dict(meta))
        clean_meta["submission_source"] = "local_web_form"
        clean_meta.pop("control_number", None)
        clean_meta.pop("control_no", None)
        try:
            clean_meta = normalize_demographics(clean_meta, strict=True)
            selected_services = normalize_service_values(
                clean_meta.get("service_availed"),
                strict=True,
            )
        except ValueError as exc:
            raise RequestValidationError(str(exc)) from exc
        if not selected_services:
            raise RequestValidationError(
                "Please select at least one school transaction."
            )
        clean_meta["service_availed"] = selected_services
        return {
            "mode": mode,
            "meta": clean_meta,
            "cc": normalized_cc,
            "sqd": normalized_sqd,
            "feedback": {
                "comments": str(feedback.get("comments") or feedback.get("text") or ""),
                "email": str(feedback.get("email") or ""),
            },
        }

    @staticmethod
    def _validate_cc(mode: str, answers: Mapping[str, Any]) -> None:
        cc1 = answers.get("cc1")
        valid_cc1 = {option["value"] for option in CC_QUESTIONS[mode]["cc1"]["options"]}
        if cc1 not in valid_cc1:
            raise RequestValidationError("Please answer CC1.")
        if mode == "onsite":
            if cc1 == 4:
                return
            for key in ("cc2", "cc3"):
                valid = {option["value"] for option in CC_QUESTIONS[mode][key]["options"]}
                if answers.get(key) not in valid:
                    raise RequestValidationError(f"Please answer {key.upper()}.")
            return
        if cc1 == 3:
            return
        valid_cc2 = {option["value"] for option in CC_QUESTIONS[mode]["cc2"]["options"]}
        if answers.get("cc2") not in valid_cc2:
            raise RequestValidationError("Please answer CC2.")
        if answers.get("cc2") == 3:
            return
        valid_cc3 = {option["value"] for option in CC_QUESTIONS[mode]["cc3"]["options"]}
        if answers.get("cc3") not in valid_cc3:
            raise RequestValidationError("Please answer CC3.")

    def _session_cookie(self) -> str:
        raw = self.headers.get("Cookie", "")
        cookie = SimpleCookie()
        try:
            cookie.load(raw)
        except Exception:
            return ""
        morsel = cookie.get(SESSION_COOKIE)
        return morsel.value if morsel is not None else ""

    def _scanner_session_cookie(self) -> str:
        raw = self.headers.get("Cookie", "")
        cookie = SimpleCookie()
        try:
            cookie.load(raw)
        except Exception:
            return ""
        morsel = cookie.get(SCANNER_SESSION_COOKIE)
        return morsel.value if morsel is not None else ""

    def _clear_session_cookie(self) -> None:
        self._pending_clear_cookie = True

    def _clear_scanner_session_cookie(self) -> None:
        self._pending_clear_scanner_cookie = True

    def _serve_school_logo(self) -> None:
        settings = self.server.settings()
        relative = str(settings.get("school_logo_path") or "").replace("\\", "/").lstrip("/")
        if not relative or ".." in Path(relative).parts:
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "School logo not configured."})
            return
        data_root = self.server.data_root
        path = (data_root / relative).resolve()
        try:
            path.relative_to(data_root)
        except ValueError:
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "School logo not found."})
            return
        if not path.is_file():
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "School logo not found."})
            return
        data = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def _serve_static(self, relative_name: str) -> None:
        clean = relative_name.replace("\\", "/").lstrip("/")
        if not clean or ".." in Path(clean).parts:
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "File not found."})
            return
        path = (self.server.static_root / clean).resolve()
        try:
            path.relative_to(self.server.static_root)
        except ValueError:
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "File not found."})
            return
        if not path.is_file():
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "File not found."})
            return
        mime = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".png": "image/png",
            ".svg": "image/svg+xml",
            ".ico": "image/x-icon",
        }.get(path.suffix.casefold(), "application/octet-stream")
        data = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store" if path.suffix == ".html" else "public, max-age=300")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(data)

    def _serve_file(self, path: Path, mime_type: str, *, cache_control: str = "no-store") -> None:
        if not path.is_file():
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "File not found."})
            return
        data = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mime_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache_control)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, status: HTTPStatus, payload: Mapping[str, Any], *, scanner_cors: bool = False) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if scanner_cors:
            self._send_scanner_cors_headers()
        pending_scanner_cookie = getattr(self, "_pending_scanner_cookie", None)
        if pending_scanner_cookie:
            token, maximum = pending_scanner_cookie
            self.send_header(
                "Set-Cookie",
                f"{SCANNER_SESSION_COOKIE}={token}; Path=/; Max-Age={int(maximum)}; HttpOnly; SameSite=Lax",
            )
            self._pending_scanner_cookie = None
        if getattr(self, "_pending_clear_cookie", False):
            self.send_header("Set-Cookie", f"{SESSION_COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax")
            self._pending_clear_cookie = False
        if getattr(self, "_pending_clear_scanner_cookie", False):
            self.send_header("Set-Cookie", f"{SCANNER_SESSION_COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax")
            self._pending_clear_scanner_cookie = False
        self.end_headers()
        self.wfile.write(data)

    def _send_scanner_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Scanner-Key")
        self.send_header("Access-Control-Max-Age", "600")

    def _redirect(self, location: str) -> None:
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()


class RequestValidationError(ValueError):
    pass


class DuplicateScannerSubmissionError(ValueError):
    pass


class ScannerAuthorizationError(PermissionError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


class SessionExpiredError(ValueError):
    pass


class SurveyUnavailableError(RuntimeError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code
