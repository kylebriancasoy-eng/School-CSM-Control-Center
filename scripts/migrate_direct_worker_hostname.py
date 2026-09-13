"""Change only the local direct Worker hostname after a workers.dev rename."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from school_csm_control_center.services.direct_worker_hostname_migration import (  # noqa: E402
    DirectWorkerHostnameMigration,
    DirectWorkerHostnameMigrationError,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Safely change only the saved workers.dev hostname for an existing "
            "direct School CSM Internet Gateway."
        )
    )
    parser.add_argument(
        "--school-id",
        required=True,
        help="Official School ID expected in the current registration and hostname.",
    )
    parser.add_argument(
        "--expected-old-host",
        required=True,
        help="Exact workers.dev hostname currently saved by the application.",
    )
    parser.add_argument(
        "--new-host",
        required=True,
        help="New workers.dev hostname, beginning with the same School ID.",
    )
    parser.add_argument(
        "--data-root",
        type=Path,
        default=None,
        help=(
            "Mutable School CSM data root. Omit to use the normal Documents\\MoSSLab "
            "Data\\School CSM Control Center location."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate every precondition without writing state or creating a backup.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    try:
        service = DirectWorkerHostnameMigration(
            REPOSITORY_ROOT,
            data_root=arguments.data_root,
        )
        result = service.migrate(
            expected_school_id=arguments.school_id,
            expected_old_hostname=arguments.expected_old_host,
            new_hostname=arguments.new_host,
            dry_run=arguments.dry_run,
        )
    except (DirectWorkerHostnameMigrationError, OSError) as exc:
        parser.exit(1, f"Hostname migration stopped safely: {exc}\n")

    if not result.changed:
        print(
            "Validation passed. Gateway state and backups were not changed, and no credentials were accessed."
        )
        return 0
    print(f"Gateway hostname updated to {result.new_hostname}.")
    print("The existing connector credential was not accessed or changed.")
    print(f"Verified timestamped backup: {result.backup_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
