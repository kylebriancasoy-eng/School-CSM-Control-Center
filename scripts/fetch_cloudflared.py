"""Fetch the pinned Cloudflare Tunnel binary and verify its official SHA-256."""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import tempfile
from urllib.request import Request, urlopen


VERSION = "2026.5.2"
DOWNLOAD_URL = (
    "https://github.com/cloudflare/cloudflared/releases/download/"
    f"{VERSION}/cloudflared-windows-amd64.exe"
)
SHA256 = "20b9638f685333d623798e733effbad2487093f15ba592f6c7752360ff3b7ab7"
MAX_BYTES = 100 * 1024 * 1024


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch(destination: Path, *, url: str = DOWNLOAD_URL, expected_sha256: str = SHA256) -> Path:
    destination = destination.expanduser().resolve()
    if destination.is_file() and sha256_file(destination).casefold() == expected_sha256.casefold():
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=destination.parent,
            prefix=".cloudflared-",
            suffix=".download",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            request = Request(url, headers={"User-Agent": "School-CSM-Control-Center-Build/1"})
            with urlopen(request, timeout=60) as response:
                declared = response.headers.get("Content-Length")
                if declared:
                    try:
                        if int(declared) > MAX_BYTES:
                            raise RuntimeError("The tunnel component exceeds the build size limit.")
                    except ValueError:
                        raise RuntimeError("The tunnel download declared an invalid size.") from None
                total = 0
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_BYTES:
                        raise RuntimeError("The tunnel component exceeds the build size limit.")
                    temporary.write(chunk)
            temporary.flush()
            os.fsync(temporary.fileno())
        actual = sha256_file(temporary_path)
        if actual.casefold() != expected_sha256.casefold():
            raise RuntimeError(
                "The downloaded tunnel component failed SHA-256 verification "
                f"(expected {expected_sha256}, received {actual})."
            )
        os.replace(temporary_path, destination)
        temporary_path = None
        return destination
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def main() -> int:
    repository = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--destination",
        type=Path,
        default=repository / "vendor" / "cloudflared" / "cloudflared.exe",
    )
    args = parser.parse_args()
    print(fetch(args.destination))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
