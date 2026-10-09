"""Encrypted, matched recovery backups for the district management host."""

from __future__ import annotations

from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from typing import Mapping
import zipfile

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from .configuration import DistrictServerConfig, load_server_config
from .credentials import DistrictCredentialStore, DistrictSecretMaterial
from .paths import DistrictRuntimePaths


MAGIC = b"MSCSDRB1\n"
SALT_BYTES = 16
NONCE_BYTES = 12
MAXIMUM_BACKUP_BYTES = 128 * 1024 * 1024
MAXIMUM_ENTRY_BYTES = 96 * 1024 * 1024
ARCHIVE_ENTRIES = frozenset(
    {
        "manifest.json",
        "configuration/server.json",
        "data/registration.sqlite3",
        "provider/internet_gateway_provider.json",
        "secrets/credentials.json",
    }
)


class DistrictRecoveryError(RuntimeError):
    pass


def create_recovery_backup(
    paths: DistrictRuntimePaths,
    destination: str | Path,
    password: str,
    *,
    credential_store: DistrictCredentialStore | None = None,
) -> Path:
    """Create an encrypted configuration/database/credential recovery set."""

    target = Path(destination).expanduser().resolve()
    if target.suffix.casefold() != ".mossdistrict":
        target = target.with_suffix(".mossdistrict")
    _password_bytes(password)
    config = load_server_config(paths.configuration_file)
    if not paths.database_file.is_file():
        raise DistrictRecoveryError("The district registration database is missing.")
    if not paths.provider_export_file.is_file():
        raise DistrictRecoveryError("The school deployment profile is missing.")
    provider_bytes = _bounded_read(paths.provider_export_file, 1024 * 1024)
    try:
        provider_document = json.loads(provider_bytes.decode("utf-8"))
        _validate_provider_document(provider_document, config)
    except Exception as exc:
        raise DistrictRecoveryError("The school deployment profile is invalid.") from exc
    secrets = (credential_store or DistrictCredentialStore()).load()

    target.parent.mkdir(parents=True, exist_ok=True)
    paths.backups_dir.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="district-backup-", dir=str(paths.backups_dir)) as temporary:
        temporary_root = Path(temporary)
        database_copy = temporary_root / "registration.sqlite3"
        _backup_database(paths.database_file, database_copy)
        _verify_database(database_copy)
        manifest = {
            "schema_version": "1.0",
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "public_origin": config.public_origin,
            "managed_domain": config.managed_domain,
            "administrator_token_included": False,
        }
        secret_document = {
            "schema_version": "1.0",
            "authorization_private_key_base64": secrets.authorization_private_key_base64,
            "handoff_encryption_key_base64": secrets.handoff_encryption_key_base64,
            "district_admin_token_sha256": secrets.district_admin_token_sha256,
            "cloudflare_api_token": secrets.cloudflare_api_token,
            "management_tunnel_token": secrets.management_tunnel_token,
        }
        archive = io.BytesIO()
        with zipfile.ZipFile(
            archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
        ) as bundle:
            bundle.writestr(
                "manifest.json",
                json.dumps(manifest, sort_keys=True, separators=(",", ":")),
            )
            bundle.writestr(
                "configuration/server.json",
                _bounded_read(paths.configuration_file, 1024 * 1024),
            )
            bundle.write(database_copy, "data/registration.sqlite3")
            bundle.writestr(
                "provider/internet_gateway_provider.json", provider_bytes
            )
            bundle.writestr(
                "secrets/credentials.json",
                json.dumps(secret_document, sort_keys=True, separators=(",", ":")),
            )
        plaintext = archive.getvalue()
        if not plaintext or len(plaintext) > MAXIMUM_BACKUP_BYTES:
            raise DistrictRecoveryError("The district recovery backup is too large.")
        salt = os.urandom(SALT_BYTES)
        nonce = os.urandom(NONCE_BYTES)
        key = _derive_key(password, salt)
        encrypted = AESGCM(key).encrypt(nonce, plaintext, MAGIC + salt)
        payload = MAGIC + salt + nonce + encrypted
        temporary_target = target.with_name(f".{target.name}.{os.getpid()}.tmp")
        try:
            temporary_target.write_bytes(payload)
            os.replace(temporary_target, target)
            try:
                os.chmod(target, 0o600)
            except OSError:
                pass
        finally:
            temporary_target.unlink(missing_ok=True)
    return target


def restore_recovery_backup(
    paths: DistrictRuntimePaths,
    source: str | Path,
    password: str,
    *,
    credential_store: DistrictCredentialStore | None = None,
) -> None:
    """Restore a matched backup only onto an empty district runtime root."""

    store = credential_store or DistrictCredentialStore()
    protected_paths = (
        paths.configuration_file,
        paths.database_file,
        paths.database_file.with_name(paths.database_file.name + "-wal"),
        paths.database_file.with_name(paths.database_file.name + "-shm"),
        paths.provider_export_file,
    )
    if any(path.exists() for path in protected_paths) or store.has_any():
        raise DistrictRecoveryError(
            "Recovery requires an empty district host and will not overwrite existing state."
        )
    document = _decrypt_backup(Path(source), password)
    entries = _read_archive(document)
    paths.ensure_directories()
    with TemporaryDirectory(prefix="district-restore-", dir=str(paths.root)) as temporary:
        stage = Path(temporary)
        config_stage = stage / "server.json"
        database_stage = stage / "registration.sqlite3"
        provider_stage = stage / "internet_gateway_provider.json"
        config_stage.write_bytes(entries["configuration/server.json"])
        database_stage.write_bytes(entries["data/registration.sqlite3"])
        provider_stage.write_bytes(entries["provider/internet_gateway_provider.json"])
        config = load_server_config(config_stage)
        _verify_database(database_stage)
        try:
            provider = json.loads(provider_stage.read_text(encoding="utf-8"))
            _validate_provider_document(provider, config)
            secret_document = json.loads(
                entries["secrets/credentials.json"].decode("utf-8")
            )
            material = _secret_material(secret_document)
        except Exception as exc:
            raise DistrictRecoveryError(
                "The district recovery backup contains invalid protected data."
            ) from exc

        restored_files: list[Path] = []
        credentials_restored = False
        try:
            store.restore_initial(material)
            credentials_restored = True
            for staged, target in (
                (config_stage, paths.configuration_file),
                (database_stage, paths.database_file),
                (provider_stage, paths.provider_export_file),
            ):
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(staged, target)
                restored_files.append(target)
        except Exception:
            for target in reversed(restored_files):
                target.unlink(missing_ok=True)
            if credentials_restored:
                try:
                    store.remove_all()
                except Exception:
                    pass
            raise


def _decrypt_backup(path: Path, password: str) -> bytes:
    payload = _bounded_read(path.expanduser().resolve(), MAXIMUM_BACKUP_BYTES + 1024)
    minimum = len(MAGIC) + SALT_BYTES + NONCE_BYTES + 16
    if len(payload) < minimum or not payload.startswith(MAGIC):
        raise DistrictRecoveryError("This is not a supported district recovery backup.")
    offset = len(MAGIC)
    salt = payload[offset : offset + SALT_BYTES]
    offset += SALT_BYTES
    nonce = payload[offset : offset + NONCE_BYTES]
    encrypted = payload[offset + NONCE_BYTES :]
    key = _derive_key(password, salt)
    try:
        plaintext = AESGCM(key).decrypt(nonce, encrypted, MAGIC + salt)
    except InvalidTag:
        raise DistrictRecoveryError(
            "The recovery password is incorrect or the backup was changed."
        ) from None
    if not plaintext or len(plaintext) > MAXIMUM_BACKUP_BYTES:
        raise DistrictRecoveryError("The decrypted district recovery backup is invalid.")
    return plaintext


def _read_archive(payload: bytes) -> dict[str, bytes]:
    try:
        with zipfile.ZipFile(io.BytesIO(payload), "r") as bundle:
            names = bundle.namelist()
            if len(names) != len(set(names)) or set(names) != ARCHIVE_ENTRIES:
                raise DistrictRecoveryError(
                    "The district recovery backup has an unexpected file layout."
                )
            entries: dict[str, bytes] = {}
            total = 0
            for info in bundle.infolist():
                if info.is_dir() or info.file_size <= 0 or info.file_size > MAXIMUM_ENTRY_BYTES:
                    raise DistrictRecoveryError(
                        "The district recovery backup contains an invalid entry."
                    )
                total += int(info.file_size)
                if total > MAXIMUM_BACKUP_BYTES:
                    raise DistrictRecoveryError(
                        "The expanded district recovery backup is too large."
                    )
                entries[info.filename] = bundle.read(info)
    except DistrictRecoveryError:
        raise
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        raise DistrictRecoveryError("The district recovery archive is invalid.") from exc
    try:
        manifest = json.loads(entries["manifest.json"].decode("utf-8"))
    except Exception as exc:
        raise DistrictRecoveryError("The district recovery manifest is invalid.") from exc
    if not isinstance(manifest, Mapping) or manifest.get("schema_version") != "1.0":
        raise DistrictRecoveryError("The district recovery manifest is unsupported.")
    return entries


def _secret_material(document: object) -> DistrictSecretMaterial:
    if not isinstance(document, Mapping) or set(document) != {
        "schema_version",
        "authorization_private_key_base64",
        "handoff_encryption_key_base64",
        "district_admin_token_sha256",
        "cloudflare_api_token",
        "management_tunnel_token",
    }:
        raise DistrictRecoveryError("The district recovery secret set is invalid.")
    if document.get("schema_version") != "1.0":
        raise DistrictRecoveryError("The district recovery secret set is unsupported.")
    return DistrictSecretMaterial(
        str(document["authorization_private_key_base64"]),
        str(document["handoff_encryption_key_base64"]),
        str(document["district_admin_token_sha256"]),
        str(document["cloudflare_api_token"]),
        str(document["management_tunnel_token"]),
    )


def _validate_provider_document(
    document: object, config: DistrictServerConfig
) -> None:
    expected_fields = {
        "schema_version",
        "provider_name",
        "registration_service_url",
        "managed_domain",
        "authorization_signing_keys",
        "trusted_proxy_addresses",
    }
    if not isinstance(document, Mapping) or set(document) != expected_fields:
        raise DistrictRecoveryError("The school deployment profile is invalid.")
    if document.get("schema_version") != "1.0":
        raise DistrictRecoveryError("The school deployment profile is unsupported.")
    if str(document.get("registration_service_url") or "").rstrip("/") != config.public_origin:
        raise DistrictRecoveryError("The deployment profile origin does not match the server.")
    if str(document.get("managed_domain") or "").casefold().rstrip(".") != config.managed_domain:
        raise DistrictRecoveryError("The deployment profile domain does not match the server.")
    key_rows = document.get("authorization_signing_keys")
    expected_key = {
        "key_id": config.authorization_key_id,
        "public_key_base64": config.authorization_public_key_base64,
    }
    if key_rows != [expected_key]:
        raise DistrictRecoveryError("The deployment profile signing key does not match the server.")
    proxies = document.get("trusted_proxy_addresses")
    if not isinstance(proxies, list) or set(proxies) != {"127.0.0.1", "::1"}:
        raise DistrictRecoveryError("The deployment profile proxy boundary is invalid.")
    provider_name = str(document.get("provider_name") or "").strip()
    if not provider_name or len(provider_name) > 120:
        raise DistrictRecoveryError("The deployment profile name is invalid.")


def _backup_database(source: Path, destination: Path) -> None:
    input_database = None
    output_database = None
    try:
        input_database = sqlite3.connect(str(source), timeout=15)
        output_database = sqlite3.connect(str(destination))
        input_database.backup(output_database)
        output_database.commit()
    except sqlite3.Error as exc:
        raise DistrictRecoveryError(
            "The district registration database could not be backed up safely."
        ) from exc
    finally:
        if output_database is not None:
            output_database.close()
        if input_database is not None:
            input_database.close()


def _verify_database(path: Path) -> None:
    connection = None
    try:
        connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        row = connection.execute("PRAGMA quick_check").fetchone()
    except sqlite3.Error as exc:
        raise DistrictRecoveryError("The district recovery database is invalid.") from exc
    finally:
        if connection is not None:
            connection.close()
    if row is None or str(row[0]).casefold() != "ok":
        raise DistrictRecoveryError("The district recovery database failed validation.")


def _derive_key(password: str, salt: bytes) -> bytes:
    return Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(
        _password_bytes(password)
    )


def _password_bytes(password: str) -> bytes:
    value = str(password or "")
    encoded = value.encode("utf-8")
    if len(value) < 12 or len(encoded) > 1024 or "\0" in value:
        raise DistrictRecoveryError(
            "Use a recovery password of at least 12 characters."
        )
    return encoded


def _bounded_read(path: Path, limit: int) -> bytes:
    try:
        size = path.stat().st_size
        if size <= 0 or size > limit:
            raise DistrictRecoveryError("A district recovery input has an invalid size.")
        return path.read_bytes()
    except DistrictRecoveryError:
        raise
    except OSError as exc:
        raise DistrictRecoveryError("A district recovery input could not be read.") from exc
