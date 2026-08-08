"""Qt bridge for the School CSM Control Center local survey server."""

from __future__ import annotations

from datetime import date
from pathlib import Path
import ipaddress
import re
import secrets
import socket
import subprocess
import time
from threading import RLock, Thread
from typing import Any

from PySide6.QtCore import QObject, Signal

from school_csm_control_center.storage.control_center_settings import (
    ControlCenterSettingsStore,
    normalize_school_identifier,
    validate_school_id,
    validate_school_identifier,
)
from school_csm_control_center.storage.scanner_security import ScannerOperatorStore
from school_csm_control_center.storage.survey_store import SurveyStore
from school_csm_control_center.web_server.server import SurveyHTTPServer
from school_csm_control_center.web_server.captive_portal import CaptivePortalService, remove_probe_hosts
from school_csm_control_center.web_server.access_codes import (
    build_access_urls,
    build_named_portal_url,
    build_direct_portal_url,
    build_wifi_qr_payload,
)
from school_csm_control_center.windows_admin import (
    hidden_process_creation_flags,
    is_administrator,
    is_windows,
    run_firewall_script,
)


class SurveyServerController(QObject):
    status_changed = Signal(str, str)
    survey_status_changed = Signal(str, str)
    response_received = Signal(object)
    response_count_changed = Signal(int)
    scanner_response_count_changed = Signal(int)
    active_sessions_changed = Signal(int)
    urls_changed = Signal(str, str, str, str)
    settings_changed = Signal(object)
    access_status_changed = Signal(object)
    portal_status_changed = Signal(object)

    def __init__(self, store: SurveyStore, project_root: str | Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.store = store
        self.project_root = Path(project_root)
        self.static_root = self.project_root / "school_csm_control_center" / "web_server" / "static"
        self.settings_store = ControlCenterSettingsStore(self.project_root)
        self.scanner_operator_store = ScannerOperatorStore(self.project_root)
        self._state_lock = RLock()
        self._operation_lock = RLock()
        self._operation = ""
        self._operation_thread: Thread | None = None
        self._server: SurveyHTTPServer | None = None
        self._thread: Thread | None = None
        self._response_count = 0
        self._scanner_response_count = 0
        self._port = 0
        self._settings = self.settings_store.load()
        self._firewall_status = "Not checked"
        self._firewall_detail = "Firewall setup will run when the server starts."
        self._captive_portal: CaptivePortalService | None = None
        if is_windows():
            remove_probe_hosts()
        self._portal_status: dict[str, Any] = {
            "status": "Stopped",
            "automatic_open": False,
            "http_running": False,
            "dns_running": False,
            "detail": "Captive Portal starts with the local survey server in Laptop Hotspot mode.",
        }
        if not self._settings.get("survey_date"):
            self._settings["survey_date"] = date.today().isoformat()
        if not self._settings.get("public_access_key"):
            self._settings["public_access_key"] = secrets.token_urlsafe(18)
        if not self._settings.get("scanner_access_key"):
            self._settings["scanner_access_key"] = secrets.token_urlsafe(24)
        # The web service is not running after an application restart.
        self._settings["survey_status"] = str(self._settings.get("survey_status") or "offline")
        self._persist()

    @property
    def running(self) -> bool:
        return self._server is not None and self._thread is not None and self._thread.is_alive()

    @property
    def port(self) -> int:
        return self._port

    @property
    def response_count(self) -> int:
        return self._response_count

    def settings(self) -> dict[str, Any]:
        with self._state_lock:
            values = dict(self._settings)
        values["server_running"] = self.running
        values["local_ip"] = self.selected_local_ip()
        values["named_hostname"] = self.hostname()
        values["active_sessions"] = self._server.active_session_count() if self._server is not None else 0
        values["administrator_active"] = is_administrator()
        values["firewall_status"] = self._firewall_status
        values["firewall_detail"] = self._firewall_detail
        values["hotspot"] = self.hotspot_status()
        values["captive_portal"] = dict(self._portal_status)
        values["scanner_operator_count"] = len(self.scanner_operator_store.list_accounts())
        values["scanner_queue"] = self._server.scanner_queue_status() if self._server is not None else {
            "active_jobs": 0,
            "waiting_jobs": 0,
            "processing_limit": int(values.get("scanner_processing_limit") or 1),
        }
        return values

    def reload_persistent_settings(self) -> dict[str, Any]:
        """Reload school/profile settings without interrupting a running server."""
        loaded = self.settings_store.load()
        with self._state_lock:
            self._settings.update(loaded)
            current = dict(self._settings)
        self.settings_changed.emit(current)
        self._emit_urls()
        return current


    def available_server_addresses(self, *, refresh: bool = False) -> list[tuple[str, str]]:
        return local_ipv4_candidates(force_refresh=refresh)

    @property
    def operation_in_progress(self) -> bool:
        with self._operation_lock:
            return bool(self._operation)

    def start_async(self, preferred_port: int | None = None, fallback_port: int = 8080) -> bool:
        """Start firewall/server/portal work outside the Qt GUI thread."""
        with self._operation_lock:
            if self.running or self._operation:
                return False
            self._operation = "starting"
        self.status_changed.emit("starting", "Preparing Windows Firewall and the local survey server…")
        worker = Thread(
            target=self._start_worker,
            args=(preferred_port, fallback_port),
            name="SchoolCSMServerStartup",
            daemon=True,
        )
        self._operation_thread = worker
        worker.start()
        return True

    def _start_worker(self, preferred_port: int | None, fallback_port: int) -> None:
        try:
            self.start(preferred_port, fallback_port)
        except OSError:
            # start() already emitted the actionable error message.
            pass
        except Exception as exc:
            self.status_changed.emit("error", f"Unable to start the local survey server: {exc}")
        finally:
            with self._operation_lock:
                self._operation = ""
                self._operation_thread = None

    def stop_async(self) -> bool:
        """Stop network services without freezing or repainting the dashboard."""
        with self._operation_lock:
            if self._operation:
                return False
            if not self.running:
                self.status_changed.emit("offline", "Local survey server is stopped.")
                return False
            self._operation = "stopping"
        self.status_changed.emit("stopping", "Stopping the local survey server…")
        worker = Thread(
            target=self._stop_worker,
            name="SchoolCSMServerShutdown",
            daemon=True,
        )
        self._operation_thread = worker
        worker.start()
        return True

    def _stop_worker(self) -> None:
        try:
            self.stop()
        except Exception as exc:
            self.status_changed.emit("error", f"Unable to stop the local survey server cleanly: {exc}")
        finally:
            with self._operation_lock:
                self._operation = ""
                self._operation_thread = None

    def selected_local_ip(self) -> str:
        configured = str(self._settings.get("server_ip") or "").strip()
        candidates = local_ipv4_candidates()
        addresses = [address for _label, address in candidates]
        if configured and configured in addresses:
            return configured
        if str(self._settings.get("network_mode") or "hotspot") == "hotspot":
            hotspot = self.preferred_hotspot_ip()
            if hotspot:
                return hotspot
        return addresses[0] if addresses else "127.0.0.1"

    def set_server_ip(self, address: str) -> str:
        value = str(address or "").strip()
        candidates = [candidate for _label, candidate in local_ipv4_candidates()]
        if value and value not in candidates:
            raise ValueError("The selected IPv4 address is not currently available on this laptop.")
        self._update(server_ip=value)
        self._emit_urls()
        return self.selected_local_ip()

    def hostname(self) -> str:
        identifier = str(self._settings.get("school_identifier") or "school-csm")
        return f"csm.{identifier}.home.arpa"


    def set_network_mode(self, mode: str) -> None:
        value = str(mode or "hotspot").strip().casefold()
        if value not in {"hotspot", "existing_wifi"}:
            raise ValueError("Network mode must be Laptop Hotspot or Existing Wi-Fi.")
        self._update(network_mode=value)
        if value == "hotspot":
            hotspot = self.preferred_hotspot_ip()
            if hotspot:
                self._update(server_ip=hotspot)
        self._emit_urls()

    def set_hotspot_internet_mode(self, mode: str) -> None:
        value = str(mode or "survey_only").strip().casefold()
        if value not in {"survey_only", "share_internet"}:
            raise ValueError("Hotspot internet mode must be Survey Network Only or Share Laptop Internet.")
        if value == "share_internet" and str(self._settings.get("access_mode") or "captive_portal") == "captive_portal":
            raise ValueError("Captive Portal mode currently requires Survey Network Only. Select Two-Step QR before sharing laptop internet.")
        self._update(hotspot_internet_mode=value)

    def set_access_mode(self, mode: str) -> None:
        value = str(mode or "captive_portal").strip().casefold()
        if value not in {"captive_portal", "two_step"}:
            raise ValueError("Access mode must be Captive Portal or Two-Step QR.")
        self._update(access_mode=value, captive_portal_enabled=(value == "captive_portal"))

    def preferred_hotspot_ip(self) -> str:
        for label, address in local_ipv4_candidates():
            if _is_hotspot_candidate(address, label):
                return address
        return ""

    def hotspot_status(self) -> dict[str, Any]:
        address = self.preferred_hotspot_ip()
        if address:
            return {"active": True, "ip": address, "status": "Active", "detail": f"Laptop hotspot adapter detected at {address}."}
        return {
            "active": False,
            "ip": "",
            "status": "Not detected",
            "detail": "Turn on Windows Mobile Hotspot, then refresh network addresses.",
        }

    def configure_firewall(self, preferred_port: int, fallback_port: int = 8080) -> dict[str, Any]:
        captive_portal = (
            str(self._settings.get("network_mode") or "hotspot") == "hotspot"
            and str(self._settings.get("access_mode") or "captive_portal")
            == "captive_portal"
        )
        result = run_firewall_script(
            self.project_root,
            preferred_port,
            fallback_port,
            captive_portal=captive_portal,
        )
        self._firewall_status = result.status
        self._firewall_detail = result.detail
        payload = {
            "administrator": (
                "Application elevated"
                if is_administrator()
                else "Helper requests approval only when required"
            ),
            "firewall": result.status,
            "detail": result.detail,
            "ok": result.ok,
        }
        self.access_status_changed.emit(payload)
        return payload

    def set_active_mode(self, mode: str) -> None:
        value = str(mode or "onsite").strip().casefold()
        if value not in {"onsite", "online"}:
            raise ValueError("Survey mode must be onsite or online.")
        self._update(active_mode=value)

    def set_survey_date(self, value: str) -> None:
        self._update(survey_date=date.fromisoformat(str(value)).isoformat())

    def set_survey_status(self, status: str, message: str = "") -> None:
        value = str(status or "offline").strip().casefold()
        if value not in {"online", "offline", "maintenance"}:
            raise ValueError("Survey status must be Online, Offline, or Under Maintenance.")
        if value == "online":
            validate_school_id(self._settings.get("school_id"), required=True)
        self._update(survey_status=value, status_message=str(message or "").strip())
        default_message = {
            "online": "The CSM Survey Form is online and accepting authorized responses.",
            "offline": "The CSM Survey Form is offline.",
            "maintenance": "The CSM Survey Form is under maintenance.",
        }[value]
        self.survey_status_changed.emit(value, message or default_message)

    def set_school_identifier(self, value: str) -> str:
        normalized = normalize_school_identifier(value)
        validated = validate_school_identifier(normalized)
        self._update(school_identifier=validated)
        self._emit_urls()
        return validated

    def set_school_name(self, value: str) -> None:
        self._update(school_name=" ".join(str(value or "").split()) or "School")

    def set_session_duration(self, seconds: int) -> None:
        self._update(session_duration_seconds=max(60, min(3600, int(seconds))))

    def set_network_profile(self, *, ssid: str, security: str, password: str, hidden: bool) -> None:
        normalized_security = str(security or "WPA").strip().upper()
        if normalized_security not in {"WPA", "WEP", "NOPASS"}:
            normalized_security = "WPA"
        self._update(
            network_ssid=str(ssid or "").strip(),
            network_security=normalized_security,
            network_password=str(password or ""),
            network_hidden=bool(hidden),
        )

    def regenerate_public_access_key(self) -> str:
        key = secrets.token_urlsafe(18)
        self._update(public_access_key=key)
        if self._server is not None:
            self._server.revoke_all_sessions()
        self._emit_urls()
        return key

    def set_scanner_intake_enabled(self, enabled: bool) -> None:
        self._update(scanner_intake_enabled=bool(enabled))

    def set_scanner_remote_enabled(self, enabled: bool) -> None:
        self._update(scanner_remote_enabled=bool(enabled))

    def set_scanner_processing_limit(self, value: int) -> int:
        limit = max(1, min(10, int(value)))
        self._update(scanner_processing_limit=limit)
        if self._server is not None:
            self._server.scanner_limit_changed()
        return limit

    def set_scanner_store_preview(self, enabled: bool) -> None:
        self._update(scanner_store_preview=bool(enabled))

    def regenerate_scanner_access_key(self) -> str:
        key = secrets.token_urlsafe(24)
        self._update(scanner_access_key=key)
        return key

    def scanner_operator_accounts(self) -> list[dict[str, Any]]:
        return self.scanner_operator_store.list_accounts()

    def save_scanner_operator(
        self,
        *,
        user_id: str = "",
        username: str,
        display_name: str,
        password: str = "",
        enabled: bool = True,
    ) -> dict[str, Any]:
        account = self.scanner_operator_store.save_account(
            user_id=user_id,
            username=username,
            display_name=display_name,
            password=password,
            enabled=enabled,
        )
        if not enabled and self._server is not None:
            self._server.revoke_scanner_sessions_for_user(str(account.get("user_id") or ""))
        return account

    def archive_scanner_operator(self, user_id: str) -> dict[str, Any]:
        account = self.scanner_operator_store.archive(user_id)
        if self._server is not None:
            self._server.revoke_scanner_sessions_for_user(str(account.get("user_id") or ""))
        return account

    def start(self, preferred_port: int | None = None, fallback_port: int = 8080) -> int:
        if self.running:
            return self._port
        self.status_changed.emit("starting", "Configuring Windows Firewall and starting the local survey server…")
        with self._state_lock:
            self._response_count = 0
            self._scanner_response_count = 0
        self.response_count_changed.emit(0)
        self.scanner_response_count_changed.emit(0)
        preferred = int(preferred_port or self._settings.get("preferred_port") or 8080)
        if (
            str(self._settings.get("network_mode") or "hotspot") == "hotspot"
            and str(self._settings.get("access_mode") or "captive_portal") == "captive_portal"
            and preferred == 80
        ):
            preferred = 8080
        self._update(preferred_port=preferred)
        firewall = self.configure_firewall(preferred, fallback_port)
        last_error: OSError | None = None
        ports: list[int] = []
        for candidate in (preferred, fallback_port):
            if candidate not in ports:
                ports.append(candidate)
        for port in ports:
            try:
                bind_address = self.selected_local_ip()
                server = SurveyHTTPServer(
                    (bind_address, port),
                    store=self.store,
                    static_root=self.static_root,
                    settings_provider=self.settings,
                    project_root=self.project_root,
                    response_callback=self._handle_response,
                    session_callback=self._handle_session_count,
                    scanner_operator_store=self.scanner_operator_store,
                )
            except OSError as exc:
                last_error = exc
                continue
            self._server = server
            self._port = int(server.server_address[1])
            self._thread = Thread(target=server.serve_forever, name="SchoolCSMLocalSurveyServer", daemon=True)
            self._thread.start()
            self._emit_urls()
            portal = self._start_captive_portal_if_enabled()
            if firewall.get("ok"):
                message = f"Local survey server is running on port {self._port}; Windows Firewall access was configured."
            else:
                message = f"Local survey server is running on port {self._port}, but firewall setup reported: {firewall.get('firewall')}."
            if str(self._settings.get("network_mode") or "hotspot") == "hotspot" and not self.preferred_hotspot_ip():
                message += " Turn on Laptop Hotspot before giving the Wi-Fi QR code to respondents."
            if portal.get("status") == "Active":
                message += " Captive Portal automatic opening is active."
            elif str(self._settings.get("access_mode") or "captive_portal") == "captive_portal":
                message += " Captive Portal is not fully active; use the fallback Survey Form QR."
            self.status_changed.emit("online", message)
            return self._port
        message = f"Unable to start the local survey server: {last_error}" if last_error else "Unable to start the local survey server."
        self.status_changed.emit("error", message)
        raise OSError(message)

    def stop(self) -> None:
        self._stop_captive_portal()
        server = self._server
        thread = self._thread
        if server is None:
            self.status_changed.emit("offline", "Local survey server is stopped.")
            return
        self._server = None
        self._thread = None
        self._port = 0
        try:
            server.shutdown()
            server.server_close()
        finally:
            if thread is not None and thread.is_alive():
                thread.join(timeout=2.0)
        self.active_sessions_changed.emit(0)
        self.urls_changed.emit("", "", "", "")
        self.status_changed.emit("offline", "Local survey server is stopped.")

    def captive_portal_target_url(self) -> str:
        """Return the configured local address used by the phone portal.

        The configured school hostname is preferred when the application owns
        the hotspot DNS responder. When Windows reserves DNS port 53, the portal
        falls back to the hotspot IPv4 address so the network-login page does not
        fail on an unresolved ``csm.<school>.home.arpa`` hostname.
        """

        if not self._port:
            return ""
        # The configured local hostname is used only when the app-owned DNS
        # responder is actually running. Windows hosts-file mappings do not
        # reliably propagate through Mobile Hotspot DNS to connected phones.
        # Falling back to the hotspot IPv4 address prevents the portal from
        # opening and then immediately failing on an unresolved school name.
        if bool(self._portal_status.get("dns_running")):
            return build_named_portal_url(hostname=self.hostname(), port=self._port)
        return build_direct_portal_url(local_ip=self.selected_local_ip(), port=self._port)

    def _start_captive_portal_if_enabled(self) -> dict[str, Any]:
        self._stop_captive_portal(emit=False)
        network_mode = str(self._settings.get("network_mode") or "hotspot").casefold()
        access_mode = str(self._settings.get("access_mode") or "captive_portal").casefold()
        if network_mode != "hotspot" or access_mode != "captive_portal":
            self._portal_status = {
                "status": "Disabled",
                "automatic_open": False,
                "http_running": False,
                "dns_running": False,
                "detail": "Captive Portal is disabled for the selected access mode.",
            }
            self.portal_status_changed.emit(dict(self._portal_status))
            return dict(self._portal_status)
        hotspot_ip = self.preferred_hotspot_ip() or self.selected_local_ip()
        if not hotspot_ip or hotspot_ip == "127.0.0.1":
            self._portal_status = {
                "status": "Hotspot required",
                "automatic_open": False,
                "http_running": False,
                "dns_running": False,
                "detail": "Turn on Windows Mobile Hotspot and refresh the respondent access IP.",
            }
            self.portal_status_changed.emit(dict(self._portal_status))
            return dict(self._portal_status)
        self._captive_portal = CaptivePortalService(
            hotspot_ip=hotspot_ip,
            target_provider=self.captive_portal_target_url,
            local_hostnames=(self.hostname(),),
        )
        status = self._captive_portal.start(http_port=80, dns_port=53)
        self._portal_status = {
            "status": "Active" if status.automatic_open_available else ("Partial" if status.http_running or status.dns_running or status.probe_hosts_configured else "Unavailable"),
            "automatic_open": status.automatic_open_available,
            "http_running": status.http_running,
            "dns_running": status.dns_running,
            "probe_hosts_configured": status.probe_hosts_configured,
            "target_url": status.target_url,
            "detail": status.detail,
        }
        self.portal_status_changed.emit(dict(self._portal_status))
        return dict(self._portal_status)

    def _stop_captive_portal(self, *, emit: bool = True) -> None:
        if self._captive_portal is not None:
            self._captive_portal.stop()
        self._captive_portal = None
        self._portal_status = {
            "status": "Stopped",
            "automatic_open": False,
            "http_running": False,
            "dns_running": False,
            "detail": "Captive Portal is stopped.",
        }
        if emit:
            self.portal_status_changed.emit(dict(self._portal_status))

    def urls(self) -> tuple[str, str, str, str]:
        if not self._port:
            return "", "", "", ""
        return build_access_urls(
            hostname=self.hostname(),
            local_ip=self.selected_local_ip(),
            port=self._port,
            access_key=str(self._settings.get("public_access_key") or ""),
        )

    def wifi_qr_payload(self) -> str:
        settings = self.settings()
        return build_wifi_qr_payload(
            ssid=str(settings.get("network_ssid") or ""),
            security=str(settings.get("network_security") or "WPA"),
            password=str(settings.get("network_password") or ""),
            hidden=bool(settings.get("network_hidden")),
        )

    def detect_current_wifi_ssid(self) -> str:
        try:
            completed = subprocess.run(
                ["netsh", "wlan", "show", "interfaces"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
                creationflags=hidden_process_creation_flags(),
            )
        except (OSError, subprocess.SubprocessError):
            return ""
        for line in completed.stdout.splitlines():
            stripped = line.strip()
            if stripped.lower().startswith("ssid") and not stripped.lower().startswith("bssid") and ":" in stripped:
                return stripped.split(":", 1)[1].strip()
        return ""

    def _handle_response(self, record: dict[str, Any]) -> None:
        meta = record.get("meta") if isinstance(record.get("meta"), dict) else {}
        is_scanner = (
            str(record.get("source_code") or "").upper() == "MRS"
            or str(meta.get("submission_source") or "").casefold() == "scanned_hardcopy"
        )
        with self._state_lock:
            self._response_count += 1
            count = self._response_count
            if is_scanner:
                self._scanner_response_count += 1
            scanner_count = self._scanner_response_count
        self.response_count_changed.emit(count)
        if is_scanner:
            self.scanner_response_count_changed.emit(scanner_count)
        self.response_received.emit(record)

    def _handle_session_count(self, count: int) -> None:
        self.active_sessions_changed.emit(int(count))

    def _update(self, **changes: Any) -> None:
        with self._state_lock:
            self._settings.update(changes)
            snapshot = self._persist()
        self.settings_changed.emit(snapshot)

    def _persist(self) -> dict[str, Any]:
        self._settings = self.settings_store.save(self._settings)
        return dict(self._settings)

    def _emit_urls(self) -> None:
        self.urls_changed.emit(*self.urls())


def _is_hotspot_candidate(address: str, label: str) -> bool:
    text = str(label or "").casefold()
    return (
        str(address) == "192.168.137.1"
        or "hotspot" in text
        or "wi-fi direct" in text
        or "wifi direct" in text
        or "local area connection*" in text
        or "mobile hotspot" in text
    )


_IPV4_CACHE_LOCK = RLock()
_IPV4_CACHE_AT = 0.0
_IPV4_CACHE: list[tuple[str, str]] = []

def local_ipv4_candidates(*, force_refresh: bool = False) -> list[tuple[str, str]]:
    """Return reachable IPv4 addresses without repeatedly spawning ``ipconfig``.

    The short cache removes several redundant console-helper launches while the
    server dashboard is being constructed. ``force_refresh`` is used by the
    operator's explicit Refresh action.
    """
    global _IPV4_CACHE_AT, _IPV4_CACHE
    now = time.monotonic()
    with _IPV4_CACHE_LOCK:
        if not force_refresh and _IPV4_CACHE and (now - _IPV4_CACHE_AT) < 2.0:
            return list(_IPV4_CACHE)

    found: dict[str, str] = {}

    def add(address: str, label: str = "Local network") -> None:
        value = str(address or "").strip()
        try:
            parsed = ipaddress.ip_address(value)
        except ValueError:
            return
        if parsed.version != 4 or parsed.is_loopback or parsed.is_link_local or parsed.is_unspecified:
            return
        found.setdefault(value, label.strip() or "Local network")

    # Windows adapter names make it easier to select Mobile Hotspot versus Wi-Fi.
    try:
        completed = subprocess.run(
            ["ipconfig"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            errors="replace",
            creationflags=hidden_process_creation_flags(),
        )
        adapter = "Local network"
        for raw_line in completed.stdout.splitlines():
            line = raw_line.rstrip()
            if line and not line[:1].isspace() and line.endswith(":"):
                adapter = line[:-1].strip()
                continue
            match = re.search(r"IPv4[^:]*:\s*([0-9]+(?:\.[0-9]+){3})", line, flags=re.IGNORECASE)
            if match:
                add(match.group(1), adapter)
    except (OSError, subprocess.SubprocessError):
        pass

    # Route-selected address.
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("192.0.2.1", 9))
        add(str(probe.getsockname()[0]), "Default network route")
    except OSError:
        pass
    finally:
        probe.close()

    # Hostname resolution fallback.
    try:
        for address in socket.gethostbyname_ex(socket.gethostname())[2]:
            add(address, "Local adapter")
    except OSError:
        pass

    def priority(item: tuple[str, str]) -> tuple[int, int, str]:
        address, label = item
        parsed = ipaddress.ip_address(address)
        hotspot = 0 if _is_hotspot_candidate(address, label) else 1
        private = 0 if parsed.is_private else 1
        return hotspot, private, address

    ordered = sorted(found.items(), key=priority)
    result = [(f"{address} — {label}", address) for address, label in ordered]
    with _IPV4_CACHE_LOCK:
        _IPV4_CACHE = list(result)
        _IPV4_CACHE_AT = time.monotonic()
    return result


def local_ipv4_address() -> str:
    candidates = local_ipv4_candidates()
    return candidates[0][1] if candidates else "127.0.0.1"
