"""Transactional first-run setup for the Motiong district pilot host."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Mapping, Protocol
from urllib.parse import urlsplit

from .cloudflare_setup import DistrictGatewayBootstrapper, ManagementGatewayResult
from .configuration import DistrictServerConfig, write_server_config
from .credentials import DistrictCredentialStore
from .paths import DistrictRuntimePaths


@dataclass(frozen=True)
class DistrictSetupResult:
    config: DistrictServerConfig
    administrator_token: str
    provider_file: Path


class GatewayBootstrapper(Protocol):
    def provision(self, *, public_host: str, listen_port: int) -> ManagementGatewayResult: ...
    def revoke(self, result: ManagementGatewayResult) -> None: ...


def configure_district_server(
    *,
    paths: DistrictRuntimePaths,
    install_root: str | Path,
    account_id: str,
    cloudflare_api_token: str,
    public_origin: str = "https://management.motiong-district-csm-survey.workers.dev",
    managed_domain: str = "motiong-district-csm-survey.workers.dev",
    key_id: str = "motiong-district-2026",
    listen_port: int = 8090,
    worker_compatibility_date: str = "2026-09-30",
    credential_store: DistrictCredentialStore | None = None,
    bootstrapper: GatewayBootstrapper | None = None,
) -> DistrictSetupResult:
    """Provision Cloudflare, secrets, and non-secret files as one setup unit.

    Existing configuration or credentials are never replaced. Cloud resources
    are removed if protected local persistence cannot be completed.
    """

    paths.ensure_directories()
    if paths.configuration_file.exists():
        raise RuntimeError("District Management Server is already configured.")
    root = Path(install_root).resolve()
    public_host = str(urlsplit(str(public_origin)).hostname or "").casefold()
    if not public_host:
        raise ValueError("The public district origin is invalid.")
    if public_host != f"management.{str(managed_domain).strip().casefold().rstrip('.')}":
        raise ValueError("The public district origin must use the management Worker.")
    district_worker = root / "district_gateway_worker" / "src" / "index.js"
    if bootstrapper is None:
        bootstrapper = DistrictGatewayBootstrapper(
            account_id=account_id,
            api_token=cloudflare_api_token,
            worker_source=district_worker,
            compatibility_date=worker_compatibility_date,
        )
    gateway = bootstrapper.provision(public_host=public_host, listen_port=listen_port)
    store = credential_store or DistrictCredentialStore()
    secrets_written = False
    try:
        administrator_token, public_key = store.provision_initial(
            cloudflare_api_token=cloudflare_api_token,
            management_tunnel_token=gateway.tunnel_token,
        )
        secrets_written = True
        document: dict[str, Any] = {
            "schema_version": "1.0",
            "listen_port": int(listen_port),
            "public_origin": str(public_origin).rstrip("/"),
            "managed_domain": str(managed_domain).casefold().rstrip("."),
            "webauthn_rp_id": public_host,
            "authorization_key_id": str(key_id),
            "authorization_public_key_base64": public_key,
            "provisioning_mode": "workers_vpc",
            "cloudflare_account_id": str(account_id).casefold(),
            "cloudflare_zone_id": "",
            "tunnel_origin": "http://127.0.0.1:8080",
            "worker_source_path": "cloudflare_worker/src/index.js",
            "worker_compatibility_date": worker_compatibility_date,
            "management_tunnel_id": gateway.tunnel_id,
            "management_vpc_service_id": gateway.vpc_service_id,
            "management_worker_name": gateway.worker_name,
        }
        config = write_server_config(paths.configuration_file, document)
        provider = {
            "schema_version": "1.0",
            "provider_name": "Schools District of Motiong School CSM Internet Gateway",
            "registration_service_url": config.public_origin,
            "managed_domain": config.managed_domain,
            "authorization_signing_keys": [
                {
                    "key_id": config.authorization_key_id,
                    "public_key_base64": config.authorization_public_key_base64,
                }
            ],
            "trusted_proxy_addresses": ["127.0.0.1", "::1"],
        }
        _atomic_json(paths.provider_export_file, provider)
    except Exception:
        if secrets_written:
            try:
                store.remove_all()
            except Exception:
                pass
        paths.configuration_file.unlink(missing_ok=True)
        paths.provider_export_file.unlink(missing_ok=True)
        try:
            bootstrapper.revoke(gateway)
        except Exception:
            pass
        raise
    return DistrictSetupResult(config, administrator_token, paths.provider_export_file)


def _atomic_json(path: Path, document: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(dict(document), indent=2, sort_keys=True) + "\n").encode("utf-8")
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        temporary.write_bytes(payload)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
