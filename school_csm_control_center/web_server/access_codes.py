"""Pure helpers for respondent access URLs and Wi-Fi QR payloads."""

from __future__ import annotations

from typing import Final


SUPPORTED_WIFI_SECURITY: Final[set[str]] = {"WPA", "WEP", "NOPASS"}


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
) -> tuple[str, str, str, str]:
    """Return direct root, named root, direct access, and named access URLs."""

    suffix = port_suffix(port)
    direct_root = f"http://{str(local_ip).strip()}{suffix}"
    named_root = f"http://{str(hostname).strip()}{suffix}"
    key = str(access_key or "").strip()
    direct_access = f"{direct_root}/access/{key}"
    named_access = f"{named_root}/access/{key}"
    return direct_root, named_root, direct_access, named_access


def build_named_portal_url(*, hostname: str, port: int) -> str:
    """Return the configured local captive-portal target URL."""

    return f"http://{str(hostname).strip()}{port_suffix(port)}/portal"


def build_direct_portal_url(*, local_ip: str, port: int) -> str:
    """Return the direct-IP captive-portal target URL."""

    return f"http://{str(local_ip).strip()}{port_suffix(port)}/portal"
