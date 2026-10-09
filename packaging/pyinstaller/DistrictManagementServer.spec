# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller one-folder build for the separate district host."""

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules, copy_metadata


repo_root = Path(SPECPATH).resolve().parents[1]
entry_point = repo_root / "run_district_management_server.py"
icon_path = repo_root / "School CSM Control Center Icon.ico"
version_info = repo_root / "packaging" / "pyinstaller" / "district_version_info.txt"


def files(source: Path, destination: str):
    rows = []
    for path in sorted(source.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
            relative_parent = path.relative_to(source).parent
            rows.append((str(path), str(Path(destination) / relative_parent)))
    return rows


datas = files(repo_root / "registration_service" / "static", "registration_service/static")
datas += [
    (str(repo_root / "cloudflare_worker" / "src" / "index.js"), "cloudflare_worker/src"),
    (
        str(repo_root / "district_gateway_worker" / "src" / "index.js"),
        "district_gateway_worker/src",
    ),
    (str(icon_path), "."),
    (str(repo_root / "School CSM Control Center Icon.png"), "."),
]

for distribution in ("PySide6", "fastapi", "uvicorn", "webauthn", "cryptography"):
    datas += copy_metadata(distribution)

cloudflared = repo_root / "vendor" / "cloudflared" / "cloudflared.exe"
if not cloudflared.is_file():
    raise FileNotFoundError(
        "The pinned Internet Gateway component is missing. Run scripts/fetch_cloudflared.py first."
    )

analysis = Analysis(
    [str(entry_point)],
    pathex=[str(repo_root)],
    binaries=[(str(cloudflared), "vendor/cloudflared")],
    datas=datas,
    hiddenimports=(
        collect_submodules("fastapi")
        + collect_submodules("uvicorn")
        + collect_submodules("webauthn")
        + [
            "registration_service.app",
            "uvicorn.logging",
            "uvicorn.loops.auto",
            "uvicorn.protocols.http.auto",
            "uvicorn.protocols.websockets.auto",
            "uvicorn.lifespan.on",
        ]
    ),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "test", "unittest.mock"],
    noarchive=False,
    optimize=1,
)

# Qt on Windows imports the operating system's unversioned ICU API. Build
# hosts can have Poppler/Conda on PATH with a different icuuc.dll whose exports
# are versioned. Bundling that DLL shadows Windows ICU and breaks Qt startup.
# This executable has no other ICU consumer; keep the OS library resolution.
analysis.binaries = [
    row for row in analysis.binaries
    if Path(row[0]).name.casefold() != "icuuc.dll"
    and not Path(row[0]).name.casefold().startswith("icudt")
]

pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="School CSM District Management Server",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(icon_path),
    version=str(version_info),
    uac_admin=False,
    contents_directory=".",
)

collection = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="School CSM District Management Server",
)
