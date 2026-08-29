"""Ed25519 authorization-envelope signing for the registration service."""

from __future__ import annotations

import base64
import json
import os
from typing import Any, Mapping


class AuthorizationEnvelopeSigner:
    def __init__(self, private_key: Any, *, key_id: str) -> None:
        self._private_key = private_key
        self.key_id = str(key_id or "").strip()
        if not self.key_id:
            raise ValueError("A signing key identifier is required.")

    @classmethod
    def from_environment(cls) -> "AuthorizationEnvelopeSigner":
        encoded = os.environ.get("SCHOOL_CSM_AUTHORIZATION_PRIVATE_KEY_BASE64", "").strip()
        key_id = os.environ.get("SCHOOL_CSM_AUTHORIZATION_KEY_ID", "").strip()
        if not encoded or not key_id:
            raise RuntimeError("Authorization signing environment variables are not configured.")
        try:
            material = base64.b64decode(encoded, validate=True)
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

            private_key = Ed25519PrivateKey.from_private_bytes(material)
        except Exception:
            raise RuntimeError("The configured authorization signing key is invalid.") from None
        return cls(private_key, key_id=key_id)

    def sign(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        document = dict(payload)
        canonical = json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        signature = self._private_key.sign(canonical)
        return {
            "schema_version": "1.0",
            "algorithm": "Ed25519",
            "key_id": self.key_id,
            "payload": document,
            "signature": base64.b64encode(signature).decode("ascii"),
        }
