"""Owner-driven API-token replacement without changing Cloudflare resources."""

from __future__ import annotations

import hmac
import http.client
import json
from typing import Any, Callable
from urllib.parse import quote

from .configuration import DistrictServerConfig
from .credentials import CLOUDFLARE_API_TOKEN_TARGET, DistrictCredentialStore


class ApiTokenRotationError(RuntimeError):
    """Only fixed, non-secret messages may cross the UI boundary."""


def _candidate(value: str) -> str:
    token = str(value).strip()
    if not 20 <= len(token) <= 2048 or any(
        character.isspace() or ord(character) < 33 or ord(character) > 126
        for character in token
    ):
        raise ApiTokenRotationError("Enter the complete replacement Cloudflare API token.")
    return token


def _read_cloudflare(path: str, token: str) -> Any:
    # HTTPSConnection does not follow redirects, so an Authorization header
    # cannot be forwarded to another host by an unexpected API response.
    connection = http.client.HTTPSConnection("api.cloudflare.com", timeout=20)
    try:
        connection.request(
            "GET", path,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )
        response = connection.getresponse()
        payload = response.read(1024 * 1024 + 1)
        if response.status != 200 or len(payload) > 1024 * 1024:
            raise ValueError("Rejected response")
        document = json.loads(payload)
        if not isinstance(document, dict) or document.get("success") is not True:
            raise ValueError("Rejected API result")
        return document.get("result")
    except Exception:
        # Do not expose a response body, URL, headers, or exception containing
        # the credential. Only the account owner's native dialog sees input.
        raise ApiTokenRotationError(
            "Cloudflare could not verify the replacement. Check its account, "
            "permissions, expiry, and Internet connection. No token was saved."
        ) from None
    finally:
        connection.close()


def verify_replacement(
    config: DistrictServerConfig,
    token: str,
    *,
    reader: Callable[[str, str], Any] = _read_cloudflare,
) -> None:
    """Check active status and read access, not a claim of write-policy proof.

    The owner must review the three required write permissions in Cloudflare.
    A verify response alone does not disclose or certify least-privilege policy.
    """
    token = _candidate(token)
    account = quote(config.cloudflare_account_id, safe="")
    base = f"/client/v4/accounts/{account}"
    verify_path = f"{base}/tokens/verify" if token.startswith("cfat_") else "/client/v4/user/tokens/verify"
    try:
        status = reader(verify_path, token)
        tunnel = reader(f"{base}/cfd_tunnel/{quote(config.management_tunnel_id, safe='')}", token)
        service = reader(
            f"{base}/connectivity/directory/services/{quote(config.management_vpc_service_id, safe='')}", token,
        )
        workers = reader(f"{base}/workers/scripts", token)
        if not isinstance(status, dict) or status.get("status") != "active":
            raise ValueError("Inactive")
        if not isinstance(tunnel, dict) or tunnel.get("id") != config.management_tunnel_id:
            raise ValueError("Wrong tunnel")
        if not isinstance(service, dict) or service.get("service_id") != config.management_vpc_service_id:
            raise ValueError("Wrong service")
        if not isinstance(workers, list) or not any(
            isinstance(worker, dict) and worker.get("id") == config.management_worker_name
            for worker in workers
        ):
            raise ValueError("Worker unavailable")
    except Exception:
        raise ApiTokenRotationError(
            "The replacement could not be verified for this district account "
            "and its existing gateway resources. No token was saved."
        ) from None


def replace_cloudflare_api_token(
    config: DistrictServerConfig,
    candidate: str,
    controller,
    *,
    credential_store: DistrictCredentialStore | None = None,
    verifier: Callable[[DistrictServerConfig, str], None] = verify_replacement,
) -> None:
    """Verify before mutation, save only the API token, and recover on failure.

    Does not create, change, or revoke any Cloudflare resource or token. The
    account owner revokes the exposed token only after confirming operation.
    """
    token = _candidate(candidate)
    store = credential_store or DistrictCredentialStore()
    try:
        previous = store.load().cloudflare_api_token
        verifier(config, token)
    except Exception:
        raise ApiTokenRotationError(
            "The replacement could not be verified. The saved credentials and "
            "running service were not changed."
        ) from None
    if hmac.compare_digest(previous, token):
        raise ApiTokenRotationError("The replacement must differ from the saved API token.")
    try:
        controller.stop()
        store.save(CLOUDFLARE_API_TOKEN_TARGET, token)
        if not hmac.compare_digest(store.load().cloudflare_api_token, token):
            raise ValueError("Persistence check failed")
        if controller.start().state != "running":
            raise ValueError("Restart did not complete")
    except Exception:
        try:
            controller.stop()
            store.save(CLOUDFLARE_API_TOKEN_TARGET, previous)
            if not hmac.compare_digest(store.load().cloudflare_api_token, previous):
                raise ValueError("Recovery persistence failed")
            if controller.start().state != "running":
                raise ValueError("Recovery restart failed")
        except Exception:
            raise ApiTokenRotationError(
                "The replacement was not activated and automatic recovery did "
                "not finish. Do not revoke either token. Reopen the district app "
                "and use the encrypted recovery backup if necessary."
            ) from None
        raise ApiTokenRotationError(
            "The replacement was not activated. The previous saved API token "
            "was restored and the district service restarted. Do not revoke it yet."
        ) from None
