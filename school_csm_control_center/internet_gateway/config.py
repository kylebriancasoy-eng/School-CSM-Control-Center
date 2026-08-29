"""Load non-secret Internet Gateway provider configuration.

The public repository intentionally does not contain a production managed
domain, registration-service address, or signing key.  Those values are
deployment infrastructure, not school data and not application credentials.
"""

from __future__ import annotations

from dataclasses import dataclass
import base64
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping
from urllib.parse import urlparse


PROVIDER_CONFIG_FILENAME = "internet_gateway_provider.json"
PROVIDER_CONFIG_PROGRAM_DATA_PARTS = (
    "MoSSLab",
    "School CSM Control Center",
    "Configuration",
    PROVIDER_CONFIG_FILENAME,
)
DOMAIN_PATTERN = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$"
)
KEY_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,80}$")


class GatewayProviderConfigurationError(ValueError):
    """Raised when a deployment provider file is missing or unsafe."""


@dataclass(frozen=True)
class GatewayProviderConfig:
    service_url: str
    managed_domain: str
    signing_public_keys: Mapping[str, bytes]
    trusted_proxy_addresses: tuple[str, ...]
    provider_name: str = "School CSM Internet Gateway"

    @property
    def configured(self) -> bool:
        return bool(self.service_url and self.managed_domain and self.signing_public_keys)

    def public_host_for(self, school_id: str) -> str:
        school = str(school_id or "").strip()
        if not school.isdigit() or not 4 <= len(school) <= 12:
            raise GatewayProviderConfigurationError(
                "A valid 4-to-12 digit School ID is required for an Internet address."
            )
        return f"{school}.{self.managed_domain}"


def load_gateway_provider_config(
    install_root: str | Path,
    *,
    path: str | Path | None = None,
    required: bool = False,
) -> GatewayProviderConfig | None:
    """Return a validated provider configuration, or ``None`` when optional.

    Only exact deployment files beside the executable or in the durable
    ProgramData configuration directory are considered.  A sidecar file takes
    precedence so an administrator can deliberately replace the configuration.
    An ``.example.json`` file is documentation and can never enable access.
    """

    if path is not None:
        target = Path(path)
    else:
        sidecar = Path(install_root) / PROVIDER_CONFIG_FILENAME
        program_data = str(os.environ.get("PROGRAMDATA") or "").strip()
        durable = (
            Path(program_data).joinpath(*PROVIDER_CONFIG_PROGRAM_DATA_PARTS)
            if program_data
            else None
        )
        target = sidecar if sidecar.is_file() or durable is None else durable
    if not target.is_file():
        if required:
            raise GatewayProviderConfigurationError(
                "Internet Gateway provider configuration is not installed."
            )
        return None
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GatewayProviderConfigurationError(
            "Internet Gateway provider configuration could not be read."
        ) from exc
    if not isinstance(raw, Mapping):
        raise GatewayProviderConfigurationError(
            "Internet Gateway provider configuration must be a JSON object."
        )
    return parse_gateway_provider_config(raw)


def parse_gateway_provider_config(raw: Mapping[str, Any]) -> GatewayProviderConfig:
    schema = str(raw.get("schema_version") or "")
    if schema != "1.0":
        raise GatewayProviderConfigurationError(
            "Unsupported Internet Gateway provider configuration version."
        )

    service_url = str(raw.get("registration_service_url") or "").strip().rstrip("/")
    parsed = urlparse(service_url)
    if (
        parsed.scheme.casefold() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise GatewayProviderConfigurationError(
            "The registration service must be an HTTPS origin without credentials."
        )

    domain = str(raw.get("managed_domain") or "").strip().casefold().rstrip(".")
    if not DOMAIN_PATTERN.fullmatch(domain):
        raise GatewayProviderConfigurationError("The managed domain is invalid.")

    public_keys: dict[str, bytes] = {}
    key_rows = raw.get("authorization_signing_keys")
    if not isinstance(key_rows, list) or not key_rows:
        raise GatewayProviderConfigurationError(
            "At least one authorization signing public key is required."
        )
    for row in key_rows:
        if not isinstance(row, Mapping):
            raise GatewayProviderConfigurationError("A signing-key entry is invalid.")
        key_id = str(row.get("key_id") or "").strip()
        if not KEY_ID_PATTERN.fullmatch(key_id) or key_id in public_keys:
            raise GatewayProviderConfigurationError("A signing-key identifier is invalid or duplicated.")
        try:
            material = base64.b64decode(str(row.get("public_key_base64") or ""), validate=True)
        except (ValueError, TypeError):
            raise GatewayProviderConfigurationError("A signing public key is not valid Base64.") from None
        if len(material) != 32:
            raise GatewayProviderConfigurationError("Ed25519 public keys must contain exactly 32 bytes.")
        public_keys[key_id] = material

    # This is a deployment trust boundary, so it must be declared explicitly.
    # Falling back to loopback would silently authorize any local process to
    # supply public forwarding headers when an administrator omitted the field.
    proxies_raw = raw.get("trusted_proxy_addresses")
    if not isinstance(proxies_raw, list) or not proxies_raw:
        raise GatewayProviderConfigurationError("At least one trusted proxy address is required.")
    proxies: list[str] = []
    import ipaddress

    for item in proxies_raw:
        try:
            canonical = str(ipaddress.ip_address(str(item).strip()))
        except ValueError:
            raise GatewayProviderConfigurationError("A trusted proxy address is invalid.") from None
        if canonical not in proxies:
            proxies.append(canonical)

    provider_name = str(raw.get("provider_name") or "School CSM Internet Gateway").strip()
    return GatewayProviderConfig(
        service_url=service_url,
        managed_domain=domain,
        signing_public_keys=public_keys,
        trusted_proxy_addresses=tuple(proxies),
        provider_name=provider_name[:120] or "School CSM Internet Gateway",
    )
