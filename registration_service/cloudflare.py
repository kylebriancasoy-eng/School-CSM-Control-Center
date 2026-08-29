"""Cloudflare Tunnel provisioner used only by the central service.

The Cloudflare API token remains in the service environment.  It is never
packaged in or sent to a school installation.  School devices receive only a
short-lived/rotatable tunnel connector token after active authorization.
"""

from __future__ import annotations

import http.client
import json
import os
import secrets
from typing import Any, Mapping
from urllib.parse import quote

from .core import ServiceError


class CloudflareTunnelProvisioner:
    API_HOST = "api.cloudflare.com"

    def __init__(
        self,
        *,
        account_id: str,
        zone_id: str,
        api_token: str,
        origin_url: str,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.account_id = str(account_id).strip()
        self.zone_id = str(zone_id).strip()
        self.api_token = str(api_token).strip()
        self.origin_url = str(origin_url).strip()
        self.timeout_seconds = max(5.0, min(60.0, float(timeout_seconds)))
        if not all((self.account_id, self.zone_id, self.api_token)):
            raise ValueError("Cloudflare account, zone, and API token are required.")
        if not self.origin_url.startswith("http://127.0.0.1:"):
            raise ValueError("Tunnel origin must be the local loopback HTTP server.")

    @classmethod
    def from_environment(cls) -> "CloudflareTunnelProvisioner":
        return cls(
            account_id=os.environ.get("CLOUDFLARE_ACCOUNT_ID", ""),
            zone_id=os.environ.get("CLOUDFLARE_ZONE_ID", ""),
            api_token=os.environ.get("CLOUDFLARE_API_TOKEN", ""),
            origin_url=os.environ.get("SCHOOL_CSM_TUNNEL_ORIGIN", "http://127.0.0.1:8080"),
        )

    def provision(self, *, school_id: str, public_host: str) -> Mapping[str, str]:
        result = self._request(
            "POST",
            f"/client/v4/accounts/{quote(self.account_id, safe='')}/cfd_tunnel",
            {"name": f"school-csm-{school_id}", "config_src": "cloudflare"},
        )
        tunnel_id = str(result.get("id") or "")
        if not tunnel_id:
            raise ServiceError("Cloudflare did not return a tunnel identity.")
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
        # A remote-tunnel token can be fetched again but is not independently
        # rotated.  Create a replacement tunnel and move the permanent DNS
        # hostname to it, which prevents the old device from serving that host.
        replacement = self._request(
            "POST",
            f"/client/v4/accounts/{quote(self.account_id, safe='')}/cfd_tunnel",
            {
                "name": f"school-csm-{school_id}-transfer-{secrets.token_hex(4)}",
                "config_src": "cloudflare",
            },
        )
        replacement_id = str(replacement.get("id") or "")
        if not replacement_id:
            raise ServiceError("Cloudflare did not return a replacement tunnel identity.")
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
        # Disconnect active connectors before deleting the remotely managed
        # tunnel; otherwise Cloudflare may reject deletion while the old school
        # computer is still online.
        self._request(
            "DELETE",
            f"/client/v4/accounts/{quote(self.account_id, safe='')}/cfd_tunnel/{quote(tunnel_id, safe='')}/connections",
        )
        self._request(
            "DELETE",
            f"/client/v4/accounts/{quote(self.account_id, safe='')}/cfd_tunnel/{quote(tunnel_id, safe='')}",
        )

    def rollback_rotation(
        self,
        *,
        source_tunnel_id: str,
        replacement_tunnel_id: str,
        public_host: str,
    ) -> None:
        """Restore the permanent hostname before deleting a failed replacement."""

        # The source tunnel was already configured while it was active.  DNS
        # restoration must not depend on an unnecessary second configuration
        # request: if that request failed, the hostname could remain routed to
        # a database-uncommitted replacement.  Restore the route first, then
        # retire only the replacement connector.
        self._route_dns(source_tunnel_id, public_host)
        self.revoke(tunnel_id=replacement_tunnel_id)

    def credential(
        self, *, tunnel_id: str, public_host: str, origin_port: int
    ) -> Mapping[str, str]:
        port = int(origin_port)
        if not 1 <= port <= 65535:
            raise ServiceError("The local origin port is invalid.")
        self._configure(
            tunnel_id,
            public_host,
            origin_url=f"http://127.0.0.1:{port}",
        )
        return {"tunnel_id": tunnel_id, "tunnel_token": self._token(tunnel_id)}

    def _configure(
        self, tunnel_id: str, public_host: str, *, origin_url: str | None = None
    ) -> None:
        service = str(origin_url or self.origin_url)
        if not service.startswith("http://127.0.0.1:"):
            raise ServiceError("The local tunnel origin is invalid.")
        self._request(
            "PUT",
            f"/client/v4/accounts/{quote(self.account_id, safe='')}/cfd_tunnel/{quote(tunnel_id, safe='')}/configurations",
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

    def _token(self, tunnel_id: str) -> str:
        result = self._request(
            "GET",
            f"/client/v4/accounts/{quote(self.account_id, safe='')}/cfd_tunnel/{quote(tunnel_id, safe='')}/token",
        )
        token = str(result if isinstance(result, str) else result.get("token") or "")
        if not token:
            raise ServiceError("Cloudflare did not return a tunnel connector token.")
        return token

    def _request(
        self, method: str, path: str, payload: Mapping[str, Any] | None = None
    ) -> Any:
        connection = http.client.HTTPSConnection(self.API_HOST, timeout=self.timeout_seconds)
        body = None
        headers = {
            "Authorization": f"Bearer {self.api_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "School-CSM-Registration-Service/1",
            "Connection": "close",
        }
        if payload is not None:
            body = json.dumps(dict(payload), separators=(",", ":")).encode("utf-8")
            if len(body) > 256 * 1024:
                raise ServiceError("Cloudflare provisioning request is too large.")
            headers["Content-Length"] = str(len(body))
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            raw = response.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise ServiceError("Cloudflare returned an oversized response.")
            document = json.loads(raw.decode("utf-8"))
            if not isinstance(document, Mapping) or not document.get("success"):
                raise ServiceError("Cloudflare could not complete tunnel provisioning.")
            return document.get("result")
        except ServiceError:
            raise
        except Exception as exc:
            raise ServiceError("Cloudflare tunnel provisioning could not be reached securely.") from exc
        finally:
            connection.close()
