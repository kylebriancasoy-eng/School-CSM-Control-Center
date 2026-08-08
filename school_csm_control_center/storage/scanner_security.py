"""Scanner Operator accounts with salted password hashing and lockout controls."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import base64
import hashlib
import hmac
import json
from pathlib import Path
import re
import secrets
from typing import Any, Mapping

from school_csm_control_center.runtime_paths import storage_root_for
from school_csm_control_center.storage.file_safety import (
    InterProcessRLock,
    atomic_write_json,
    preserve_corrupt_file,
)

USERNAME_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{1,30}[a-z0-9])?$")
PBKDF2_ITERATIONS = 240_000


class ScannerAuthenticationError(PermissionError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


class ScannerOperatorStore:
    FILE_TYPE = "School CSM Scanner Operators"
    SCHEMA_VERSION = "1.0"

    def __init__(
        self,
        project_root: str | Path,
        *,
        data_root: str | Path | None = None,
    ) -> None:
        self.install_root = Path(project_root)
        self.project_root = storage_root_for(project_root, data_root)
        self.path = self.project_root / "data" / "csm_survey" / "scanner_operators.json"
        self._lock = InterProcessRLock(self.path)
        self.last_read_error: str | None = None
        self.last_recovery_copy: Path | None = None

    def list_accounts(self) -> list[dict[str, Any]]:
        with self._lock:
            accounts = [self._public(account) for account in self._read()["accounts"]]
        accounts.sort(key=lambda item: (str(item.get("display_name") or "").casefold(), str(item.get("username") or "")))
        return accounts

    def get(self, user_id: str) -> dict[str, Any] | None:
        wanted = str(user_id or "").strip()
        if not wanted:
            return None
        with self._lock:
            for account in self._read()["accounts"]:
                if str(account.get("user_id") or "") == wanted:
                    return self._public(account)
        return None

    def save_account(
        self,
        *,
        user_id: str = "",
        username: str,
        display_name: str,
        password: str = "",
        enabled: bool = True,
    ) -> dict[str, Any]:
        clean_username = self._validate_username(username)
        clean_name = " ".join(str(display_name or "").split())
        if not clean_name:
            raise ValueError("Scanner Operator display name is required.")

        with self._lock:
            document = self._read()
            accounts = document["accounts"]
            wanted = str(user_id or "").strip()
            existing_index = next(
                (index for index, account in enumerate(accounts) if str(account.get("user_id") or "") == wanted),
                None,
            ) if wanted else None

            for account in accounts:
                if existing_index is not None and account is accounts[existing_index]:
                    continue
                if str(account.get("username") or "").casefold() == clean_username:
                    raise ValueError("That Scanner Operator username is already registered.")

            now = _utc_now()
            if existing_index is None:
                if len(str(password or "")) < 6:
                    raise ValueError("New Scanner Operator passwords must contain at least 6 characters.")
                salt, password_hash = _hash_password(password)
                account = {
                    "user_id": self._next_user_id(accounts),
                    "username": clean_username,
                    "display_name": clean_name,
                    "enabled": bool(enabled),
                    "password_salt": salt,
                    "password_hash": password_hash,
                    "password_iterations": PBKDF2_ITERATIONS,
                    "created_at": now,
                    "updated_at": now,
                    "last_successful_login": "",
                    "last_failed_login": "",
                    "failed_login_count": 0,
                    "lockout_until": "",
                    "archived": False,
                }
                accounts.append(account)
            else:
                account = accounts[existing_index]
                account["username"] = clean_username
                account["display_name"] = clean_name
                account["enabled"] = bool(enabled)
                account["updated_at"] = now
                if password:
                    if len(password) < 6:
                        raise ValueError("Scanner Operator passwords must contain at least 6 characters.")
                    salt, password_hash = _hash_password(password)
                    account["password_salt"] = salt
                    account["password_hash"] = password_hash
                    account["password_iterations"] = PBKDF2_ITERATIONS
                    account["failed_login_count"] = 0
                    account["lockout_until"] = ""

            self._write(document)
            return self._public(account)

    def archive(self, user_id: str) -> dict[str, Any]:
        wanted = str(user_id or "").strip()
        with self._lock:
            document = self._read()
            account = next((item for item in document["accounts"] if str(item.get("user_id") or "") == wanted), None)
            if account is None:
                raise KeyError("Scanner Operator account was not found.")
            account["enabled"] = False
            account["archived"] = True
            account["updated_at"] = _utc_now()
            self._write(document)
            return self._public(account)

    def authenticate(
        self,
        username: str,
        password: str,
        *,
        failed_login_limit: int = 5,
        lockout_seconds: int = 900,
    ) -> dict[str, Any]:
        clean_username = str(username or "").strip().casefold()
        supplied_password = str(password or "")
        limit = max(1, min(20, int(failed_login_limit or 5)))
        lock_seconds = max(60, min(86_400, int(lockout_seconds or 900)))

        with self._lock:
            document = self._read()
            account = next(
                (
                    item
                    for item in document["accounts"]
                    if not bool(item.get("archived"))
                    and str(item.get("username") or "").casefold() == clean_username
                ),
                None,
            )
            if account is None:
                # Execute a comparable hash to reduce username-enumeration timing differences.
                _verify_password(supplied_password, base64.b64encode(b"scanner-operator").decode(), base64.b64encode(b"0" * 32).decode(), PBKDF2_ITERATIONS)
                raise ScannerAuthenticationError("Invalid Scanner Operator username or password.", code="invalid_credentials")

            if not bool(account.get("enabled", True)):
                raise ScannerAuthenticationError("This Scanner Operator account is disabled.", code="account_disabled")

            lockout_until = _parse_datetime(account.get("lockout_until"))
            now = datetime.now(timezone.utc)
            if lockout_until is not None and lockout_until > now:
                remaining = max(1, int((lockout_until - now).total_seconds()))
                raise ScannerAuthenticationError(
                    f"This Scanner Operator account is temporarily locked. Try again in {remaining} seconds.",
                    code="account_locked",
                )

            ok = _verify_password(
                supplied_password,
                str(account.get("password_salt") or ""),
                str(account.get("password_hash") or ""),
                int(account.get("password_iterations") or PBKDF2_ITERATIONS),
            )
            if not ok:
                failed = int(account.get("failed_login_count") or 0) + 1
                account["failed_login_count"] = failed
                account["last_failed_login"] = _utc_now()
                if failed >= limit:
                    account["lockout_until"] = (now + timedelta(seconds=lock_seconds)).isoformat()
                    account["failed_login_count"] = 0
                account["updated_at"] = _utc_now()
                self._write(document)
                raise ScannerAuthenticationError("Invalid Scanner Operator username or password.", code="invalid_credentials")

            account["failed_login_count"] = 0
            account["lockout_until"] = ""
            account["last_successful_login"] = _utc_now()
            account["updated_at"] = _utc_now()
            self._write(document)
            return self._public(account)

    @staticmethod
    def _validate_username(value: str) -> str:
        username = str(value or "").strip().casefold()
        if not USERNAME_PATTERN.fullmatch(username):
            raise ValueError(
                "Scanner Operator usernames must contain 3 to 32 lowercase letters, numbers, dots, underscores, or hyphens."
            )
        return username

    @staticmethod
    def _next_user_id(accounts: list[dict[str, Any]]) -> str:
        used: list[int] = []
        pattern = re.compile(r"^SCOP-(\d{4})$")
        for account in accounts:
            match = pattern.fullmatch(str(account.get("user_id") or ""))
            if match:
                used.append(int(match.group(1)))
        return f"SCOP-{max(used, default=0) + 1:04d}"

    @staticmethod
    def _public(account: Mapping[str, Any]) -> dict[str, Any]:
        return {
            key: deepcopy(value)
            for key, value in account.items()
            if key not in {"password_salt", "password_hash", "password_iterations"}
        }

    def _blank(self) -> dict[str, Any]:
        return {
            "file_type": self.FILE_TYPE,
            "schema_version": self.SCHEMA_VERSION,
            "updated_at": _utc_now(),
            "accounts": [],
        }

    def _read(self) -> dict[str, Any]:
        self.last_read_error = None
        if not self.path.is_file():
            return self._blank()
        try:
            parsed = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._set_read_error(str(exc))
            return self._blank()
        if not isinstance(parsed, Mapping):
            self._set_read_error("The Scanner Operator file root is not an object.")
            return self._blank()
        accounts = parsed.get("accounts")
        if not isinstance(accounts, list):
            self._set_read_error("The Scanner Operator accounts value is not a list.")
            return self._blank()
        document = self._blank()
        document.update({str(key): deepcopy(value) for key, value in parsed.items() if key != "accounts"})
        document["accounts"] = [deepcopy(item) for item in accounts if isinstance(item, Mapping)]
        return document

    def _write(self, document: dict[str, Any]) -> None:
        self._raise_if_corrupt()
        document = deepcopy(document)
        document["file_type"] = self.FILE_TYPE
        document["schema_version"] = self.SCHEMA_VERSION
        document["updated_at"] = _utc_now()
        try:
            atomic_write_json(self.path, document)
        except (OSError, TypeError, ValueError) as exc:
            raise RuntimeError(
                f"Unable to save Scanner Operator accounts to {self.path}: {exc}"
            ) from exc

    def _raise_if_corrupt(self) -> None:
        if self.path.exists() and self.last_read_error:
            raise RuntimeError(
                "The existing Scanner Operator file could not be read and was left unchanged. "
                "A recovery copy was preserved."
            )

    def _set_read_error(self, message: str) -> None:
        self.last_read_error = str(message)
        self.last_recovery_copy = preserve_corrupt_file(self.path)


def _hash_password(password: str) -> tuple[str, str]:
    salt_bytes = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", str(password).encode("utf-8"), salt_bytes, PBKDF2_ITERATIONS)
    return base64.b64encode(salt_bytes).decode("ascii"), base64.b64encode(digest).decode("ascii")


def _verify_password(password: str, salt: str, expected_hash: str, iterations: int) -> bool:
    try:
        salt_bytes = base64.b64decode(salt, validate=True)
        expected = base64.b64decode(expected_hash, validate=True)
    except Exception:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", str(password).encode("utf-8"), salt_bytes, max(100_000, int(iterations or PBKDF2_ITERATIONS)))
    return hmac.compare_digest(digest, expected)


def _parse_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
