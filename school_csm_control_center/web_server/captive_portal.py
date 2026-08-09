"""Experimental captive-portal services for a laptop-managed survey hotspot.

The service has two independent pieces:

* a tiny wildcard DNS responder that maps A-record lookups to the laptop's
  hotspot IPv4 address; and
* an HTTP listener on port 80 that redirects connectivity probes and ordinary
  browser requests to the CSM Survey Form portal endpoint.

Windows Mobile Hotspot and phone captive-portal behavior vary by adapter and OS.
Callers must treat startup as best effort and retain a direct QR fallback.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import secrets
import subprocess
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import socket
import socketserver
import struct
from threading import Thread
from typing import Callable, Iterable

from school_csm_control_center.windows_admin import hidden_process_creation_flags


@dataclass(frozen=True)
class CaptivePortalStatus:
    http_running: bool
    dns_running: bool
    probe_hosts_configured: bool
    target_url: str
    detail: str
    dns_verified: bool = False
    dns_verification_detail: str = ""

    @property
    def automatic_open_available(self) -> bool:
        # A hosts-file mapping on the laptop is only a best-effort fallback;
        # it does not prove that phones connected to Windows Mobile Hotspot
        # receive the same DNS answer. Automatic opening is reported only when
        # the redirect listener and the app-owned DNS responder are both live.
        return self.http_running and self.dns_running and self.dns_verified


class _PortalHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], target_provider: Callable[[], str]) -> None:
        super().__init__(address, _PortalRedirectHandler)
        self.target_provider = target_provider


class _PortalRedirectHandler(BaseHTTPRequestHandler):
    server: _PortalHTTPServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        return

    def do_GET(self) -> None:  # noqa: N802
        target = str(self.server.target_provider() or "/").strip()
        if not target:
            self.send_response(HTTPStatus.SERVICE_UNAVAILABLE)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            body = b"School CSM Control Center captive portal is not ready."
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", target)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()

    do_HEAD = do_GET


class _WildcardDNSServer(socketserver.ThreadingUDPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], answer_ip: str) -> None:
        self.answer_ip = str(ipaddress.IPv4Address(answer_ip))
        super().__init__(address, _WildcardDNSHandler)


class _WildcardDNSHandler(socketserver.BaseRequestHandler):
    server: _WildcardDNSServer

    def handle(self) -> None:
        packet, udp_socket = self.request
        response = build_dns_response(packet, self.server.answer_ip)
        if response:
            udp_socket.sendto(response, self.client_address)


def build_dns_response(packet: bytes, answer_ip: str) -> bytes:
    """Return a minimal DNS response mapping A queries to ``answer_ip``.

    Unsupported or malformed queries receive a valid no-answer response where
    possible. The implementation intentionally supports only the subset needed
    by phone captive-portal probes.
    """

    if len(packet) < 12:
        return b""
    transaction_id = packet[:2]
    try:
        _flags, qdcount, _ancount, _nscount, _arcount = struct.unpack("!HHHHH", packet[2:12])
    except struct.error:
        return b""
    if qdcount < 1:
        return b""

    offset = 12
    labels: list[bytes] = []
    try:
        while True:
            length = packet[offset]
            offset += 1
            if length == 0:
                break
            if length & 0xC0:
                return b""
            if length > 63 or offset + length > len(packet):
                return b""
            labels.append(packet[offset:offset + length])
            offset += length
        if offset + 4 > len(packet):
            return b""
        qtype, qclass = struct.unpack("!HH", packet[offset:offset + 4])
        question_end = offset + 4
    except (IndexError, struct.error):
        return b""

    question = packet[12:question_end]
    # Standard response, authoritative answer, recursion available.
    flags = 0x8180
    answer_count = 1 if qtype == 1 and qclass == 1 else 0
    header = transaction_id + struct.pack("!HHHHH", flags, 1, answer_count, 0, 0)
    if not answer_count:
        return header + question

    packed_ip = socket.inet_aton(str(ipaddress.IPv4Address(answer_ip)))
    answer = b"\xC0\x0C" + struct.pack("!HHIH", 1, 1, 30, len(packed_ip)) + packed_ip
    return header + question + answer


def build_dns_query(hostname: str, transaction_id: bytes | None = None) -> bytes:
    """Return a minimal A-record query used by the resolver self-test."""

    normalized = str(hostname or "").strip().casefold().rstrip(".")
    labels = normalized.split(".") if normalized else []
    if not labels or any(not label or len(label.encode("ascii")) > 63 for label in labels):
        raise ValueError("A valid hostname is required for the DNS self-test.")
    transaction = transaction_id or secrets.token_bytes(2)
    if len(transaction) != 2:
        raise ValueError("DNS transaction IDs must contain exactly two bytes.")
    encoded_name = b"".join(
        bytes([len(label.encode("ascii"))]) + label.encode("ascii") for label in labels
    ) + b"\x00"
    return transaction + struct.pack("!HHHHH", 0x0100, 1, 0, 0, 0) + encoded_name + struct.pack("!HH", 1, 1)


def verify_dns_resolution(
    *,
    hostname: str,
    expected_ip: str,
    resolver_ip: str,
    resolver_port: int = 53,
    timeout: float = 0.75,
) -> tuple[bool, str]:
    """Verify that the app-owned resolver answers its configured hostname.

    This is intentionally a local component check. It does not claim that a
    particular phone will accept Windows Mobile Hotspot's DNS configuration.
    """

    try:
        expected = str(ipaddress.IPv4Address(expected_ip))
        resolver = str(ipaddress.IPv4Address(resolver_ip))
        transaction = secrets.token_bytes(2)
        query = build_dns_query(hostname, transaction)
    except (ValueError, UnicodeEncodeError) as exc:
        return False, f"Local DNS self-test could not be prepared: {exc}"

    client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    client.settimeout(max(0.1, float(timeout)))
    try:
        client.sendto(query, (resolver, int(resolver_port)))
        response, _peer = client.recvfrom(2048)
    except OSError as exc:
        return False, f"Local DNS self-test did not receive an answer: {exc}"
    finally:
        client.close()

    if len(response) < 12 or response[:2] != transaction:
        return False, "Local DNS self-test received an invalid transaction response."
    try:
        answer_count = struct.unpack("!H", response[6:8])[0]
    except struct.error:
        return False, "Local DNS self-test received a malformed response."
    packed_expected = socket.inet_aton(expected)
    if answer_count < 1 or not response.endswith(packed_expected):
        return False, f"Local DNS self-test did not map the school hostname to {expected}."
    return True, (
        f"The app-owned DNS responder mapped {str(hostname).rstrip('.')} to {expected} in its local self-test. "
        "Connected-device DNS behavior remains dependent on Windows Mobile Hotspot and the phone."
    )


CAPTIVE_PROBE_HOSTS = (
    # Android / Google
    "connectivitycheck.gstatic.com",
    "connectivitycheck.android.com",
    "clients3.google.com",
    "clients4.google.com",
    "www.google.com",
    # Apple
    "captive.apple.com",
    "www.apple.com",
    "apple.com",
    # Windows
    "msftconnecttest.com",
    "www.msftconnecttest.com",
    "ipv6.msftconnecttest.com",
    "dns.msftncsi.com",
    "www.msftncsi.com",
    # Common browser and Android-vendor probes
    "detectportal.firefox.com",
    "connectivitycheck.samsung.com",
    "connectivitycheck.platform.hicloud.com",
    "connect.rom.miui.com",
    "conn1.oppomobile.com",
    "conn2.oppomobile.com",
    "connect.vivo.com.cn",
)
HOSTS_BEGIN = "# BEGIN SCHOOL CSM CAPTIVE PORTAL"
HOSTS_END = "# END SCHOOL CSM CAPTIVE PORTAL"


def default_windows_hosts_path() -> Path:
    root = Path(os.environ.get("SystemRoot") or r"C:\Windows")
    return root / "System32" / "drivers" / "etc" / "hosts"


def configure_probe_hosts(
    answer_ip: str,
    hosts_path: Path | None = None,
    extra_hostnames: Iterable[str] = (),
) -> tuple[bool, str]:
    """Map captive-portal probes and configured local names to the hotspot IP.

    These entries affect the laptop resolver only. Windows Internet Connection
    Sharing does not reliably propagate them to phones, so their presence is
    never used as proof that a configured ``.home.arpa`` name is available.
    """

    path = Path(hosts_path or default_windows_hosts_path())
    names: list[str] = []
    for raw in (*CAPTIVE_PROBE_HOSTS, *tuple(extra_hostnames)):
        hostname = str(raw or "").strip().casefold().rstrip(".")
        if hostname and " " not in hostname and hostname not in names:
            names.append(hostname)
    try:
        original = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
        cleaned = _remove_hosts_block(original)
        block = [HOSTS_BEGIN]
        for hostname in names:
            block.append(f"{answer_ip} {hostname}")
        block.append(HOSTS_END)
        text = cleaned.rstrip() + "\n\n" + "\n".join(block) + "\n"
        path.write_text(text, encoding="utf-8")
        _flush_windows_dns()
        return True, f"Laptop-only captive-portal probe mappings were added to {path}."
    except OSError as exc:
        return False, f"Probe-host mapping failed: {exc}"


def remove_probe_hosts(hosts_path: Path | None = None) -> tuple[bool, str]:
    path = Path(hosts_path or default_windows_hosts_path())
    try:
        if not path.exists():
            return True, "No captive-portal probe mappings were present."
        original = path.read_text(encoding="utf-8", errors="replace")
        cleaned = _remove_hosts_block(original)
        if cleaned != original:
            path.write_text(cleaned.rstrip() + "\n", encoding="utf-8")
            _flush_windows_dns()
        return True, "Captive-portal probe mappings were removed."
    except OSError as exc:
        return False, f"Probe-host cleanup failed: {exc}"


def _remove_hosts_block(text: str) -> str:
    lines = str(text).splitlines()
    output: list[str] = []
    inside = False
    for line in lines:
        marker = line.strip()
        if marker == HOSTS_BEGIN:
            inside = True
            continue
        if marker == HOSTS_END:
            inside = False
            continue
        if not inside:
            output.append(line)
    return "\n".join(output)


def _flush_windows_dns() -> None:
    if os.name != "nt":
        return
    try:
        subprocess.run(
            ["ipconfig", "/flushdns"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
            creationflags=hidden_process_creation_flags(),
        )
    except (OSError, subprocess.SubprocessError):
        pass


class CaptivePortalService:
    """Manage the HTTP redirector and wildcard DNS responder."""

    def __init__(
        self,
        *,
        hotspot_ip: str,
        target_provider: Callable[[], str],
        hosts_path: Path | None = None,
        local_hostnames: Iterable[str] = (),
    ) -> None:
        self.hotspot_ip = str(ipaddress.IPv4Address(hotspot_ip))
        self.target_provider = target_provider
        self.hosts_path = hosts_path
        self.local_hostnames = tuple(
            hostname
            for hostname in (str(value or "").strip().casefold().rstrip(".") for value in local_hostnames)
            if hostname and " " not in hostname
        )
        self._http_server: _PortalHTTPServer | None = None
        self._dns_server: _WildcardDNSServer | None = None
        self._http_thread: Thread | None = None
        self._dns_thread: Thread | None = None
        self._errors: list[str] = []
        self._probe_hosts_configured = False
        self._dns_verified = False
        self._dns_verification_detail = "Local DNS has not been tested."

    @property
    def dns_listener_running(self) -> bool:
        return bool(self._dns_thread and self._dns_thread.is_alive())

    @property
    def named_resolution_available(self) -> bool:
        return bool(self.dns_listener_running and self._dns_verified)

    def start(self, *, http_port: int = 80, dns_port: int = 53) -> CaptivePortalStatus:
        self.stop()
        self._errors.clear()

        try:
            # Listen on every local interface. This avoids a race where the
            # Windows hotspot adapter is recreated after the address was
            # detected but before the portal listener starts.
            self._http_server = _PortalHTTPServer(("0.0.0.0", int(http_port)), self.target_provider)
            self._http_thread = Thread(
                target=self._http_server.serve_forever,
                name="SchoolCSMCaptivePortalHTTP",
                daemon=True,
            )
            self._http_thread.start()
        except OSError as exc:
            self._http_server = None
            self._http_thread = None
            self._errors.append(f"HTTP port {http_port} unavailable: {exc}")

        try:
            self._dns_server = _WildcardDNSServer((self.hotspot_ip, int(dns_port)), self.hotspot_ip)
            self._dns_thread = Thread(
                target=self._dns_server.serve_forever,
                name="SchoolCSMCaptivePortalDNS",
                daemon=True,
            )
            self._dns_thread.start()
        except OSError as exc:
            self._dns_server = None
            self._dns_thread = None
            self._errors.append(f"DNS port {dns_port} unavailable: {exc}")

        if self._dns_thread and self._dns_thread.is_alive() and self._dns_server is not None:
            test_hostname = self.local_hostnames[0] if self.local_hostnames else "school-csm-health.local"
            actual_dns_port = int(self._dns_server.server_address[1])
            self._dns_verified, self._dns_verification_detail = verify_dns_resolution(
                hostname=test_hostname,
                expected_ip=self.hotspot_ip,
                resolver_ip=self.hotspot_ip,
                resolver_port=actual_dns_port,
            )
            if not self._dns_verified:
                self._errors.append(self._dns_verification_detail)
        else:
            self._dns_verified = False
            self._dns_verification_detail = "The app-owned DNS listener is not running."

        if not (self._dns_thread and self._dns_thread.is_alive()) and (os.name == "nt" or self.hosts_path is not None):
            configured, detail = configure_probe_hosts(self.hotspot_ip, self.hosts_path, self.local_hostnames)
            self._probe_hosts_configured = configured
            if not configured:
                self._errors.append(detail)
        return self.status()

    def stop(self) -> None:
        for server, thread in (
            (self._http_server, self._http_thread),
            (self._dns_server, self._dns_thread),
        ):
            if server is not None:
                try:
                    server.shutdown()
                    server.server_close()
                except OSError:
                    pass
            if thread is not None and thread.is_alive():
                thread.join(timeout=1.5)
        self._http_server = None
        self._dns_server = None
        self._http_thread = None
        self._dns_thread = None
        if self._probe_hosts_configured:
            remove_probe_hosts(self.hosts_path)
        self._probe_hosts_configured = False
        self._dns_verified = False
        self._dns_verification_detail = "Local DNS is stopped."

    def status(self) -> CaptivePortalStatus:
        http_running = bool(self._http_thread and self._http_thread.is_alive())
        dns_running = self.dns_listener_running
        probe_hosts = bool(self._probe_hosts_configured)
        dns_verified = bool(dns_running and self._dns_verified)
        if http_running and dns_verified:
            detail = (
                "The captive-portal redirect and app-owned DNS responder passed their local checks. "
                "Automatic network-login opening remains device-dependent; the direct-IP QR is the primary reliable address."
            )
        elif http_running and dns_running:
            detail = (
                "The captive-portal listeners started, but the local DNS self-test did not verify the configured school hostname. "
                "The named address is hidden; use the direct-IP Survey Form QR."
            )
        elif http_running and probe_hosts:
            detail = (
                "Captive Portal HTTP redirect is running, but Windows probe-host mappings are only a best-effort fallback. "
                "Automatic opening on phones is not guaranteed; use the direct-IP Survey Form QR when no network-login notification appears."
            )
        elif http_running or dns_running or probe_hosts:
            detail = "Captive Portal started partially; use the direct-IP Survey Form QR."
        else:
            detail = "Captive Portal is unavailable; use the fallback Survey Form QR."
        if self._errors:
            detail += " " + " ".join(self._errors)
        return CaptivePortalStatus(
            http_running=http_running,
            dns_running=dns_running,
            probe_hosts_configured=probe_hosts,
            target_url=str(self.target_provider() or ""),
            detail=detail,
            dns_verified=dns_verified,
            dns_verification_detail=self._dns_verification_detail,
        )
