"""Read-only release checks and explicit, local Setup launch."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
from urllib.request import Request, build_opener, HTTPRedirectHandler

REPOSITORY = "kylebriancasoy-eng/School-CSM-Control-Center"
SCHOOL_SETUP = "School-CSM-Control-Center-Setup.exe"
DISTRICT_SETUP = "School-CSM-District-Management-Server-Setup.exe"
MAX_METADATA = 256 * 1024


def version_tuple(value: str) -> tuple[int, int, int]:
    if not re.fullmatch(r"\d{1,5}\.\d{1,5}\.\d{1,5}", value):
        raise ValueError("Invalid release version")
    return tuple(int(part) for part in value.split("."))


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    title: str
    notes: str

    def newer_than(self, installed: str) -> bool:
        return version_tuple(self.version) > version_tuple(installed)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Unexpected update metadata redirect")


def parse_release(data: bytes, *, district: bool = False) -> ReleaseInfo:
    if not data or len(data) > MAX_METADATA:
        raise ValueError("Invalid update metadata size")
    document = json.loads(data)
    if not isinstance(document, dict) or document.get("draft") is not False or document.get("prerelease") is not False:
        raise ValueError("Only published stable releases are supported")
    tag = str(document.get("tag_name") or "")
    if not tag.startswith("v"):
        raise ValueError("Invalid release tag")
    version_tuple(tag[1:])
    setup = DISTRICT_SETUP if district else SCHOOL_SETUP
    manifest = "district-release.json" if district else "release.json"
    expected = f"https://github.com/{REPOSITORY}/releases/download/{tag}/"
    assets = document.get("assets")
    if not isinstance(assets, list) or len(assets) > 100:
        raise ValueError("Invalid release assets")
    for name in (setup, manifest):
        matches = [asset for asset in assets if isinstance(asset, dict) and asset.get("name") == name]
        if len(matches) != 1 or matches[0].get("browser_download_url") != expected + name:
            raise ValueError("The update does not contain this product's Setup and manifest")
    title = document.get("name")
    notes = document.get("body")
    if title is None or title == "":
        title = tag
    if notes is None or notes == "":
        notes = "No release notes were provided."
    if not isinstance(title, str) or not isinstance(notes, str):
        raise ValueError("Invalid release details")
    return ReleaseInfo(tag[1:], title[:200], notes[:16000])


def fetch_latest(*, district: bool = False) -> ReleaseInfo:
    url = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
    request = Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "MoSSLab-CSM-Update-Check"})
    with build_opener(_NoRedirect()).open(request, timeout=8) as response:
        if response.geturl() != url or response.status != 200:
            raise ValueError("Unexpected update response")
        return parse_release(response.read(MAX_METADATA + 1), district=district)


def stage_setup(install_root: Path, *, district: bool = False) -> Path:
    """Copy out before launch: Setup must not run inside files it replaces."""
    name = DISTRICT_SETUP if district else SCHOOL_SETUP
    root = Path(install_root).resolve()
    source = root / name
    if source.is_symlink() or not source.is_file() or source.resolve().parent != root:
        raise FileNotFoundError("Bundled Setup is missing. Use the official release installer to repair the application.")
    if source.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("Bundled Setup is unexpectedly large")
    with source.open("rb") as stream:
        data = stream.read(16 * 1024 * 1024 + 1)
    if not data.startswith(b"MZ") or len(data) > 16 * 1024 * 1024:
        raise ValueError("Bundled Setup is not a Windows executable")
    folder = Path(tempfile.mkdtemp(prefix="MoSSLab-CSM-Setup-"))
    destination = folder / name
    try:
        with destination.open("xb") as stream:
            stream.write(data)
        if hashlib.sha256(destination.read_bytes()).digest() != hashlib.sha256(data).digest():
            raise ValueError("Setup staging verification failed")
    except Exception:
        shutil.rmtree(folder)
        raise
    return destination


def launch_setup(install_root: Path, *, district: bool = False) -> None:
    if os.name != "nt" or not getattr(sys, "frozen", False):
        raise RuntimeError("Setup can be opened only from the compiled Windows application.")
    expected = "School CSM District Management Server.exe" if district else "School CSM Control Center.exe"
    if Path(sys.executable).name != expected or Path(sys.executable).resolve().parent != Path(install_root).resolve():
        raise ValueError("The application installation folder does not match this running program")
    staged = stage_setup(install_root, district=district)
    try:
        os.startfile(str(staged))
    except Exception:
        shutil.rmtree(staged.parent)
        raise
