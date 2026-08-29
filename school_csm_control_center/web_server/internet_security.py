"""Request-boundary security for the optional Internet Gateway.

The reverse tunnel terminates HTTPS before forwarding a request to the local
HTTP server.  Forwarded headers are therefore security-sensitive: they are
accepted only from a configured local proxy, and the public host is derived
from the Control Center's official School ID rather than browser input.
"""

from __future__ import annotations

from collections import OrderedDict, deque
from dataclasses import dataclass
from http import HTTPStatus
import hashlib
import hmac
import ipaddress
import math
import re
import secrets
from threading import RLock
from time import monotonic
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit


_SCHOOL_ID_PATTERN = re.compile(r"^\d{4,12}$")
_DNS_LABEL_PATTERN = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")


INTERNET_RATE_LIMIT_DEFAULTS: dict[str, tuple[int, int]] = {
    # These limits intentionally protect the public reverse-tunnel boundary
    # without changing local-LAN operation.
    "access_session": (12, 60),
    "survey_submit": (30, 60),
    "scanner_login": (10, 300),
    "scanner_jobs": (120, 60),
    "scanner_upload": (12, 60),
}


@dataclass(frozen=True, slots=True)
class RequestSecurityContext:
    """Server-owned description of how the current request reached the app."""

    transport: str = "local"
    scheme: str = "http"
    host: str = ""
    client_fingerprint: str = ""

    @property
    def internet(self) -> bool:
        return self.transport == "internet"


class InternetRequestRejected(PermissionError):
    """A request failed the Internet Gateway trust boundary."""

    def __init__(self, message: str, *, code: str, status: HTTPStatus) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass(frozen=True, slots=True)
class InternetRequestPolicy:
    enabled: bool
    public_host: str
    school_id: str
    trusted_proxy_networks: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]
    configuration_valid: bool

    @classmethod
    def from_settings(cls, settings: Mapping[str, Any]) -> "InternetRequestPolicy":
        enabled = bool(settings.get("internet_gateway_enabled", False))
        school_id = "".join(str(settings.get("school_id") or "").split())
        try:
            public_host = normalize_request_host(
                str(settings.get("internet_gateway_public_host") or "")
            )
        except ValueError:
            public_host = ""

        networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
        configured = settings.get("internet_gateway_trusted_proxy_ips") or ()
        if isinstance(configured, str):
            configured = [item.strip() for item in configured.split(",")]
        if isinstance(configured, (list, tuple, set, frozenset)):
            for value in configured:
                text = str(value or "").strip()
                if not text:
                    continue
                try:
                    networks.append(ipaddress.ip_network(text, strict=False))
                except ValueError:
                    # An invalid entry must never widen the trust boundary.
                    continue

        expected_school_label = public_host.split(".", 1)[0] if public_host else ""
        configuration_valid = bool(
            enabled
            and _SCHOOL_ID_PATTERN.fullmatch(school_id)
            and public_host
            and _is_dns_hostname(public_host)
            and expected_school_label == school_id
        )
        return cls(
            enabled=enabled,
            public_host=public_host,
            school_id=school_id,
            trusted_proxy_networks=tuple(networks),
            configuration_valid=configuration_valid,
        )

    def classify(
        self,
        *,
        peer_ip: str,
        host: str,
        forwarded_host: str = "",
        forwarded_proto: str = "",
        forwarded_for: str = "",
        connecting_ip: str = "",
        forwarding_headers_present: bool = False,
        fingerprint_secret: bytes = b"",
    ) -> RequestSecurityContext:
        """Classify one request without trusting browser-supplied identity."""

        try:
            request_host = normalize_request_host(host)
        except ValueError as exc:
            raise InternetRequestRejected(
                "The request Host header is invalid.",
                code="invalid_host",
                status=HTTPStatus.BAD_REQUEST,
            ) from exc

        looks_public = bool(self.public_host and request_host == self.public_host)
        if not forwarding_headers_present and not looks_public:
            return RequestSecurityContext(transport="local", scheme="http", host=request_host)

        if not self.enabled or not self.configuration_valid:
            raise InternetRequestRejected(
                "The Internet Gateway is not configured for this Control Center.",
                code="gateway_not_configured",
                status=HTTPStatus.SERVICE_UNAVAILABLE,
            )
        if not self._is_trusted_proxy(peer_ip):
            raise InternetRequestRejected(
                "Internet forwarding headers were received from an untrusted connection.",
                code="untrusted_gateway_proxy",
                status=HTTPStatus.FORBIDDEN,
            )

        proto = _single_forwarded_value(forwarded_proto, "forwarded protocol").casefold()
        if proto != "https":
            raise InternetRequestRejected(
                "The public School CSM service requires HTTPS.",
                code="gateway_https_required",
                status=HTTPStatus.FORBIDDEN,
            )

        effective_host = request_host
        effective_host_header = str(host or "")
        if str(forwarded_host or "").strip():
            effective_host_header = _single_forwarded_value(
                forwarded_host, "forwarded host"
            )
            try:
                effective_host = normalize_request_host(
                    effective_host_header
                )
            except ValueError as exc:
                raise InternetRequestRejected(
                    "The forwarded public Host header is invalid.",
                    code="invalid_forwarded_host",
                    status=HTTPStatus.BAD_REQUEST,
                ) from exc
        if effective_host != self.public_host or not _https_host_port_allowed(
            effective_host_header
        ):
            raise InternetRequestRejected(
                "This public hostname is not assigned to the configured School ID.",
                code="school_host_mismatch",
                status=HTTPStatus.MISDIRECTED_REQUEST,
            )

        return RequestSecurityContext(
            transport="internet",
            scheme="https",
            host=self.public_host,
            client_fingerprint=privacy_preserving_fingerprint(
                fingerprint_secret,
                _forwarded_client_address(
                    connecting_ip=connecting_ip,
                    forwarded_for=forwarded_for,
                    fallback=peer_ip,
                ),
            ),
        )

    def _is_trusted_proxy(self, peer_ip: str) -> bool:
        try:
            address = ipaddress.ip_address(str(peer_ip or "").split("%", 1)[0])
        except ValueError:
            return False
        return any(address in network for network in self.trusted_proxy_networks)


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    allowed: bool
    retry_after_seconds: int = 0


@dataclass(slots=True)
class _RateWindow:
    events: deque[float]
    window_seconds: int


class BoundedRateLimiter:
    """Thread-safe fixed-window history with bounded, pseudonymous keys.

    The caller's identity is HMACed before it becomes a dictionary key, so a
    forwarded client address or session token is never retained in plaintext.
    Oldest identities are evicted once ``max_identities`` is reached.
    """

    def __init__(
        self,
        *,
        max_identities: int = 4096,
        secret: bytes | None = None,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self.max_identities = max(64, min(65_536, int(max_identities)))
        self._secret = secret or secrets.token_bytes(32)
        self._clock = clock
        self._entries: OrderedDict[tuple[str, str], _RateWindow] = OrderedDict()
        self._lock = RLock()

    def consume(
        self,
        bucket: str,
        identity: str,
        *,
        limit: int,
        window_seconds: int,
    ) -> RateLimitDecision:
        normalized_bucket = str(bucket or "request")[:64]
        digest = privacy_preserving_fingerprint(self._secret, identity or "anonymous")
        key = (normalized_bucket, digest)
        maximum = max(1, min(100_000, int(limit)))
        window = max(1, min(86_400, int(window_seconds)))
        now = float(self._clock())
        cutoff = now - window

        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                while len(self._entries) >= self.max_identities:
                    self._entries.popitem(last=False)
                entry = _RateWindow(events=deque(), window_seconds=window)
                self._entries[key] = entry
            else:
                entry.window_seconds = window
                self._entries.move_to_end(key)

            while entry.events and entry.events[0] <= cutoff:
                entry.events.popleft()
            if len(entry.events) >= maximum:
                retry = max(1, math.ceil(entry.events[0] + window - now))
                return RateLimitDecision(False, retry)
            entry.events.append(now)
            return RateLimitDecision(True, 0)

    @property
    def retained_identity_count(self) -> int:
        with self._lock:
            return len(self._entries)


def configured_rate_limit(
    settings: Mapping[str, Any],
    bucket: str,
) -> tuple[int, int]:
    """Return a clamped per-bucket limit, allowing deployment overrides."""

    default_limit, default_window = INTERNET_RATE_LIMIT_DEFAULTS[bucket]
    configured = settings.get("internet_gateway_rate_limits")
    value = configured.get(bucket) if isinstance(configured, Mapping) else None
    if not isinstance(value, Mapping):
        return default_limit, default_window
    try:
        limit = int(value.get("limit", default_limit))
        window = int(value.get("window_seconds", default_window))
    except (TypeError, ValueError):
        return default_limit, default_window
    return (
        max(1, min(100_000, limit)),
        max(1, min(86_400, window)),
    )


def validate_internet_origin(
    context: RequestSecurityContext,
    origin_values: list[str] | tuple[str, ...],
) -> None:
    """Require an exact HTTPS same-origin assertion on public POST requests."""

    if not context.internet:
        return
    if len(origin_values) != 1:
        raise InternetRequestRejected(
            "A same-origin request is required.",
            code="csrf_origin_required",
            status=HTTPStatus.FORBIDDEN,
        )
    raw = str(origin_values[0] or "").strip()
    if not raw or "," in raw or any(character in raw for character in "\r\n"):
        raise InternetRequestRejected(
            "A same-origin request is required.",
            code="csrf_origin_required",
            status=HTTPStatus.FORBIDDEN,
        )
    try:
        parsed = urlsplit(raw)
        origin_host = normalize_request_host(parsed.netloc)
        port = parsed.port
    except (ValueError, UnicodeError) as exc:
        raise InternetRequestRejected(
            "The request Origin is invalid.",
            code="invalid_origin",
            status=HTTPStatus.BAD_REQUEST,
        ) from exc
    if (
        parsed.scheme.casefold() != "https"
        or parsed.username is not None
        or parsed.password is not None
        or origin_host != context.host
        or port not in (None, 443)
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise InternetRequestRejected(
            "The request Origin does not match this school's public service.",
            code="csrf_origin_mismatch",
            status=HTTPStatus.FORBIDDEN,
        )


def privacy_preserving_fingerprint(secret: bytes, value: str) -> str:
    key = bytes(secret or b"school-csm-ephemeral-request-key")
    normalized = str(value or "anonymous").strip().casefold().encode("utf-8", "replace")
    return hmac.new(key, normalized, hashlib.sha256).hexdigest()[:32]


def normalize_request_host(value: str) -> str:
    """Return a lowercase hostname without a port or trailing DNS dot."""

    text = str(value or "").strip()
    if not text or any(character in text for character in "\r\n/\\") or "," in text:
        raise ValueError("Invalid host")
    if text.startswith("["):
        closing = text.find("]")
        if closing <= 1:
            raise ValueError("Invalid host")
        host = text[1:closing]
        remainder = text[closing + 1 :]
        if remainder and not (remainder.startswith(":") and remainder[1:].isdigit()):
            raise ValueError("Invalid host port")
    else:
        if text.count(":") > 1:
            raise ValueError("IPv6 hosts must be bracketed")
        host, separator, port = text.rpartition(":")
        if separator:
            if not host or not port.isdigit():
                raise ValueError("Invalid host port")
        else:
            host = text
    host = host.rstrip(".").casefold()
    if not host or len(host) > 253:
        raise ValueError("Invalid host")
    try:
        return host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError("Invalid host") from exc


def _https_host_port_allowed(value: str) -> bool:
    """Accept a public HTTPS authority only without a port or with port 443."""

    text = str(value or "").strip()
    if text.startswith("["):
        # School Internet hostnames are DNS names, never IPv6 literals.
        return False
    host, separator, port = text.rpartition(":")
    if not separator:
        return True
    if not host or not port.isdigit():
        return False
    try:
        return int(port) == 443
    except ValueError:
        return False


def _is_dns_hostname(value: str) -> bool:
    labels = str(value or "").split(".")
    return len(labels) >= 3 and all(_DNS_LABEL_PATTERN.fullmatch(label) for label in labels)


def _forwarded_client_address(
    *,
    connecting_ip: str,
    forwarded_for: str,
    fallback: str,
) -> str:
    # CF-Connecting-IP is preferred because it is a single value set by the
    # trusted tunnel.  X-Forwarded-For is only used as a compatibility fallback.
    candidates = [str(connecting_ip or "").strip()]
    forwarded = str(forwarded_for or "").strip()
    if forwarded:
        candidates.append(forwarded.split(",", 1)[0].strip())
    candidates.append(str(fallback or "").strip())
    for candidate in candidates:
        try:
            return str(ipaddress.ip_address(candidate.split("%", 1)[0]))
        except ValueError:
            continue
    return "gateway-client"


def _single_forwarded_value(value: str, label: str) -> str:
    text = str(value or "").strip()
    if not text or "," in text or any(character in text for character in "\r\n"):
        raise InternetRequestRejected(
            f"The {label} header is missing or ambiguous.",
            code="ambiguous_gateway_headers",
            status=HTTPStatus.BAD_REQUEST,
        )
    return text
