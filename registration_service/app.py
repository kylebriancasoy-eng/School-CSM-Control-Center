"""FastAPI/WebAuthn boundary for the reference registration service."""

from __future__ import annotations

import base64
from collections import OrderedDict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import hashlib
import secrets
from threading import RLock
from time import monotonic
from typing import Any, Mapping

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers.structs import (
    AttestationConveyancePreference,
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from .cloudflare import CloudflareTunnelProvisioner
from .core import RegistrationRepository, RegistrationService, ServiceError
from .signing import AuthorizationEnvelopeSigner
from .handoff import CredentialHandoffVault


class _EphemeralRateLimiter:
    """Small in-memory fixed-window limiter with non-reversible client keys."""

    def __init__(self) -> None:
        self._salt = secrets.token_bytes(32)
        self._lock = RLock()
        self._buckets: OrderedDict[str, tuple[float, int, int]] = OrderedDict()
        self._maximum_buckets = 10_000

    def allow(self, client: str, route: str, *, limit: int, window: int) -> bool:
        digest = hashlib.sha256(
            self._salt + str(client).encode("utf-8", "replace") + b"\0" + route.encode("ascii")
        ).hexdigest()
        now = monotonic()
        with self._lock:
            started, count, retained_window = self._buckets.get(
                digest, (now, 0, int(window))
            )
            if now - started >= window:
                started, count = now, 0
            if count >= limit:
                self._buckets.move_to_end(digest)
                return False
            if digest not in self._buckets and len(self._buckets) >= self._maximum_buckets:
                expired = [
                    key
                    for key, value in self._buckets.items()
                    if now - value[0] >= value[2]
                ]
                for key in expired:
                    self._buckets.pop(key, None)
            while digest not in self._buckets and len(self._buckets) >= self._maximum_buckets:
                self._buckets.popitem(last=False)
            self._buckets[digest] = (started, count + 1, int(window or retained_window))
            self._buckets.move_to_end(digest)
            return True


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RegistrationBegin(StrictModel):
    school_id: str
    school_name: str = Field(min_length=1, max_length=200)
    installation_id: str
    activation_code: str = Field(min_length=8, max_length=120)


class RegistrationComplete(RegistrationBegin):
    ceremony_id: str
    passkey_response: dict[str, Any]
    passkey_label: str = Field(default="Initial administrator passkey", max_length=120)


class TransferIntentCreate(StrictModel):
    school_id: str
    destination_installation_id: str
    transfer_mode: str
    data_validation: dict[str, Any] = Field(default_factory=dict)


class TransferIntentReference(StrictModel):
    intent_id: str = Field(min_length=32, max_length=96)


class TransferBegin(TransferIntentReference):
    pass


class TransferComplete(TransferIntentReference):
    ceremony_id: str
    passkey_response: dict[str, Any]


class TunnelCredentialRequest(StrictModel):
    school_id: str
    origin_port: int = Field(ge=1, le=65535)


class HandoffRedeemRequest(StrictModel):
    completion_code: str = Field(min_length=32, max_length=64)
    installation_id: str


class HandoffAcknowledgeRequest(HandoffRedeemRequest):
    acknowledgement_token: str = Field(min_length=32, max_length=128)


class PasskeyAddBegin(StrictModel):
    school_id: str


class PasskeyAddAuthorize(PasskeyAddBegin):
    ceremony_id: str
    passkey_response: dict[str, Any]


class PasskeyAddComplete(PasskeyAddBegin):
    ceremony_id: str
    passkey_response: dict[str, Any]
    passkey_label: str = Field(default="Additional administrator passkey", max_length=120)


class PasskeyResetBegin(StrictModel):
    school_id: str
    reset_code: str = Field(min_length=12, max_length=120)


class PasskeyResetComplete(PasskeyResetBegin):
    ceremony_id: str
    passkey_response: dict[str, Any]
    passkey_label: str = Field(default="Recovered administrator passkey", max_length=120)


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(bytes(value)).decode("ascii").rstrip("=")


def _origin() -> str:
    origin = os.environ.get("SCHOOL_CSM_WEBAUTHN_ORIGIN", "").strip().rstrip("/")
    if not origin.startswith("https://"):
        raise RuntimeError("SCHOOL_CSM_WEBAUTHN_ORIGIN must be configured as HTTPS.")
    return origin


def _rp_id() -> str:
    value = os.environ.get("SCHOOL_CSM_WEBAUTHN_RP_ID", "").strip().casefold()
    if not value or ":" in value or "/" in value:
        raise RuntimeError("SCHOOL_CSM_WEBAUTHN_RP_ID is not configured.")
    return value


def build_service() -> RegistrationService:
    database = Path(os.environ.get("SCHOOL_CSM_REGISTRATION_DATABASE", "registration.sqlite3"))
    domain = os.environ.get("SCHOOL_CSM_MANAGED_DOMAIN", "").strip()
    signer = AuthorizationEnvelopeSigner.from_environment()
    provisioner = CloudflareTunnelProvisioner.from_environment()
    return RegistrationService(
        RegistrationRepository(database),
        managed_domain=domain,
        signer=signer,
        provisioner=provisioner,
    )


def create_app(service: RegistrationService | None = None) -> FastAPI:
    app = FastAPI(
        title="School CSM Internet Gateway Registration Service",
        version="1.0.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.registration_service = service
    app.state.handoff_vault = None
    app.state.rate_limiter = _EphemeralRateLimiter()

    def current_service(request: Request) -> RegistrationService:
        if request.app.state.registration_service is None:
            try:
                request.app.state.registration_service = build_service()
            except Exception as exc:
                raise HTTPException(503, "Registration service is not provisioned.") from exc
        return request.app.state.registration_service

    def current_handoff_vault(
        request: Request,
        service: RegistrationService = Depends(current_service),
    ) -> CredentialHandoffVault:
        if request.app.state.handoff_vault is None:
            try:
                request.app.state.handoff_vault = CredentialHandoffVault.from_environment(
                    service.repository
                )
            except Exception as exc:
                raise HTTPException(503, "Credential handoff is not provisioned.") from exc
        return request.app.state.handoff_vault

    @app.exception_handler(ServiceError)
    async def service_error_handler(_request: Request, exc: ServiceError):
        from fastapi.responses import JSONResponse

        status = 409 if exc.code in {
            "school_already_registered", "data_validation_required", "school_not_active"
        } else 400
        if exc.code in {"authorization_failed", "passkey_not_found"}:
            status = 401
        return JSONResponse(status_code=status, content={"ok": False, "code": exc.code, "detail": str(exc)})

    @app.middleware("http")
    async def service_security_headers(request: Request, call_next):
        if request.url.scheme != "https" and not _is_test_client(request):
            return _json_error(400, "HTTPS is required.")
        if request.method in {"POST", "PUT", "PATCH"}:
            content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().casefold()
            if content_type != "application/json":
                return _json_error(415, "Application/json is required.")
            try:
                declared = int(request.headers.get("content-length", "0"))
            except ValueError:
                return _json_error(400, "The request size is invalid.")
            if declared <= 0 or declared > 1024 * 1024:
                return _json_error(413, "The request body is too large.")
        path = request.url.path
        if (
            path.startswith("/v1/registrations")
            or path.startswith("/v1/transfers")
            or path.startswith("/v1/passkeys")
        ):
            client = request.client.host if request.client else "unknown"
            if not request.app.state.rate_limiter.allow(
                client, "browser-authorization", limit=20, window=3600
            ):
                return _json_error(429, "Too many authorization attempts. Try again later.")
        elif path.startswith("/v1/handoffs"):
            client = request.client.host if request.client else "unknown"
            if not request.app.state.rate_limiter.allow(
                client, "credential-handoff", limit=12, window=600
            ):
                return _json_error(429, "Too many completion-code attempts. Try again later.")
        elif path.startswith("/v1/installations/"):
            client = request.client.host if request.client else "unknown"
            parts = path.split("/")
            installation = parts[3] if len(parts) > 3 else "unknown"
            client_allowed = request.app.state.rate_limiter.allow(
                client, "installation-auth-client", limit=60, window=600
            )
            installation_allowed = request.app.state.rate_limiter.allow(
                installation, "installation-auth-target", limit=12, window=600
            )
            if not client_allowed or not installation_allowed:
                return _json_error(
                    429,
                    "Too many installation authorization attempts. Try again later.",
                )
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; "
            "connect-src 'self'; img-src 'self' data:; base-uri 'none'; "
            "form-action 'self'; frame-ancestors 'none'"
        )
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        return response

    @app.get("/healthz")
    def healthz():
        return {"ok": True, "service": "school-csm-gateway-registration"}

    @app.get("/manage", response_class=FileResponse)
    def manage_page():
        return FileResponse(
            Path(__file__).resolve().parent / "static" / "manage.html",
            media_type="text/html",
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/assets/manage.js", response_class=FileResponse)
    def manage_script():
        return FileResponse(
            Path(__file__).resolve().parent / "static" / "manage.js",
            media_type="application/javascript",
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/v1/schools/{school_id}")
    def school_lookup(school_id: str, service: RegistrationService = Depends(current_service)):
        return service.school_lookup(school_id)

    @app.post("/v1/registrations/begin")
    def begin_registration(body: RegistrationBegin, service: RegistrationService = Depends(current_service)):
        # Validate the one-time activation code without consuming it.  It is
        # consumed atomically only after the passkey ceremony succeeds.
        digest = __import__("hashlib").sha256(body.activation_code.strip().upper().encode()).hexdigest()
        with service.repository.read() as connection:
            activation = connection.execute(
                "SELECT * FROM activation_codes WHERE code_hash=? AND school_id=?",
                (digest, body.school_id),
            ).fetchone()
        if (
            activation is None
            or activation["used_at"]
            or datetime.fromisoformat(activation["expires_at"]) <= datetime.now(timezone.utc)
        ):
            raise ServiceError("The activation code is invalid or expired.", code="activation_invalid")
        options = generate_registration_options(
            rp_id=_rp_id(),
            rp_name="School CSM Internet Gateway",
            user_id=body.school_id.encode("ascii"),
            user_name=body.school_id,
            user_display_name=body.school_name,
            authenticator_selection=AuthenticatorSelectionCriteria(
                resident_key=ResidentKeyRequirement.PREFERRED,
                user_verification=UserVerificationRequirement.REQUIRED,
            ),
            attestation=AttestationConveyancePreference.NONE,
        )
        ceremony_id = service.repository.create_ceremony(
            purpose="registration",
            school_id=body.school_id,
            installation_id=body.installation_id,
            challenge=options.challenge,
            context={"activation_code_hash": digest, "school_name": body.school_name},
        )
        return {"ceremony_id": ceremony_id, "public_key": json.loads(options_to_json(options))}

    @app.post("/v1/registrations/complete")
    def complete_registration(
        body: RegistrationComplete,
        service: RegistrationService = Depends(current_service),
        vault: CredentialHandoffVault = Depends(current_handoff_vault),
    ):
        operation_id = f"registration:{body.ceremony_id}"
        request_fingerprint = vault.request_fingerprint(body.model_dump(mode="json"))
        recovered = vault.recover_code(
            operation_id,
            body.installation_id,
            request_fingerprint,
        )
        if recovered is not None:
            return {"ok": True, "completion_code": recovered, "expires_in_seconds": 600}

        ceremony = service.repository.consume_ceremony(body.ceremony_id, "registration")
        consumption_token = str(ceremony.get("consumption_token") or "")
        try:
            if ceremony["school_id"] != body.school_id or ceremony["installation_id"] != body.installation_id:
                raise ServiceError("The passkey ceremony belongs to another registration.")
            expected_hash = ceremony["context"].get("activation_code_hash")
            actual_hash = hashlib.sha256(
                body.activation_code.strip().upper().encode()
            ).hexdigest()
            if actual_hash != expected_hash:
                raise ServiceError(
                    "The activation code does not match the ceremony.",
                    code="activation_invalid",
                )
            verification = verify_registration_response(
                credential=body.passkey_response,
                expected_challenge=ceremony["challenge"],
                expected_origin=_origin(),
                expected_rp_id=_rp_id(),
                require_user_verification=True,
            )
            result = service.complete_initial_registration(
                school_id=body.school_id,
                school_name=body.school_name,
                installation_id=body.installation_id,
                activation_code=body.activation_code,
                credential_id=_b64url(verification.credential_id),
                credential_public_key=verification.credential_public_key,
                sign_count=verification.sign_count,
                passkey_label=body.passkey_label,
                handoff_writer=vault,
                handoff_operation_id=operation_id,
                handoff_request_fingerprint=request_fingerprint,
            )
            if not result.completion_code:
                raise ServiceError(
                    "Credential handoff was not committed.", code="handoff_invalid"
                )
            return {
                "ok": True,
                "completion_code": result.completion_code,
                "expires_in_seconds": 600,
            }
        except Exception:
            service.repository.release_ceremony(
                body.ceremony_id, "registration", consumption_token
            )
            raise

    @app.post("/v1/transfers/intents")
    def create_transfer_intent(
        body: TransferIntentCreate,
        service: RegistrationService = Depends(current_service),
    ):
        return service.repository.create_transfer_intent(
            school_id=body.school_id,
            destination_installation_id=body.destination_installation_id,
            transfer_mode=body.transfer_mode,
            data_validation=body.data_validation,
        )

    @app.post("/v1/transfers/intents/inspect")
    def inspect_transfer_intent(
        body: TransferIntentReference,
        service: RegistrationService = Depends(current_service),
    ):
        intent = service.repository.transfer_intent(body.intent_id)
        return {
            "school_id": intent["school_id"],
            "destination_installation_id": intent["destination_installation_id"],
            "transfer_mode": intent["transfer_mode"],
            "data_verified": bool(intent["data_validation"].get("verified")),
            "expires_at": intent["expires_at"],
        }

    @app.post("/v1/transfers/begin")
    def begin_transfer(body: TransferBegin, service: RegistrationService = Depends(current_service)):
        intent = service.repository.transfer_intent(body.intent_id)
        with service.repository.read() as connection:
            rows = connection.execute(
                "SELECT credential_id FROM passkeys WHERE school_id=? ORDER BY created_at",
                (intent["school_id"],),
            ).fetchall()
        if not rows:
            raise ServiceError("No passkey is registered for this School ID.", code="passkey_not_found")
        descriptors = [
            PublicKeyCredentialDescriptor(id=_decode_credential_id(row["credential_id"]))
            for row in rows
        ]
        options = generate_authentication_options(
            rp_id=_rp_id(),
            allow_credentials=descriptors,
            user_verification=UserVerificationRequirement.REQUIRED,
        )
        ceremony_id = service.repository.create_ceremony(
            purpose="transfer",
            school_id=intent["school_id"],
            installation_id=intent["destination_installation_id"],
            challenge=options.challenge,
            context={
                "intent_hash": hashlib.sha256(body.intent_id.encode("utf-8")).hexdigest(),
                "transfer_mode": intent["transfer_mode"],
                "data_validation": intent["data_validation"],
            },
        )
        return {"ceremony_id": ceremony_id, "public_key": json.loads(options_to_json(options))}

    @app.post("/v1/transfers/complete")
    def complete_transfer(
        body: TransferComplete,
        service: RegistrationService = Depends(current_service),
        vault: CredentialHandoffVault = Depends(current_handoff_vault),
    ):
        intent_digest = hashlib.sha256(body.intent_id.encode("utf-8")).hexdigest()
        operation_id = f"transfer:{body.ceremony_id}:{intent_digest}"
        request_fingerprint = vault.request_fingerprint(body.model_dump(mode="json"))
        recovered = vault.recover_operation_code(operation_id, request_fingerprint)
        if recovered is not None:
            completion_code, _installation_id = recovered
            return {
                "ok": True,
                "completion_code": completion_code,
                "expires_in_seconds": 600,
            }

        ceremony = service.repository.consume_ceremony(body.ceremony_id, "transfer")
        ceremony_token = str(ceremony.get("consumption_token") or "")
        intent_token = ""
        try:
            if ceremony["context"].get("intent_hash") != intent_digest:
                raise ServiceError("The passkey ceremony belongs to another transfer.")
            intent = service.repository.transfer_intent(body.intent_id)
            if (
                ceremony["school_id"] != intent["school_id"]
                or ceremony["installation_id"]
                != intent["destination_installation_id"]
            ):
                raise ServiceError("The passkey ceremony belongs to another transfer.")
            raw_id = str(
                body.passkey_response.get("rawId")
                or body.passkey_response.get("id")
                or ""
            )
            credential_id = _canonical_credential_id(raw_id)
            with service.repository.read() as connection:
                passkey = connection.execute(
                    "SELECT * FROM passkeys WHERE school_id=? AND credential_id=?",
                    (intent["school_id"], credential_id),
                ).fetchone()
            if passkey is None:
                raise ServiceError(
                    "The registered passkey was not found.", code="passkey_not_found"
                )
            verification = verify_authentication_response(
                credential=body.passkey_response,
                expected_challenge=ceremony["challenge"],
                expected_rp_id=_rp_id(),
                expected_origin=_origin(),
                credential_public_key=bytes(passkey["public_key"]),
                credential_current_sign_count=int(passkey["sign_count"]),
                require_user_verification=True,
            )
            consumed_intent = service.repository.transfer_intent(
                body.intent_id, consume=True
            )
            intent_token = str(consumed_intent.get("consumption_token") or "")
            context = ceremony["context"]
            result = service.complete_transfer(
                school_id=consumed_intent["school_id"],
                destination_installation_id=consumed_intent[
                    "destination_installation_id"
                ],
                credential_id=credential_id,
                new_sign_count=verification.new_sign_count,
                transfer_mode=str(
                    context.get("transfer_mode")
                    or consumed_intent["transfer_mode"]
                ),
                data_validation=(
                    context.get("data_validation")
                    if isinstance(context.get("data_validation"), Mapping)
                    else {}
                ),
                handoff_writer=vault,
                handoff_operation_id=operation_id,
                handoff_request_fingerprint=request_fingerprint,
            )
            completion_code = str(result.get("completion_code") or "")
            if not completion_code:
                raise ServiceError(
                    "Credential handoff was not committed.", code="handoff_invalid"
                )
            return {
                "ok": True,
                "completion_code": completion_code,
                "expires_in_seconds": 600,
            }
        except Exception:
            if intent_token:
                service.repository.release_transfer_intent(
                    body.intent_id, intent_token
                )
            service.repository.release_ceremony(
                body.ceremony_id, "transfer", ceremony_token
            )
            raise

    @app.post("/v1/handoffs/redeem")
    def redeem_handoff(
        body: HandoffRedeemRequest,
        vault: CredentialHandoffVault = Depends(current_handoff_vault),
    ):
        return {"ok": True, **vault.redeem(body.completion_code, body.installation_id)}

    @app.post("/v1/handoffs/acknowledge")
    def acknowledge_handoff(
        body: HandoffAcknowledgeRequest,
        vault: CredentialHandoffVault = Depends(current_handoff_vault),
    ):
        return {
            "ok": True,
            **vault.acknowledge(
                body.completion_code,
                body.installation_id,
                body.acknowledgement_token,
            ),
        }

    @app.post("/v1/passkeys/add/begin")
    def begin_add_passkey(
        body: PasskeyAddBegin,
        service: RegistrationService = Depends(current_service),
    ):
        passkeys = service.list_passkeys(body.school_id)
        if not passkeys:
            raise ServiceError("No passkey is registered for this School ID.", code="passkey_not_found")
        options = generate_authentication_options(
            rp_id=_rp_id(),
            allow_credentials=[
                PublicKeyCredentialDescriptor(id=_decode_credential_id(row["credential_id"]))
                for row in passkeys
            ],
            user_verification=UserVerificationRequirement.REQUIRED,
        )
        ceremony_id = service.repository.create_passkey_ceremony(
            purpose="add_authorization",
            school_id=body.school_id,
            challenge=options.challenge,
            context={},
        )
        return {"ceremony_id": ceremony_id, "public_key": json.loads(options_to_json(options))}

    @app.post("/v1/passkeys/add/authorize")
    def authorize_add_passkey(
        body: PasskeyAddAuthorize,
        service: RegistrationService = Depends(current_service),
    ):
        ceremony = service.repository.consume_passkey_ceremony(
            body.ceremony_id, "add_authorization"
        )
        if ceremony["school_id"] != body.school_id:
            raise ServiceError("The passkey ceremony belongs to another school.")
        credential_id, passkey = _registered_passkey(
            service, body.school_id, body.passkey_response
        )
        verification = verify_authentication_response(
            credential=body.passkey_response,
            expected_challenge=ceremony["challenge"],
            expected_rp_id=_rp_id(),
            expected_origin=_origin(),
            credential_public_key=bytes(passkey["public_key"]),
            credential_current_sign_count=int(passkey["sign_count"]),
            require_user_verification=True,
        )
        with service.repository.transaction() as connection:
            connection.execute(
                "UPDATE passkeys SET sign_count=?,last_used_at=? WHERE credential_id=?",
                (
                    max(int(verification.new_sign_count), int(passkey["sign_count"])),
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    credential_id,
                ),
            )
        existing = service.list_passkeys(body.school_id)
        registration = generate_registration_options(
            rp_id=_rp_id(),
            rp_name="School CSM Internet Gateway",
            user_id=body.school_id.encode("ascii"),
            user_name=body.school_id,
            user_display_name=f"School {body.school_id} Internet Server Administrator",
            exclude_credentials=[
                PublicKeyCredentialDescriptor(id=_decode_credential_id(row["credential_id"]))
                for row in existing
            ],
            authenticator_selection=AuthenticatorSelectionCriteria(
                resident_key=ResidentKeyRequirement.PREFERRED,
                user_verification=UserVerificationRequirement.REQUIRED,
            ),
            attestation=AttestationConveyancePreference.NONE,
        )
        registration_ceremony = service.repository.create_passkey_ceremony(
            purpose="add_registration",
            school_id=body.school_id,
            challenge=registration.challenge,
            context={"authorized_by": credential_id},
        )
        return {
            "ceremony_id": registration_ceremony,
            "public_key": json.loads(options_to_json(registration)),
        }

    @app.post("/v1/passkeys/add/complete")
    def complete_add_passkey(
        body: PasskeyAddComplete,
        service: RegistrationService = Depends(current_service),
    ):
        ceremony = service.repository.consume_passkey_ceremony(
            body.ceremony_id, "add_registration"
        )
        if ceremony["school_id"] != body.school_id:
            raise ServiceError("The passkey ceremony belongs to another school.")
        verification = verify_registration_response(
            credential=body.passkey_response,
            expected_challenge=ceremony["challenge"],
            expected_origin=_origin(),
            expected_rp_id=_rp_id(),
            require_user_verification=True,
        )
        service.add_passkey(
            school_id=body.school_id,
            credential_id=_b64url(verification.credential_id),
            credential_public_key=verification.credential_public_key,
            sign_count=verification.sign_count,
            label=body.passkey_label,
            authorized_by=str(ceremony["context"].get("authorized_by") or ""),
        )
        return {"ok": True}

    @app.post("/v1/passkeys/reset/begin")
    def begin_passkey_reset(
        body: PasskeyResetBegin,
        service: RegistrationService = Depends(current_service),
    ):
        reset_hash = service.repository.validate_passkey_reset_code(
            body.school_id, body.reset_code
        )
        options = generate_registration_options(
            rp_id=_rp_id(),
            rp_name="School CSM Internet Gateway",
            user_id=body.school_id.encode("ascii"),
            user_name=body.school_id,
            user_display_name=f"School {body.school_id} Recovered Administrator",
            authenticator_selection=AuthenticatorSelectionCriteria(
                resident_key=ResidentKeyRequirement.PREFERRED,
                user_verification=UserVerificationRequirement.REQUIRED,
            ),
            attestation=AttestationConveyancePreference.NONE,
        )
        ceremony_id = service.repository.create_passkey_ceremony(
            purpose="reset_registration",
            school_id=body.school_id,
            challenge=options.challenge,
            context={"reset_code_hash": reset_hash},
        )
        return {"ceremony_id": ceremony_id, "public_key": json.loads(options_to_json(options))}

    @app.post("/v1/passkeys/reset/complete")
    def complete_passkey_reset(
        body: PasskeyResetComplete,
        service: RegistrationService = Depends(current_service),
    ):
        ceremony = service.repository.consume_passkey_ceremony(
            body.ceremony_id, "reset_registration"
        )
        if ceremony["school_id"] != body.school_id:
            raise ServiceError("The passkey ceremony belongs to another school.")
        reset_hash = service.repository.validate_passkey_reset_code(
            body.school_id, body.reset_code
        )
        if reset_hash != ceremony["context"].get("reset_code_hash"):
            raise ServiceError(
                "The passkey recovery code does not match this ceremony.",
                code="passkey_reset_invalid",
            )
        verification = verify_registration_response(
            credential=body.passkey_response,
            expected_challenge=ceremony["challenge"],
            expected_origin=_origin(),
            expected_rp_id=_rp_id(),
            require_user_verification=True,
        )
        service.reset_passkeys(
            school_id=body.school_id,
            reset_code_hash=reset_hash,
            credential_id=_b64url(verification.credential_id),
            credential_public_key=verification.credential_public_key,
            sign_count=verification.sign_count,
            label=body.passkey_label,
        )
        return {"ok": True}

    @app.get("/v1/installations/{installation_id}/authorization")
    def authorization(
        installation_id: str,
        school_id: str,
        authorization: str | None = Header(default=None),
        service: RegistrationService = Depends(current_service),
    ):
        secret = _bearer(authorization)
        envelope = service.signed_authorization(installation_id, secret)
        if str(envelope.get("payload", {}).get("school_id")) != school_id:
            raise ServiceError("Installation authorization failed.", code="authorization_failed")
        return envelope

    @app.post("/v1/installations/{installation_id}/tunnel-credential")
    def tunnel_credential(
        installation_id: str,
        body: TunnelCredentialRequest,
        authorization: str | None = Header(default=None),
        service: RegistrationService = Depends(current_service),
    ):
        row = service.authenticate_installation(installation_id, _bearer(authorization))
        if row["state"] != "active" or row["school_id"] != body.school_id:
            raise ServiceError("This installation is not the active school server.", code="authorization_failed")
        provisioned = service.provisioner.credential(
            tunnel_id=str(row["tunnel_id"]),
            public_host=str(row["public_host"]),
            origin_port=body.origin_port,
        )
        return {
            "tunnel_token": str(provisioned.get("tunnel_token") or ""),
            "tunnel_id": str(row["tunnel_id"]),
        }

    return app


def _registered_passkey(
    service: RegistrationService,
    school_id: str,
    response: Mapping[str, Any],
) -> tuple[str, Mapping[str, Any]]:
    raw_id = str(response.get("rawId") or response.get("id") or "")
    credential_id = _canonical_credential_id(raw_id)
    with service.repository.read() as connection:
        passkey = connection.execute(
            "SELECT * FROM passkeys WHERE school_id=? AND credential_id=?",
            (school_id, credential_id),
        ).fetchone()
    if passkey is None:
        raise ServiceError("The registered passkey was not found.", code="passkey_not_found")
    return credential_id, dict(passkey)


def _bearer(header: str | None) -> str:
    prefix = "Bearer "
    if not header or not header.startswith(prefix):
        raise ServiceError("Installation authorization failed.", code="authorization_failed")
    return header[len(prefix):].strip()


def _canonical_credential_id(value: str) -> str:
    return _b64url(_decode_credential_id(value))


def _decode_credential_id(value: str) -> bytes:
    text = str(value).strip()
    padding = "=" * (-len(text) % 4)
    try:
        return base64.urlsafe_b64decode(text + padding)
    except Exception:
        raise ServiceError("The passkey credential identity is invalid.", code="passkey_invalid") from None


def _is_test_client(request: Request) -> bool:
    return bool(os.environ.get("SCHOOL_CSM_ALLOW_INSECURE_TEST_CLIENT") == "1" and request.client and request.client.host == "testclient")


def _json_error(status: int, detail: str):
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=status, content={"ok": False, "detail": detail})


app = create_app()
