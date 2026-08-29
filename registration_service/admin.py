"""Offline administrator commands for the registration-service operator."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from .core import RegistrationRepository, RegistrationService


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        type=Path,
        default=Path(
            os.environ.get("SCHOOL_CSM_REGISTRATION_DATABASE", "registration.sqlite3")
        ),
    )
    subcommands = parser.add_subparsers(dest="command", required=True)
    issue = subcommands.add_parser("issue-activation", help="Issue a one-time school code")
    issue.add_argument("--school-id", required=True)
    reset = subcommands.add_parser(
        "issue-passkey-reset", help="Issue a short-lived lost-passkey recovery code"
    )
    reset.add_argument("--school-id", required=True)
    listing = subcommands.add_parser(
        "list-passkeys", help="List registered passkey IDs and labels"
    )
    listing.add_argument("--school-id", required=True)
    revoke = subcommands.add_parser(
        "revoke-passkey", help="Revoke one passkey while preserving at least one"
    )
    revoke.add_argument("--school-id", required=True)
    revoke.add_argument("--credential-id", required=True)
    revoke.add_argument("--confirm", action="store_true", required=True)
    arguments = parser.parse_args(argv)
    repository = RegistrationRepository(arguments.database)
    if arguments.command == "issue-activation":
        code = repository.issue_activation_code(arguments.school_id)
        print(code)
        return 0
    if arguments.command == "issue-passkey-reset":
        print(repository.issue_passkey_reset_code(arguments.school_id))
        return 0
    service = RegistrationService(repository, managed_domain="administration.invalid")
    if arguments.command == "list-passkeys":
        for row in service.list_passkeys(arguments.school_id):
            print(
                f"{row['credential_id']}\t{row['label']}\t"
                f"created={row['created_at']}\tlast_used={row['last_used_at'] or '-'}"
            )
        return 0
    if arguments.command == "revoke-passkey":
        service.revoke_passkey(
            school_id=arguments.school_id,
            credential_id=arguments.credential_id,
        )
        print("Passkey revoked.")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
