"""Bounded HTTPS client and signed authorization verification."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import base64
import http.client
import json
import re
import socket
import ssl
from typing import Any, Mapping
from urllib.parse import quote, urlparse
from uuid import UUID

from .config import GatewayProviderConfig


MAX_RESPONSE_BYTES = 512 * 1024
PUBLIC_HEALTH_MAX_RESPONSE_BYTES = 4096
_DNS_LABEL_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
AUTHORIZATION_STATES = {
    "active",
    "transfer_pending",
    "transferred",
    "revoked",
    "registration_error",
}


class GatewayClientError(RuntimeError):
    """A safe, non-secret gateway request or verification failure."""


@dataclass(frozen=True)
class GatewayAuthorization:
    school_id: str
    installation_id: str
    state: str
    registration_id: str
    public_host: str
    tunnel_id: str
    transaction_id: str
    issued_at: str
    expires_at: str
    key_id: str
    envelope: Mapping[str, Any]

    @property
    def active(self) -> bool:
        return self.state == "active"


class GatewayProviderClient:
    """Registration-service client with no ambient credentials or redirects."""

    def __init__(
        self,
        config: GatewayProviderConfig,
        *,
        timeout_seconds: float = 20.0,
        connection_factory: Any | None = None,
    ) -> None:
        self.config = config
        self.timeout_seconds = max(3.0, min(60.0, float(timeout_seconds)))
        self._connection_factory = connection_factory

    def lookup_school(self, school_id: str) -> Mapping[str, Any]:
        return self._request("GET", f"/v1/schools/{quote(str(school_id), safe='')}")

    def health_check(self) -> bool:
        """Verify the configured registration-service HTTPS endpoint."""

        document = self._request("GET", "/healthz")
        if document.get("ok") is not True or document.get("service") != (
            "school-csm-gateway-registration"
        ):
            raise GatewayClientError(
                "The Internet Gateway registration service returned an unexpected health response."
            )
        return True

    def begin_registration(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        return self._request("POST", "/v1/registrations/begin", payload)

    def complete_registration(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        return self._request("POST", "/v1/registrations/complete", payload)

    def begin_transfer(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        return self._request("POST", "/v1/transfers/begin", payload)

    def create_transfer_intent(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        """Bind a browser passkey ceremony to desktop-verified transfer facts."""

        return self._request("POST", "/v1/transfers/intents", payload)

    def complete_transfer(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        return self._request("POST", "/v1/transfers/complete", payload)

    def authorization_status(
        self,
        school_id: str,
        installation_id: str,
        installation_secret: str,
    ) -> GatewayAuthorization:
        document = self._request(
            "GET",
            "/v1/installations/"
            + quote(str(installation_id), safe="")
            + "/authorization?school_id="
            + quote(str(school_id), safe=""),
            bearer=installation_secret,
        )
        return verify_authorization_envelope(
            document,
            self.config,
            expected_school_id=school_id,
            expected_installation_id=installation_id,
        )

    def tunnel_credential(
        self,
        school_id: str,
        installation_id: str,
        installation_secret: str,
        *,
        origin_port: int,
    ) -> Mapping[str, Any]:
        return self._request(
            "POST",
            f"/v1/installations/{quote(str(installation_id), safe='')}/tunnel-credential",
            {"school_id": str(school_id), "origin_port": int(origin_port)},
            bearer=installation_secret,
        )

    def redeem_handoff(
        self, completion_code: str, installation_id: str
    ) -> Mapping[str, Any]:
        return self._request(
            "POST",
            "/v1/handoffs/redeem",
            {
                "completion_code": str(completion_code),
                "installation_id": str(installation_id),
            },
        )

    def acknowledge_handoff(
        self,
        completion_code: str,
        installation_id: str,
        acknowledgement_token: str,
    ) -> Mapping[str, Any]:
        """Confirm that the destination durably saved delivered credentials."""

        return self._request(
            "POST",
            "/v1/handoffs/acknowledge",
            {
                "completion_code": str(completion_code),
                "installation_id": str(installation_id),
                "acknowledgement_token": str(acknowledgement_token),
            },
        )

    def _request(
        self,
        method: str,
        path: str,
        payload: Mapping[str, Any] | None = None,
        *,
        bearer: str = "",
    ) -> Mapping[str, Any]:
        parsed = urlparse(self.config.service_url)
        body = b""
        headers = {
            "Accept": "application/json",
            "User-Agent": "School-CSM-Control-Center-Gateway/1",
            "Connection": "close",
        }
        if payload is not None:
            body = json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            if len(body) > 256 * 1024:
                raise GatewayClientError("The registration request is too large.")
            headers["Content-Type"] = "application/json"
            headers["Content-Length"] = str(len(body))
        if bearer:
            if any(character.isspace() for character in bearer):
                raise GatewayClientError("The saved installation credential is invalid.")
            headers["Authorization"] = f"Bearer {bearer}"

        connection = None
        try:
            if self._connection_factory is None:
                context = ssl.create_default_context()
                connection = http.client.HTTPSConnection(
                    parsed.hostname,
                    parsed.port or 443,
                    timeout=self.timeout_seconds,
                    context=context,
                )
            else:
                connection = self._connection_factory(parsed, self.timeout_seconds)
            base_path = parsed.path
            request_path = (base_path.rstrip("/") + "/" + path.lstrip("/")) or "/"
            connection.request(str(method).upper(), request_path, body=body or None, headers=headers)
            response = connection.getresponse()
            length_header = response.getheader("Content-Length")
            if length_header:
                try:
                    if int(length_header) > MAX_RESPONSE_BYTES:
                        raise GatewayClientError("The registration service response is too large.")
                except ValueError:
                    raise GatewayClientError("The registration service returned an invalid response size.") from None
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise GatewayClientError("The registration service response is too large.")
            try:
                document = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise GatewayClientError("The registration service returned an unreadable response.") from None
            if not isinstance(document, Mapping):
                raise GatewayClientError("The registration service returned an invalid response.")
            if not 200 <= int(response.status) < 300:
                message = str(document.get("detail") or document.get("error") or "Registration service request failed.")
                raise GatewayClientError(message[:300])
            return dict(document)
        except GatewayClientError:
            raise
        except (OSError, TimeoutError, http.client.HTTPException, ssl.SSLError) as exc:
            raise GatewayClientError(
                "The Internet Gateway registration service could not be reached securely."
            ) from exc
        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass


def probe_public_gateway(
    public_host: str,
    *,
    timeout_seconds: float = 10.0,
    connection_factory: Any | None = None,
    resolver: Any | None = None,
) -> Mapping[str, bool]:
    """Perform a bounded, body-free public DNS/HTTPS/host-routing diagnostic.

    Only three booleans are returned. Resolved addresses, certificate details,
    response content, and connection errors are deliberately not retained.
    Redirects are never followed.
    """

    host = _validated_public_host(public_host)
    result = {
        "public_dns": False,
        "public_https": False,
        "host_routing": False,
    }
    try:
        if resolver is None:
            resolved = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        else:
            resolved = resolver(host, 443)
        if not resolved:
            return result
        result["public_dns"] = True
    except (OSError, socket.gaierror):
        return result

    connection = None
    try:
        timeout = max(3.0, min(30.0, float(timeout_seconds)))
        if connection_factory is None:
            connection = http.client.HTTPSConnection(
                host,
                443,
                timeout=timeout,
                context=ssl.create_default_context(),
            )
        else:
            connection = connection_factory(host, timeout)
        connection.request(
            "GET",
            "/healthz",
            headers={
                "Accept": "application/json",
                "User-Agent": "School-CSM-Control-Center-Gateway-Diagnostics/1",
                "Connection": "close",
            },
        )
        response = connection.getresponse()
        result["public_https"] = True
        declared = response.getheader("Content-Length")
        if declared:
            try:
                if int(declared) > PUBLIC_HEALTH_MAX_RESPONSE_BYTES:
                    return result
            except (TypeError, ValueError):
                return result
        raw = response.read(PUBLIC_HEALTH_MAX_RESPONSE_BYTES + 1)
        if len(raw) > PUBLIC_HEALTH_MAX_RESPONSE_BYTES or int(response.status) != 200:
            return result
        try:
            document = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return result
        result["host_routing"] = bool(
            isinstance(document, Mapping)
            and document.get("ok") is True
            and document.get("service") == "school-csm-control-center"
        )
        return result
    except (OSError, TimeoutError, http.client.HTTPException, ssl.SSLError):
        return result
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass


def _validated_public_host(value: Any) -> str:
    host = str(value or "").strip().casefold().rstrip(".")
    labels = host.split(".")
    if (
        not host
        or len(host) > 253
        or len(labels) < 3
        or any(character in host for character in ":/\\\r\n\0")
        or any(not _DNS_LABEL_PATTERN.fullmatch(label) for label in labels)
    ):
        raise GatewayClientError("The public School CSM hostname is invalid.")
    return host


def verify_authorization_envelope(
    envelope: Mapping[str, Any],
    config: GatewayProviderConfig,
    *,
    expected_school_id: str,
    expected_installation_id: str,
    now: datetime | None = None,
) -> GatewayAuthorization:
    """Verify a service-signed authorization/retirement status envelope."""

    payload = envelope.get("payload")
    key_id = str(envelope.get("key_id") or "")
    signature_text = str(envelope.get("signature") or "")
    if not isinstance(payload, Mapping) or key_id not in config.signing_public_keys:
        raise GatewayClientError("The authorization status is not signed by a trusted provider key.")
    canonical = json.dumps(
        dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    try:
        signature = base64.b64decode(signature_text, validate=True)
    except (ValueError, TypeError):
        raise GatewayClientError("The authorization status signature is invalid.") from None
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        Ed25519PublicKey.from_public_bytes(config.signing_public_keys[key_id]).verify(
            signature, canonical
        )
    except ImportError:
        raise GatewayClientError(
            "Authorization verification is unavailable. Repair the application installation."
        ) from None
    except Exception:
        raise GatewayClientError("The authorization status signature could not be verified.") from None

    school_id = str(payload.get("school_id") or "")
    installation_id = str(payload.get("installation_id") or "")
    state = str(payload.get("authorization_state") or "").casefold()
    public_host = str(payload.get("public_host") or "").strip().casefold().rstrip(".")
    if school_id != str(expected_school_id) or installation_id != str(expected_installation_id):
        raise GatewayClientError("The authorization status belongs to a different school installation.")
    try:
        if str(UUID(installation_id)) != installation_id.casefold():
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise GatewayClientError("The authorization status installation identity is invalid.") from None
    if state not in AUTHORIZATION_STATES:
        raise GatewayClientError("The authorization status value is invalid.")
    if public_host != config.public_host_for(school_id):
        raise GatewayClientError("The authorization status contains an unexpected public host.")

    issued_at = _parse_service_time(payload.get("issued_at"), "issued")
    expires_at = _parse_service_time(payload.get("expires_at"), "expiry")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    if issued_at > current.replace(microsecond=0) or expires_at <= current:
        raise GatewayClientError("The authorization status is not currently valid.")
    if expires_at <= issued_at:
        raise GatewayClientError("The authorization status validity period is invalid.")

    return GatewayAuthorization(
        school_id=school_id,
        installation_id=installation_id,
        state=state,
        registration_id=str(payload.get("registration_id") or ""),
        public_host=public_host,
        tunnel_id=str(payload.get("tunnel_id") or ""),
        transaction_id=str(payload.get("transaction_id") or ""),
        issued_at=issued_at.isoformat(),
        expires_at=expires_at.isoformat(),
        key_id=key_id,
        envelope=dict(envelope),
    )


def _parse_service_time(value: Any, label: str) -> datetime:
    text = str(value or "")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise GatewayClientError(f"The authorization {label} timestamp is invalid.") from None
    if parsed.tzinfo is None:
        raise GatewayClientError(f"The authorization {label} timestamp must include a time zone.")
    return parsed.astimezone(timezone.utc)
