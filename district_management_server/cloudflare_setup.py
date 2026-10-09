"""One-time Cloudflare bootstrap for the public district management route."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
import json
import re
import secrets
from typing import Any, Mapping
from urllib.parse import quote

from registration_service.cloudflare import _CloudflareApi
from registration_service.core import ServiceError


_PUBLIC_HOST = re.compile(
    r"(?P<script>[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)\."
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.workers\.dev\Z"
)


@dataclass(frozen=True)
class ManagementGatewayResult:
    tunnel_id: str
    vpc_service_id: str
    worker_name: str
    public_host: str
    tunnel_token: str


class DistrictGatewayBootstrapper(_CloudflareApi):
    """Create an isolated Worker -> VPC Service -> named-tunnel route."""

    MAX_WORKER_BYTES = 512 * 1024

    def __init__(
        self,
        *,
        account_id: str,
        api_token: str,
        worker_source: str | Path,
        compatibility_date: str,
        timeout_seconds: float = 30.0,
    ) -> None:
        super().__init__(
            account_id=account_id,
            api_token=api_token,
            timeout_seconds=timeout_seconds,
        )
        self.worker_source = Path(worker_source).expanduser().resolve()
        self.compatibility_date = str(compatibility_date).strip()
        try:
            date.fromisoformat(self.compatibility_date)
        except ValueError:
            raise ValueError("Worker compatibility date must use YYYY-MM-DD.") from None
        if not self.worker_source.is_file():
            raise ValueError("The reviewed District Gateway Worker source is missing.")

    def provision(self, *, public_host: str, listen_port: int) -> ManagementGatewayResult:
        host = str(public_host).strip().casefold().rstrip(".")
        match = _PUBLIC_HOST.fullmatch(host)
        if not match:
            raise ServiceError("The public district workers.dev hostname is invalid.")
        worker_name = str(match.group("script"))
        if worker_name.isdigit():
            raise ServiceError("The district Worker name must not be a School ID.")
        port = _port(listen_port)
        self._ensure_worker_name_available(worker_name)
        tunnel_id = self._create_tunnel(
            f"school-csm-district-management-{secrets.token_hex(4)}"
        )
        service_id = ""
        uploaded = False
        try:
            service_id = self._create_service(tunnel_id=tunnel_id, port=port)
            self._upload_worker(worker_name, host, service_id)
            uploaded = True
            self._request(
                "POST",
                self._worker_path(worker_name, "subdomain"),
                {"enabled": True, "previews_enabled": False},
            )
            token = self._token(tunnel_id)
        except Exception:
            if uploaded:
                self._best_effort("DELETE", self._worker_path(worker_name) + "?force=false")
            if service_id:
                self._best_effort("DELETE", self._service_path(service_id))
            try:
                self._delete_tunnel(tunnel_id)
            except Exception:
                pass
            raise
        return ManagementGatewayResult(tunnel_id, service_id, worker_name, host, token)

    def revoke(self, result: ManagementGatewayResult) -> None:
        self._request("DELETE", self._worker_path(result.worker_name) + "?force=false")
        self._request("DELETE", self._service_path(result.vpc_service_id))
        self._delete_tunnel(result.tunnel_id)

    def _create_service(self, *, tunnel_id: str, port: int) -> str:
        result = self._request(
            "POST",
            self._service_path(),
            {
                "type": "http",
                "name": f"school-csm-district-management-{tunnel_id[:8]}",
                "http_port": _port(port),
                "host": {
                    "ipv4": "127.0.0.1",
                    "network": {"tunnel_id": tunnel_id},
                },
            },
        )
        service_id = str(result.get("service_id") or "") if isinstance(result, Mapping) else ""
        if not service_id:
            raise ServiceError("Cloudflare did not return a district VPC service identity.")
        return service_id

    def _upload_worker(
        self, worker_name: str, public_host: str, service_id: str
    ) -> None:
        try:
            source = self.worker_source.read_bytes()
        except OSError as exc:
            raise ServiceError("The reviewed District Gateway Worker could not be read.") from exc
        if not source or len(source) > self.MAX_WORKER_BYTES:
            raise ServiceError("The reviewed District Gateway Worker has an invalid size.")
        metadata = {
            "main_module": "worker.js",
            "compatibility_date": self.compatibility_date,
            "bindings": [
                {
                    "type": "vpc_service",
                    "name": "DISTRICT_MANAGEMENT_ORIGIN",
                    "service_id": service_id,
                },
                {"type": "plain_text", "name": "PUBLIC_HOST", "text": public_host},
            ],
        }
        boundary = f"school-csm-district-{secrets.token_hex(16)}"
        body = _multipart(boundary, metadata, source)
        self._request_body(
            "PUT",
            self._worker_path(worker_name),
            body=body,
            extra_headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )

    def _worker_path(self, worker_name: str, suffix: str = "") -> str:
        base = (
            f"/client/v4/accounts/{quote(self.account_id, safe='')}/workers/scripts/"
            f"{quote(worker_name, safe='')}"
        )
        return f"{base}/{suffix}" if suffix else base

    def _service_path(self, service_id: str = "") -> str:
        base = (
            f"/client/v4/accounts/{quote(self.account_id, safe='')}"
            "/connectivity/directory/services"
        )
        return f"{base}/{quote(service_id, safe='')}" if service_id else base

    def _best_effort(self, method: str, path: str) -> None:
        try:
            self._request(method, path)
        except Exception:
            pass


def _multipart(boundary: str, metadata: Mapping[str, Any], source: bytes) -> bytes:
    marker = boundary.encode("ascii")
    document = json.dumps(dict(metadata), separators=(",", ":")).encode("utf-8")
    return b"".join(
        (
            b"--" + marker + b"\r\n",
            b'Content-Disposition: form-data; name="metadata"\r\n',
            b"Content-Type: application/json\r\n\r\n",
            document,
            b"\r\n--" + marker + b"\r\n",
            b'Content-Disposition: form-data; name="worker.js"; filename="worker.js"\r\n',
            b"Content-Type: application/javascript+module\r\n\r\n",
            source,
            b"\r\n--" + marker + b"--\r\n",
        )
    )


def _port(value: Any) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError):
        port = 0
    if not 1024 <= port <= 65535:
        raise ServiceError("The district loopback port is invalid.")
    return port
