"""Pure helpers for respondent access URLs and Wi-Fi QR payloads."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Final


SUPPORTED_WIFI_SECURITY: Final[set[str]] = {"WPA", "WEP", "NOPASS"}


@dataclass(frozen=True)
class RespondentAccessCapability:
    """An honest snapshot of the addresses that may be given to respondents.

    A configured hostname is not itself a capability.  It becomes usable only
    after a resolver controlled by the application has answered a local DNS
    self-test.  The direct IPv4 URL is therefore the primary address and the
    named URL is an optional convenience.
    """

    server_running: bool
    direct_available: bool
    direct_verified: bool
    direct_root: str
    direct_access_url: str
    direct_reason: str
    named_available: bool
    named_verified: bool
    named_hostname: str
    named_root: str
    named_access_url: str
    named_reason: str
    primary_kind: str
    primary_url: str
    restart_required: bool = False

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def wifi_escape(value: str) -> str:
    """Escape characters reserved by the de-facto Wi-Fi QR convention."""

    return (
        str(value)
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace(":", "\\:")
        .replace('"', '\\"')
    )


def build_wifi_qr_payload(
    *,
    ssid: str,
    security: str = "WPA",
    password: str = "",
    hidden: bool = False,
) -> str:
    """Return a Windows/Android/iOS-compatible Wi-Fi QR payload.

    For an ordinary visible WPA hotspot, the output intentionally matches the
    compact payload produced by Windows itself::

        WIFI:T:WPA;S:Network Name;P:password;;

    The optional ``H:true`` field is emitted only for a hidden network. Some
    camera applications are less reliable when an unnecessary ``H:false`` field
    is present, so visible networks use the exact compact form above.
    """

    normalized_ssid = str(ssid or "").strip()
    if not normalized_ssid:
        return ""

    normalized_security = str(security or "WPA").strip().upper()
    if normalized_security not in SUPPORTED_WIFI_SECURITY:
        normalized_security = "WPA"

    escaped_ssid = wifi_escape(normalized_ssid)
    parts = ["WIFI:", f"T:{'nopass' if normalized_security == 'NOPASS' else normalized_security};", f"S:{escaped_ssid};"]

    if normalized_security != "NOPASS":
        normalized_password = str(password or "")
        if not normalized_password:
            return ""
        parts.append(f"P:{wifi_escape(normalized_password)};")

    if bool(hidden):
        parts.append("H:true;")

    # The final empty field creates the conventional terminating double
    # semicolon without adding H:false to visible networks.
    parts.append(";")
    return "".join(parts)


def port_suffix(port: int) -> str:
    number = int(port)
    return "" if number == 80 else f":{number}"


def build_access_urls(
    *,
    hostname: str,
    local_ip: str,
    port: int,
    access_key: str,
    named_available: bool = True,
) -> tuple[str, str, str, str]:
    """Return direct root, named root, direct access, and named access URLs.

    ``named_available`` defaults to ``True`` for compatibility with callers
    that use this pure formatter. Runtime code must pass the result of its DNS
    capability check. When that check is false, both named values are empty so
    an unresolved ``.home.arpa`` address cannot leak into the UI or QR output.
    """

    suffix = port_suffix(port)
    normalized_ip = str(local_ip).strip()
    normalized_hostname = str(hostname).strip()
    direct_root = f"http://{normalized_ip}{suffix}" if normalized_ip else ""
    named_root = (
        f"http://{normalized_hostname}{suffix}"
        if bool(named_available) and normalized_hostname
        else ""
    )
    key = str(access_key or "").strip()
    direct_access = f"{direct_root}/access/{key}" if direct_root else ""
    named_access = f"{named_root}/access/{key}" if named_root else ""
    return direct_root, named_root, direct_access, named_access


def build_access_capability(
    *,
    hostname: str,
    local_ip: str,
    port: int,
    access_key: str,
    server_running: bool,
    direct_health_verified: bool,
    named_resolver_verified: bool,
    named_reason: str = "",
    restart_required: bool = False,
) -> RespondentAccessCapability:
    """Build respondent-address state without claiming unverified access."""

    running = bool(server_running and int(port) > 0)
    direct_verified = bool(running and direct_health_verified)
    named_verified = bool(direct_verified and named_resolver_verified)
    direct_root, named_root, direct_access, named_access = build_access_urls(
        hostname=hostname,
        local_ip=local_ip,
        port=port,
        access_key=access_key,
        named_available=named_verified,
    )
    if not running:
        direct_reason = "Start the local survey server to create a respondent address."
    elif not direct_verified:
        direct_reason = "The server health check has not confirmed this laptop IPv4 address."
    else:
        direct_reason = "The server answered its HTTP health check on this laptop IPv4 address."

    if named_verified:
        resolved_named_reason = (
            named_reason
            or "The app-owned local DNS responder passed its hostname self-test. Device support can still vary."
        )
    else:
        resolved_named_reason = named_reason or (
            "The configured school hostname is hidden because no verified local DNS resolver is available."
        )

    primary_url = direct_access if direct_verified else ""
    return RespondentAccessCapability(
        server_running=running,
        direct_available=direct_verified,
        direct_verified=direct_verified,
        direct_root=direct_root if direct_verified else "",
        direct_access_url=direct_access if direct_verified else "",
        direct_reason=direct_reason,
        named_available=named_verified,
        named_verified=named_verified,
        named_hostname=str(hostname or "").strip(),
        named_root=named_root,
        named_access_url=named_access,
        named_reason=resolved_named_reason,
        primary_kind="direct_ipv4" if direct_verified else "unavailable",
        primary_url=primary_url,
        restart_required=bool(restart_required),
    )


def build_named_portal_url(*, hostname: str, port: int) -> str:
    """Return the configured local captive-portal target URL."""

    return f"http://{str(hostname).strip()}{port_suffix(port)}/portal"


def build_direct_portal_url(*, local_ip: str, port: int) -> str:
    """Return the direct-IP captive-portal target URL."""

    return f"http://{str(local_ip).strip()}{port_suffix(port)}/portal"
