# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller one-folder build for the operator-facing Windows executable."""

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules, copy_metadata


repo_root = Path(SPECPATH).resolve().parents[1]
entry_point = repo_root / "run_school_csm_control_center.py"
icon_path = repo_root / "School CSM Control Center Icon.ico"
version_info = repo_root / "packaging" / "pyinstaller" / "version_info.txt"


def data_tree(source: Path, destination: str):
    rows = []
    for path in sorted(source.rglob("*")):
        if (
            not path.is_file()
            or "__pycache__" in path.parts
            or path.suffix in {".py", ".pyc"}
        ):
            continue
        relative_parent = path.relative_to(source).parent
        target = Path(destination) / relative_parent
        rows.append((str(path), str(target)))
    return rows


datas = data_tree(repo_root / "assets", "assets")
datas += data_tree(
    repo_root / "school_csm_control_center" / "web_server" / "static",
    "school_csm_control_center/web_server/static",
)
datas += data_tree(
    repo_root / "school_csm_control_center" / "mosslab_ui",
    "school_csm_control_center/mosslab_ui",
)
datas += data_tree(
    repo_root / "school_csm_control_center" / "mosslab_splash",
    "school_csm_control_center/mosslab_splash",
)
datas += [
    (str(repo_root / "School CSM Control Center Icon.ico"), "."),
    (str(repo_root / "School CSM Control Center Icon.png"), "."),
]

# The frozen launcher uses importlib.metadata for its startup diagnostics, and
# the OpenAI SDK also reads its installed distribution metadata.  PyInstaller
# does not include dist-info automatically for every imported package.
METADATA_DISTRIBUTIONS = (
    "PySide6",
    "qrcode",
    "Pillow",
    "numpy",
    "opencv-contrib-python-headless",
    "openai",
    "cryptography",
)
for distribution in METADATA_DISTRIBUTIONS:
    datas += copy_metadata(distribution)

cloudflared = repo_root / "vendor" / "cloudflared" / "cloudflared.exe"
if not cloudflared.is_file():
    raise FileNotFoundError(
        "The pinned Internet Gateway component is missing. "
        "Run scripts/fetch_cloudflared.py before building."
    )
binaries = [(str(cloudflared), "vendor/cloudflared")]


analysis = Analysis(
    [str(entry_point)],
    pathex=[str(repo_root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=[
        "PySide6.QtPrintSupport",
        "PIL.Image",
        "cv2",
        "numpy",
        "qrcode",
        "qrcode.image.pil",
    ] + collect_submodules("openai"),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "test", "unittest.mock"],
    noarchive=False,
    optimize=1,
)

pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="School CSM Control Center",
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
    name="School CSM Control Center",
)
