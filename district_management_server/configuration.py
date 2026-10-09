"""Validated public configuration and protected environment preparation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import base64
import json
import os
from pathlib import Path, PurePosixPath
import re
from typing import Any, Mapping, MutableMapping
from urllib.parse import urlsplit

from .credentials import DistrictCredentialStore
from .paths import DistrictRuntimePaths


MAXIMUM_CONFIG_BYTES = 64 * 1024
ALLOWED_FIELDS = frozenset(
    {
        "schema_version",
        "listen_port",
        "public_origin",
        "managed_domain",
        "webauthn_rp_id",
        "authorization_key_id",
        "authorization_public_key_base64",
        "provisioning_mode",
        "cloudflare_account_id",
        "cloudflare_zone_id",
        "tunnel_origin",
        "worker_source_path",
        "worker_compatibility_date",
        "management_tunnel_id",
        "management_vpc_service_id",
        "management_worker_name",
    }
)
FORBIDDEN_FIELD_HINTS = ("secret", "password", "private", "token", "credential")
HOST_PATTERN = re.compile(
    r"(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z"
)
HEX_ID_PATTERN = re.compile(r"[0-9a-f]{32}")
KEY_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{2,79}")


class ConfigurationError(RuntimeError):
    """Raised when public or protected configuration fails closed."""


@dataclass(frozen=True)
class DistrictServerConfig:
    schema_version: str
    listen_port: int
    public_origin: str
    managed_domain: str
    webauthn_rp_id: str
    authorization_key_id: str
    authorization_public_key_base64: str
    provisioning_mode: str
    cloudflare_account_id: str
    cloudflare_zone_id: str
    tunnel_origin: str
    worker_source_path: str
    worker_compatibility_date: str
    management_tunnel_id: str
    management_vpc_service_id: str
    management_worker_name: str

    @property
    def portal_url(self) -> str:
        return f"{self.public_origin}/district"

    @property
    def local_health_url(self) -> str:
        return f"http://127.0.0.1:{self.listen_port}/healthz"


def load_server_config(path: str | Path) -> DistrictServerConfig:
    """Read one bounded UTF-8 JSON document with a closed field set."""

    source = Path(path)
    try:
        size = source.stat().st_size
    except FileNotFoundError:
        raise ConfigurationError(
            f"District server configuration is missing: {source}"
        ) from None
    except OSError as exc:
        raise ConfigurationError(
            f"District server configuration cannot be read: {type(exc).__name__}."
        ) from None
    if size <= 0 or size > MAXIMUM_CONFIG_BYTES:
        raise ConfigurationError("District server configuration has an invalid size.")
    try:
        document = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise ConfigurationError(
            "District server configuration must be valid UTF-8 JSON."
        ) from None
    if not isinstance(document, dict):
        raise ConfigurationError("District server configuration must be a JSON object.")
    unknown = sorted(set(document) - ALLOWED_FIELDS)
    if unknown:
        if any(
            hint in str(field).casefold()
            for field in unknown
            for hint in FORBIDDEN_FIELD_HINTS
        ):
            raise ConfigurationError(
                "Secrets are not permitted in server.json; use Windows Credential Manager."
            )
        raise ConfigurationError(
            "District server configuration contains unsupported fields: "
            + ", ".join(unknown)
        )
    return _parse_config(document)


def _parse_config(document: Mapping[str, Any]) -> DistrictServerConfig:
    schema = str(document.get("schema_version") or "").strip()
    if schema != "1.0":
        raise ConfigurationError("server.json schema_version must be 1.0.")

    try:
        listen_port = int(document.get("listen_port", 8090))
    except (TypeError, ValueError):
        listen_port = 0
    if not 1024 <= listen_port <= 65535:
        raise ConfigurationError("listen_port must be between 1024 and 65535.")

    public_origin = _https_origin(document.get("public_origin"))
    public_host = str(urlsplit(public_origin).hostname or "").casefold()
    rp_id = _hostname(document.get("webauthn_rp_id"), "webauthn_rp_id")
    if rp_id != public_host:
        raise ConfigurationError(
            "webauthn_rp_id must exactly match the public_origin hostname."
        )

    managed_domain = _hostname(document.get("managed_domain"), "managed_domain")
    if public_host != f"management.{managed_domain}":
        raise ConfigurationError(
            "public_origin must be the management Worker under managed_domain."
        )
    key_id = str(document.get("authorization_key_id") or "").strip()
    if not KEY_ID_PATTERN.fullmatch(key_id):
        raise ConfigurationError("authorization_key_id is invalid.")
    public_key = str(document.get("authorization_public_key_base64") or "").strip()
    try:
        public_material = base64.b64decode(public_key, validate=True)
    except (ValueError, TypeError):
        public_material = b""
    if len(public_material) != 32:
        raise ConfigurationError("authorization_public_key_base64 is invalid.")

    mode = str(document.get("provisioning_mode") or "").strip().casefold()
    if mode not in {"dns_tunnel", "workers_vpc"}:
        raise ConfigurationError(
            "provisioning_mode must be dns_tunnel or workers_vpc."
        )
    account_id = str(document.get("cloudflare_account_id") or "").strip().casefold()
    if not HEX_ID_PATTERN.fullmatch(account_id):
        raise ConfigurationError("cloudflare_account_id must be a 32-character hex ID.")
    zone_id = str(document.get("cloudflare_zone_id") or "").strip().casefold()
    if zone_id and not HEX_ID_PATTERN.fullmatch(zone_id):
        raise ConfigurationError("cloudflare_zone_id must be a 32-character hex ID.")
    if mode == "dns_tunnel" and not zone_id:
        raise ConfigurationError(
            "cloudflare_zone_id is required for dns_tunnel provisioning."
        )

    tunnel_origin = _loopback_origin(
        document.get("tunnel_origin", "http://127.0.0.1:8080")
    )
    worker_path = _relative_worker_path(
        document.get("worker_source_path", "cloudflare_worker/src/index.js")
    )
    compatibility_date = str(
        document.get("worker_compatibility_date") or "2026-09-30"
    ).strip()
    try:
        date.fromisoformat(compatibility_date)
    except ValueError:
        raise ConfigurationError("worker_compatibility_date must use YYYY-MM-DD.") from None

    management_tunnel_id = _uuid(document.get("management_tunnel_id"), "management_tunnel_id")
    management_service_id = _uuid(
        document.get("management_vpc_service_id"), "management_vpc_service_id"
    )
    management_worker_name = str(
        document.get("management_worker_name") or "management"
    ).strip().casefold()
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", management_worker_name):
        raise ConfigurationError("management_worker_name is invalid.")

    return DistrictServerConfig(
        schema_version=schema,
        listen_port=listen_port,
        public_origin=public_origin,
        managed_domain=managed_domain,
        webauthn_rp_id=rp_id,
        authorization_key_id=key_id,
        authorization_public_key_base64=public_key,
        provisioning_mode=mode,
        cloudflare_account_id=account_id,
        cloudflare_zone_id=zone_id,
        tunnel_origin=tunnel_origin,
        worker_source_path=worker_path,
        worker_compatibility_date=compatibility_date,
        management_tunnel_id=management_tunnel_id,
        management_vpc_service_id=management_service_id,
        management_worker_name=management_worker_name,
    )


def prepare_service_environment(
    config: DistrictServerConfig,
    paths: DistrictRuntimePaths,
    install_root: str | Path,
    *,
    credential_store: DistrictCredentialStore | None = None,
) -> dict[str, str]:
    """Return the service environment without writing or logging secret values."""

    secrets = (credential_store or DistrictCredentialStore()).load()
    root = Path(install_root).resolve()
    worker_source = (root / Path(config.worker_source_path)).resolve()
    try:
        worker_source.relative_to(root)
    except ValueError:
        raise ConfigurationError(
            "worker_source_path must remain inside the installed application."
        ) from None
    if config.provisioning_mode == "workers_vpc" and not worker_source.is_file():
        raise ConfigurationError("The bundled Cloudflare Worker source is missing.")

    environment = {
        "SCHOOL_CSM_REGISTRATION_DATABASE": str(paths.database_file),
        "SCHOOL_CSM_MANAGED_DOMAIN": config.managed_domain,
        "SCHOOL_CSM_WEBAUTHN_ORIGIN": config.public_origin,
        "SCHOOL_CSM_WEBAUTHN_RP_ID": config.webauthn_rp_id,
        "SCHOOL_CSM_AUTHORIZATION_KEY_ID": config.authorization_key_id,
        "SCHOOL_CSM_AUTHORIZATION_PRIVATE_KEY_BASE64": (
            secrets.authorization_private_key_base64
        ),
        "SCHOOL_CSM_HANDOFF_ENCRYPTION_KEY_BASE64": (
            secrets.handoff_encryption_key_base64
        ),
        "SCHOOL_CSM_DISTRICT_ADMIN_TOKEN_SHA256": (
            secrets.district_admin_token_sha256
        ),
        "SCHOOL_CSM_CLOUDFLARE_MODE": config.provisioning_mode,
        "CLOUDFLARE_ACCOUNT_ID": config.cloudflare_account_id,
        "CLOUDFLARE_API_TOKEN": secrets.cloudflare_api_token,
        "SCHOOL_CSM_TUNNEL_ORIGIN": config.tunnel_origin,
        "SCHOOL_CSM_WORKER_SOURCE": str(worker_source),
        "SCHOOL_CSM_WORKER_COMPATIBILITY_DATE": config.worker_compatibility_date,
    }
    if config.cloudflare_zone_id:
        environment["CLOUDFLARE_ZONE_ID"] = config.cloudflare_zone_id
    return environment


def install_service_environment(
    values: Mapping[str, str],
    *,
    environment: MutableMapping[str, str] | None = None,
) -> tuple[str, ...]:
    """Install validated values and return names only, never secret contents."""

    target = os.environ if environment is None else environment
    names: list[str] = []
    for name, value in values.items():
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{2,79}", str(name)):
            raise ConfigurationError("An internal environment variable name is invalid.")
        text = str(value)
        if not text or "\0" in text:
            raise ConfigurationError(
                f"The prepared value for {name} is empty or invalid."
            )
        target[str(name)] = text
        names.append(str(name))
    return tuple(names)


def write_server_config(path: str | Path, document: Mapping[str, Any]) -> DistrictServerConfig:
    """Validate and atomically persist a non-secret server configuration."""

    config = _parse_config(dict(document))
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(dict(document), indent=2, sort_keys=True) + "\n").encode("utf-8")
    if len(encoded) > MAXIMUM_CONFIG_BYTES:
        raise ConfigurationError("District server configuration has an invalid size.")
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    try:
        temporary.write_bytes(encoded)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return config


def _https_origin(value: Any) -> str:
    text = str(value or "").strip().rstrip("/")
    parsed = urlsplit(text)
    if (
        parsed.scheme.casefold() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or parsed.port not in {None, 443}
    ):
        raise ConfigurationError(
            "public_origin must be one exact HTTPS origin without a path."
        )
    host = _hostname(parsed.hostname, "public_origin hostname")
    return f"https://{host}"


def _hostname(value: Any, label: str) -> str:
    text = str(value or "").strip().rstrip(".").casefold()
    if not HOST_PATTERN.fullmatch(text):
        raise ConfigurationError(f"{label} must be a valid DNS hostname.")
    return text


def _loopback_origin(value: Any) -> str:
    text = str(value or "").strip().rstrip("/")
    parsed = urlsplit(text)
    try:
        port = parsed.port
    except ValueError:
        port = None
    if (
        parsed.scheme.casefold() != "http"
        or parsed.hostname != "127.0.0.1"
        or port is None
        or not 1 <= port <= 65535
        or parsed.path
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ConfigurationError(
            "tunnel_origin must be an http://127.0.0.1:<port> origin."
        )
    return f"http://127.0.0.1:{port}"


def _relative_worker_path(value: Any) -> str:
    text = str(value or "").strip().replace("\\", "/")
    path = PurePosixPath(text)
    if (
        not text
        or path.is_absolute()
        or ".." in path.parts
        or path.suffix.casefold() != ".js"
    ):
        raise ConfigurationError(
            "worker_source_path must be a relative bundled JavaScript file."
        )
    return path.as_posix()


def _uuid(value: Any, label: str) -> str:
    import uuid

    text = str(value or "").strip().casefold()
    try:
        parsed = uuid.UUID(text)
    except (ValueError, TypeError, AttributeError):
        raise ConfigurationError(f"{label} must be a UUID.") from None
    if str(parsed) != text:
        raise ConfigurationError(f"{label} must be a canonical UUID.")
    return text
