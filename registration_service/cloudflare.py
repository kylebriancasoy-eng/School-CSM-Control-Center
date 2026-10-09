"""Least-privilege Cloudflare provisioners for the central service.

The API token remains on the district service.  School installations receive
only their named-tunnel connector token after active authorization.
"""

from __future__ import annotations

from datetime import date
import http.client
import json
import os
from pathlib import Path
import re
import secrets
import sys
from typing import Any, Mapping
from urllib.parse import quote

from .core import ServiceError


_SCHOOL_ID = re.compile(r"\d{4,12}\Z")
_WORKERS_HOST = re.compile(
    r"(?P<school>\d{4,12})\.(?P<subdomain>[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)"
    r"\.workers\.dev\Z"
)


class _CloudflareApi:
    API_HOST = "api.cloudflare.com"

    def __init__(self, *, account_id: str, api_token: str, timeout_seconds: float = 30.0) -> None:
        self.account_id = str(account_id).strip()
        self.api_token = str(api_token).strip()
        self.timeout_seconds = max(5.0, min(60.0, float(timeout_seconds)))
        if not self.account_id or not self.api_token:
            raise ValueError("Cloudflare account and API token are required.")

    def _request(
        self, method: str, path: str, payload: Mapping[str, Any] | None = None
    ) -> Any:
        body = None
        headers = {"Content-Type": "application/json"}
        if payload is not None:
            body = json.dumps(dict(payload), separators=(",", ":")).encode("utf-8")
        return self._request_body(method, path, body=body, extra_headers=headers)

    def _request_body(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None,
        extra_headers: Mapping[str, str] | None = None,
    ) -> Any:
        if body is not None and len(body) > 1024 * 1024:
            raise ServiceError("Cloudflare provisioning request is too large.")
        connection = http.client.HTTPSConnection(self.API_HOST, timeout=self.timeout_seconds)
        headers = {
            "Authorization": f"Bearer {self.api_token}",
            "Accept": "application/json",
            "User-Agent": "School-CSM-Registration-Service/2",
            "Connection": "close",
        }
        headers.update(dict(extra_headers or {}))
        if body is not None:
            headers["Content-Length"] = str(len(body))
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            raw = response.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise ServiceError("Cloudflare returned an oversized response.")
            try:
                document = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                document = None
            if not isinstance(document, Mapping) or not document.get("success"):
                raise ServiceError("Cloudflare could not complete gateway provisioning.")
            return document.get("result")
        except ServiceError:
            raise
        except Exception as exc:
            raise ServiceError(
                "Cloudflare gateway provisioning could not be reached securely."
            ) from exc
        finally:
            connection.close()

    def _create_tunnel(self, name: str) -> str:
        result = self._request(
            "POST",
            f"/client/v4/accounts/{quote(self.account_id, safe='')}/cfd_tunnel",
            {"name": name, "config_src": "cloudflare"},
        )
        tunnel_id = str(result.get("id") or "") if isinstance(result, Mapping) else ""
        if not tunnel_id:
            raise ServiceError("Cloudflare did not return a tunnel identity.")
        return tunnel_id

    def _token(self, tunnel_id: str) -> str:
        result = self._request(
            "GET",
            f"/client/v4/accounts/{quote(self.account_id, safe='')}/cfd_tunnel/"
            f"{quote(tunnel_id, safe='')}/token",
        )
        token = str(result if isinstance(result, str) else (result or {}).get("token") or "")
        if not token:
            raise ServiceError("Cloudflare did not return a tunnel connector token.")
        return token

    def _delete_tunnel(self, tunnel_id: str) -> None:
        account = quote(self.account_id, safe="")
        tunnel = quote(tunnel_id, safe="")
        try:
            self._request(
                "DELETE",
                f"/client/v4/accounts/{account}/cfd_tunnel/{tunnel}/connections",
            )
        except Exception:
            # The connection endpoint can be empty or already gone. Always
            # attempt deletion of the tunnel itself so rollback does not leak
            # a partially-created Cloudflare resource.
            pass
        self._request("DELETE", f"/client/v4/accounts/{account}/cfd_tunnel/{tunnel}")

    def _ensure_worker_name_available(self, worker_name: str) -> None:
        """Fail before provisioning if a Worker name is already in the account.

        A Worker upload is a PUT operation. Without this guard, an initial
        setup could silently replace an unrelated script and rollback could
        then delete that pre-existing script.
        """

        account = quote(self.account_id, safe="")
        result = self._request(
            "GET", f"/client/v4/accounts/{account}/workers/scripts"
        )
        if not isinstance(result, list):
            raise ServiceError(
                "Cloudflare did not return a safe Worker inventory; no changes were made."
            )
        requested = str(worker_name).strip().casefold()
        for row in result:
            if not isinstance(row, Mapping):
                continue
            existing = str(row.get("id") or row.get("name") or "").strip().casefold()
            if existing == requested:
                raise ServiceError(
                    f"Cloudflare Worker {worker_name!r} already exists. "
                    "No resources were changed; use the documented migration process."
                )


class CloudflareTunnelProvisioner(_CloudflareApi):
    """Custom-domain DNS + Tunnel provisioner retained for managed deployments."""

    def __init__(
        self,
        *,
        account_id: str,
        zone_id: str,
        api_token: str,
        origin_url: str,
        timeout_seconds: float = 30.0,
    ) -> None:
        super().__init__(
            account_id=account_id,
            api_token=api_token,
            timeout_seconds=timeout_seconds,
        )
        self.zone_id = str(zone_id).strip()
        self.origin_url = str(origin_url).strip()
        if not self.zone_id:
            raise ValueError("Cloudflare DNS zone is required.")
        if not self.origin_url.startswith("http://127.0.0.1:"):
            raise ValueError("Tunnel origin must be the local loopback HTTP server.")

    @classmethod
    def from_environment(cls):
        mode = os.environ.get("SCHOOL_CSM_CLOUDFLARE_MODE", "dns_tunnel").strip().casefold()
        if mode == "workers_vpc":
            return CloudflareWorkersVPCProvisioner.from_environment()
        if mode != "dns_tunnel":
            raise ValueError("SCHOOL_CSM_CLOUDFLARE_MODE is invalid.")
        return cls(
            account_id=os.environ.get("CLOUDFLARE_ACCOUNT_ID", ""),
            zone_id=os.environ.get("CLOUDFLARE_ZONE_ID", ""),
            api_token=os.environ.get("CLOUDFLARE_API_TOKEN", ""),
            origin_url=os.environ.get("SCHOOL_CSM_TUNNEL_ORIGIN", "http://127.0.0.1:8080"),
        )

    def provision(self, *, school_id: str, public_host: str) -> Mapping[str, str]:
        tunnel_id = self._create_tunnel(f"school-csm-{school_id}")
        try:
            self._configure(tunnel_id, public_host)
            self._route_dns(tunnel_id, public_host)
            token = self._token(tunnel_id)
        except Exception:
            try:
                self.revoke(tunnel_id=tunnel_id)
            except Exception:
                pass
            raise
        return {"tunnel_id": tunnel_id, "tunnel_token": token}

    def rotate(self, *, tunnel_id: str, school_id: str, public_host: str) -> Mapping[str, str]:
        replacement_id = self._create_tunnel(
            f"school-csm-{school_id}-transfer-{secrets.token_hex(4)}"
        )
        try:
            self._configure(replacement_id, public_host)
            self._route_dns(replacement_id, public_host)
            token = self._token(replacement_id)
        except Exception:
            try:
                self.rollback_rotation(
                    source_tunnel_id=tunnel_id,
                    replacement_tunnel_id=replacement_id,
                    public_host=public_host,
                )
            except Exception:
                pass
            raise
        return {"tunnel_id": replacement_id, "tunnel_token": token}

    def revoke(self, *, tunnel_id: str) -> None:
        self._delete_tunnel(tunnel_id)

    def rollback_rotation(
        self,
        *,
        source_tunnel_id: str,
        replacement_tunnel_id: str,
        public_host: str,
    ) -> None:
        self._route_dns(source_tunnel_id, public_host)
        self.revoke(tunnel_id=replacement_tunnel_id)

    def credential(
        self, *, tunnel_id: str, public_host: str, origin_port: int
    ) -> Mapping[str, str]:
        port = _valid_port(origin_port)
        self._configure(tunnel_id, public_host, origin_url=f"http://127.0.0.1:{port}")
        return {"tunnel_id": tunnel_id, "tunnel_token": self._token(tunnel_id)}

    def _configure(
        self, tunnel_id: str, public_host: str, *, origin_url: str | None = None
    ) -> None:
        service = str(origin_url or self.origin_url)
        if not service.startswith("http://127.0.0.1:"):
            raise ServiceError("The local tunnel origin is invalid.")
        self._request(
            "PUT",
            f"/client/v4/accounts/{quote(self.account_id, safe='')}/cfd_tunnel/"
            f"{quote(tunnel_id, safe='')}/configurations",
            {
                "config": {
                    "ingress": [
                        {"hostname": public_host, "service": service},
                        {"service": "http_status:404"},
                    ],
                    "originRequest": {
                        "httpHostHeader": public_host,
                        "connectTimeout": 30,
                    },
                }
            },
        )

    def _route_dns(self, tunnel_id: str, public_host: str) -> None:
        zone = quote(self.zone_id, safe="")
        records = self._request(
            "GET",
            f"/client/v4/zones/{zone}/dns_records?type=CNAME&name={quote(public_host, safe='')}",
        )
        payload = {
            "type": "CNAME",
            "name": public_host,
            "content": f"{tunnel_id}.cfargotunnel.com",
            "proxied": True,
            "ttl": 1,
            "comment": "School CSM Internet Gateway managed route",
        }
        existing = records[0] if isinstance(records, list) and records else None
        if isinstance(existing, Mapping) and existing.get("id"):
            self._request(
                "PUT",
                f"/client/v4/zones/{zone}/dns_records/{quote(str(existing['id']), safe='')}",
                payload,
            )
        else:
            self._request("POST", f"/client/v4/zones/{zone}/dns_records", payload)


class CloudflareWorkersVPCProvisioner(_CloudflareApi):
    """Free-pilot provisioner: one ordinary Worker and VPC Service per school."""

    VPC_BINDING = "SCHOOL_CSM_ORIGIN"
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
            raise ValueError("The reviewed School CSM Worker source is missing.")

    @classmethod
    def from_environment(cls) -> "CloudflareWorkersVPCProvisioner":
        configured = os.environ.get("SCHOOL_CSM_WORKER_SOURCE", "").strip()
        if configured:
            source = Path(configured)
        else:
            base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
            source = base / "cloudflare_worker" / "src" / "index.js"
        return cls(
            account_id=os.environ.get("CLOUDFLARE_ACCOUNT_ID", ""),
            api_token=os.environ.get("CLOUDFLARE_API_TOKEN", ""),
            worker_source=source,
            compatibility_date=os.environ.get(
                "SCHOOL_CSM_WORKER_COMPATIBILITY_DATE", "2026-09-30"
            ),
        )

    def provision(self, *, school_id: str, public_host: str) -> Mapping[str, str]:
        script = self._script_name(school_id, public_host)
        self._ensure_worker_name_available(script)
        tunnel_id = self._create_tunnel(f"school-csm-{school_id}")
        service_id = ""
        worker_uploaded = False
        try:
            service_id = self._create_service(
                tunnel_id=tunnel_id,
                school_id=school_id,
                origin_port=8080,
            )
            self._upload_worker(script, public_host, service_id)
            worker_uploaded = True
            self._enable_worker_subdomain(script)
            token = self._token(tunnel_id)
        except Exception:
            if worker_uploaded:
                self._best_effort_delete_worker(script)
            if service_id:
                self._best_effort_delete_service(service_id)
            try:
                self._delete_tunnel(tunnel_id)
            except Exception:
                pass
            raise
        return {"tunnel_id": tunnel_id, "tunnel_token": token}

    def rotate(self, *, tunnel_id: str, school_id: str, public_host: str) -> Mapping[str, str]:
        script = self._script_name(school_id, public_host)
        source_service = self._service_for_tunnel(tunnel_id)
        port = _valid_port(source_service.get("http_port") or 8080)
        replacement_id = self._create_tunnel(
            f"school-csm-{school_id}-transfer-{secrets.token_hex(4)}"
        )
        replacement_service = ""
        try:
            replacement_service = self._create_service(
                tunnel_id=replacement_id,
                school_id=school_id,
                origin_port=port,
            )
            self._upload_worker(script, public_host, replacement_service)
            self._enable_worker_subdomain(script)
            token = self._token(replacement_id)
        except Exception:
            if replacement_service:
                try:
                    self._upload_worker(
                        script,
                        public_host,
                        str(source_service["service_id"]),
                    )
                except Exception:
                    pass
                self._best_effort_delete_service(replacement_service)
            try:
                self._delete_tunnel(replacement_id)
            except Exception:
                pass
            raise
        return {"tunnel_id": replacement_id, "tunnel_token": token}

    def rollback_rotation(
        self,
        *,
        source_tunnel_id: str,
        replacement_tunnel_id: str,
        public_host: str,
    ) -> None:
        source_service = self._service_for_tunnel(source_tunnel_id)
        script = self._script_from_host(public_host)
        self._upload_worker(script, public_host, str(source_service["service_id"]))
        self.revoke(tunnel_id=replacement_tunnel_id)

    def credential(
        self, *, tunnel_id: str, public_host: str, origin_port: int
    ) -> Mapping[str, str]:
        port = _valid_port(origin_port)
        service = self._service_for_tunnel(tunnel_id)
        service_id = str(service.get("service_id") or "")
        if not service_id:
            raise ServiceError("The school's Workers VPC service is unavailable.")
        self._request(
            "PUT",
            self._service_path(service_id),
            self._service_payload(
                name=str(service.get("name") or f"school-csm-{self._script_from_host(public_host)}"),
                tunnel_id=tunnel_id,
                origin_port=port,
            ),
        )
        self._upload_worker(
            self._script_from_host(public_host), public_host, service_id
        )
        self._enable_worker_subdomain(self._script_from_host(public_host))
        return {"tunnel_id": tunnel_id, "tunnel_token": self._token(tunnel_id)}

    def revoke(self, *, tunnel_id: str) -> None:
        services = self._services_for_tunnel(tunnel_id)
        for service in services:
            service_id = str(service.get("service_id") or "")
            script = self._script_from_service(service)
            if script and service_id and self._worker_binding_service(script) == service_id:
                self._best_effort_delete_worker(script)
            if service_id:
                self._request("DELETE", self._service_path(service_id))
        self._delete_tunnel(tunnel_id)

    def _script_name(self, school_id: str, public_host: str) -> str:
        school = str(school_id).strip()
        if not _SCHOOL_ID.fullmatch(school):
            raise ServiceError("School ID is invalid for Worker provisioning.")
        script = self._script_from_host(public_host)
        if script != school:
            raise ServiceError("The public Worker host does not match the School ID.")
        return script

    @staticmethod
    def _script_from_host(public_host: str) -> str:
        host = str(public_host).strip().casefold().rstrip(".")
        match = _WORKERS_HOST.fullmatch(host)
        if not match:
            raise ServiceError("The public Workers address is invalid.")
        return str(match.group("school"))

    @staticmethod
    def _script_from_service(service: Mapping[str, Any]) -> str:
        name = str(service.get("name") or "")
        match = re.match(r"school-csm-(\d{4,12})(?:-|\Z)", name)
        return match.group(1) if match else ""

    def _create_service(self, *, tunnel_id: str, school_id: str, origin_port: int) -> str:
        result = self._request(
            "POST",
            self._service_path(),
            self._service_payload(
                name=f"school-csm-{school_id}-{tunnel_id[:8]}",
                tunnel_id=tunnel_id,
                origin_port=_valid_port(origin_port),
            ),
        )
        service_id = str(result.get("service_id") or "") if isinstance(result, Mapping) else ""
        if not service_id:
            raise ServiceError("Cloudflare did not return a Workers VPC service identity.")
        return service_id

    @staticmethod
    def _service_payload(*, name: str, tunnel_id: str, origin_port: int) -> Mapping[str, Any]:
        return {
            "type": "http",
            "name": name,
            "http_port": _valid_port(origin_port),
            "host": {"ipv4": "127.0.0.1", "network": {"tunnel_id": tunnel_id}},
        }

    def _service_path(self, service_id: str = "") -> str:
        base = (
            f"/client/v4/accounts/{quote(self.account_id, safe='')}"
            "/connectivity/directory/services"
        )
        return f"{base}/{quote(service_id, safe='')}" if service_id else base

    def _services_for_tunnel(self, tunnel_id: str) -> list[Mapping[str, Any]]:
        # A district account is intentionally small.  Request a bounded page and
        # fail closed rather than accidentally managing an unknown later page.
        result = self._request("GET", f"{self._service_path()}?page=1&per_page=100")
        rows = result if isinstance(result, list) else []
        matches = []
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            host = row.get("host")
            network = host.get("network") if isinstance(host, Mapping) else None
            if isinstance(network, Mapping) and str(network.get("tunnel_id") or "") == tunnel_id:
                matches.append(row)
        return matches

    def _service_for_tunnel(self, tunnel_id: str) -> Mapping[str, Any]:
        matches = self._services_for_tunnel(tunnel_id)
        if len(matches) != 1:
            raise ServiceError("The school's Workers VPC service could not be identified safely.")
        return matches[0]

    def _upload_worker(self, script: str, public_host: str, service_id: str) -> None:
        try:
            source = self.worker_source.read_bytes()
        except OSError as exc:
            raise ServiceError("The reviewed School CSM Worker source could not be read.") from exc
        if not source or len(source) > self.MAX_WORKER_BYTES:
            raise ServiceError("The reviewed School CSM Worker source has an invalid size.")
        metadata = {
            "main_module": "worker.js",
            "compatibility_date": self.compatibility_date,
            "bindings": [
                {
                    "type": "vpc_service",
                    "name": self.VPC_BINDING,
                    "service_id": service_id,
                },
                {"type": "plain_text", "name": "PUBLIC_HOST", "text": public_host},
            ],
        }
        boundary = f"school-csm-{secrets.token_hex(16)}"
        body = _multipart_body(boundary, metadata, source)
        self._request_body(
            "PUT",
            f"/client/v4/accounts/{quote(self.account_id, safe='')}/workers/scripts/"
            f"{quote(script, safe='')}",
            body=body,
            extra_headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )

    def _enable_worker_subdomain(self, script: str) -> None:
        self._request(
            "POST",
            f"/client/v4/accounts/{quote(self.account_id, safe='')}/workers/scripts/"
            f"{quote(script, safe='')}/subdomain",
            {"enabled": True, "previews_enabled": False},
        )

    def _worker_binding_service(self, script: str) -> str:
        result = self._request(
            "GET",
            f"/client/v4/accounts/{quote(self.account_id, safe='')}/workers/scripts/"
            f"{quote(script, safe='')}/settings",
        )
        bindings = result.get("bindings") if isinstance(result, Mapping) else None
        for binding in bindings if isinstance(bindings, list) else []:
            if (
                isinstance(binding, Mapping)
                and binding.get("type") == "vpc_service"
                and binding.get("name") == self.VPC_BINDING
            ):
                return str(binding.get("service_id") or "")
        return ""

    def _best_effort_delete_worker(self, script: str) -> None:
        try:
            self._request(
                "DELETE",
                f"/client/v4/accounts/{quote(self.account_id, safe='')}/workers/scripts/"
                f"{quote(script, safe='')}?force=false",
            )
        except Exception:
            pass

    def _best_effort_delete_service(self, service_id: str) -> None:
        try:
            self._request("DELETE", self._service_path(service_id))
        except Exception:
            pass


def _multipart_body(boundary: str, metadata: Mapping[str, Any], source: bytes) -> bytes:
    marker = boundary.encode("ascii")
    metadata_bytes = json.dumps(dict(metadata), separators=(",", ":")).encode("utf-8")
    return b"".join(
        (
            b"--" + marker + b"\r\n",
            b'Content-Disposition: form-data; name="metadata"\r\n',
            b"Content-Type: application/json\r\n\r\n",
            metadata_bytes,
            b"\r\n--" + marker + b"\r\n",
            b'Content-Disposition: form-data; name="worker.js"; filename="worker.js"\r\n',
            b"Content-Type: application/javascript+module\r\n\r\n",
            source,
            b"\r\n--" + marker + b"--\r\n",
        )
    )


def _valid_port(value: Any) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError):
        port = 0
    if not 1 <= port <= 65535:
        raise ServiceError("The local origin port is invalid.")
    return port
