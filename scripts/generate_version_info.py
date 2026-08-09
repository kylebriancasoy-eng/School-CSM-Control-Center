"""Generate PyInstaller's Windows version resource from the central version."""

from __future__ import annotations

import argparse
from pathlib import Path
import runpy


REPO_ROOT = Path(__file__).resolve().parents[1]
VERSION_DATA = runpy.run_path(str(REPO_ROOT / "school_csm_control_center" / "version.py"))
WINDOWS_FILE_VERSION = VERSION_DATA["WINDOWS_FILE_VERSION"]
WINDOWS_FILE_VERSION_TEXT = VERSION_DATA["WINDOWS_FILE_VERSION_TEXT"]
__version__ = VERSION_DATA["__version__"]


TEMPLATE = """VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={file_version!r},
    prodvers={file_version!r},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        u'040904B0',
        [
          StringStruct(u'CompanyName', u'MoSSLab'),
          StringStruct(u'FileDescription', u'School CSM Control Center'),
          StringStruct(u'FileVersion', u'{file_version_text}'),
          StringStruct(u'InternalName', u'School CSM Control Center'),
          StringStruct(u'LegalCopyright', u'Copyright MoSSLab'),
          StringStruct(u'OriginalFilename', u'School CSM Control Center.exe'),
          StringStruct(u'ProductName', u'School CSM Control Center'),
          StringStruct(u'ProductVersion', u'{product_version}')
        ]
      )
    ]),
    VarFileInfo([VarStruct(u'Translation', [1033, 1200])])
  ]
)
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=REPO_ROOT / "packaging" / "pyinstaller" / "version_info.txt",
    )
    args = parser.parse_args()
    content = TEMPLATE.format(
        file_version=WINDOWS_FILE_VERSION,
        file_version_text=WINDOWS_FILE_VERSION_TEXT,
        product_version=__version__,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if not args.output.exists() or args.output.read_text(encoding="utf-8") != content:
        args.output.write_text(content, encoding="utf-8", newline="\n")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
