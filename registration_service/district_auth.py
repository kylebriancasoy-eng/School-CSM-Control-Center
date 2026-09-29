"""Bounded district-administrator browser sessions.

The district portal deliberately uses a separate authentication boundary from
school passkeys and installation credentials.  A high-entropy access token is
kept by the deployment owner; only its SHA-256 digest is supplied to the
service.  Successful login exchanges that token for an opaque, short-lived,
HttpOnly browser cookie plus a per-session CSRF token.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import os
import secrets
from threading import RLock


SESSION_COOKIE = "__Host-school_csm_district_session"
CSRF_HEADER = "X-CSM-CSRF-Token"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class DistrictAdminSession:
    session_id: str
    csrf_token: str
    created_at: datetime
    expires_at: datetime


class DistrictAdminSessions:
    """Single-process session store for the reference single-worker service."""

    def __init__(
        self,
        token_sha256: str = "",
        *,
        lifetime: timedelta = timedelta(hours=8),
        maximum_sessions: int = 128,
    ) -> None:
        digest = str(token_sha256 or "").strip().casefold()
        if digest and (
            len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise ValueError(
                "SCHOOL_CSM_DISTRICT_ADMIN_TOKEN_SHA256 must be a SHA-256 hex digest."
            )
        self._token_digest = digest
        self._lifetime = min(max(lifetime, timedelta(minutes=15)), timedelta(hours=24))
        self._maximum_sessions = min(max(1, int(maximum_sessions)), 1024)
        self._sessions: OrderedDict[str, DistrictAdminSession] = OrderedDict()
        self._lock = RLock()

    @classmethod
    def from_environment(cls) -> "DistrictAdminSessions":
        return cls(os.environ.get("SCHOOL_CSM_DISTRICT_ADMIN_TOKEN_SHA256", ""))

    @property
    def configured(self) -> bool:
        return bool(self._token_digest)

    def login(self, access_token: str) -> DistrictAdminSession | None:
        candidate = str(access_token or "")
        if not self.configured or len(candidate) < 24 or len(candidate) > 512:
            return None
        digest = hashlib.sha256(candidate.encode("utf-8")).hexdigest()
        if not hmac.compare_digest(digest, self._token_digest):
            return None
        now = _utc_now()
        session = DistrictAdminSession(
            session_id=secrets.token_urlsafe(32),
            csrf_token=secrets.token_urlsafe(32),
            created_at=now,
            expires_at=now + self._lifetime,
        )
        with self._lock:
            self._prune(now)
            while len(self._sessions) >= self._maximum_sessions:
                self._sessions.popitem(last=False)
            self._sessions[session.session_id] = session
        return session

    def get(self, session_id: str) -> DistrictAdminSession | None:
        candidate = str(session_id or "")
        if not candidate:
            return None
        now = _utc_now()
        with self._lock:
            self._prune(now)
            session = self._sessions.get(candidate)
            if session is None:
                return None
            self._sessions.move_to_end(candidate)
            return session

    def csrf_matches(self, session: DistrictAdminSession, candidate: str) -> bool:
        value = str(candidate or "")
        return bool(value) and hmac.compare_digest(value, session.csrf_token)

    def logout(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(str(session_id or ""), None)

    def _prune(self, now: datetime) -> None:
        expired = [
            session_id
            for session_id, session in self._sessions.items()
            if session.expires_at <= now
        ]
        for session_id in expired:
            self._sessions.pop(session_id, None)


def generate_district_admin_token() -> tuple[str, str]:
    """Return a one-time access token and the digest stored by the service."""

    token = secrets.token_urlsafe(36)
    return token, hashlib.sha256(token.encode("utf-8")).hexdigest()
