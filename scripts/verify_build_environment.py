"""Fail-closed checks for the Python environment used to build the Windows app.

PyInstaller can successfully create an executable when a dependency is imported
only inside an optional-looking code path.  Several Internet Gateway operations
deliberately import ``cryptography`` only when they are used, so a successful
PyInstaller exit code is not sufficient evidence that the release is complete.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from importlib import import_module, metadata
from pathlib import Path
import re
import sys
from typing import Callable, Iterable, Sequence


MINIMUM_PYTHON = (3, 11)
MAXIMUM_PYTHON = (3, 15)
MINIMUM_PYINSTALLER = (6, 11)
MAXIMUM_PYINSTALLER = (7, 0)

_PIN_PATTERN = re.compile(
    r"^(?P<name>[A-Za-z0-9_.-]+)(?:\[(?P<extras>[A-Za-z0-9_,.-]+)\])?"
    r"==(?P<version>[^\s;]+)$"
)
_MISSING_MODULE_PATTERN = re.compile(
    r"missing module named (?P<quoted>['\"]?)(?P<name>[A-Za-z0-9_.]+)(?P=quoted) -"
)

# Import the actual modules exercised by the compiled desktop application, not
# just their distribution metadata.  This also catches broken native wheels.
RUNTIME_IMPORTS = (
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtPrintSupport",
    "PySide6.QtWidgets",
    "PIL.Image",
    "cv2",
    "numpy",
    "qrcode",
    "qrcode.image.pil",
)

# Only exact top-level misses are actionable for packages with generated or
# optional submodules.  Cryptography is stricter because every hazmat import in
# this application is release-critical and must be present in the bundle.
REQUIRED_WARNING_MODULES = frozenset(
    {
        *RUNTIME_IMPORTS,
        "PySide6",
        "PIL",
        "openai",
        "cryptography",
    }
)


@dataclass(frozen=True)
class LockedRequirement:
    name: str
    version: str

    @property
    def normalized_name(self) -> str:
        return re.sub(r"[-_.]+", "-", self.name).lower()


def parse_locked_requirements(path: Path) -> tuple[LockedRequirement, ...]:
    """Read a strict, fully pinned build lock file."""

    requirements: list[LockedRequirement] = []
    seen: set[str] = set()
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = _PIN_PATTERN.fullmatch(line)
        if match is None:
            raise ValueError(
                f"{path.name}:{line_number} must be an exact package==version pin: {line!r}"
            )
        requirement = LockedRequirement(match.group("name"), match.group("version"))
        if requirement.normalized_name in seen:
            raise ValueError(f"{path.name}:{line_number} repeats {requirement.name!r}.")
        seen.add(requirement.normalized_name)
        requirements.append(requirement)
    if not requirements:
        raise ValueError(f"{path} does not contain any locked dependencies.")
    return tuple(requirements)


def check_locked_versions(
    requirements: Iterable[LockedRequirement],
    version_lookup: Callable[[str], str] = metadata.version,
) -> tuple[list[str], list[str]]:
    """Return human-readable installed rows and release-blocking errors."""

    installed: list[str] = []
    errors: list[str] = []
    for requirement in requirements:
        try:
            actual = version_lookup(requirement.name)
        except metadata.PackageNotFoundError:
            errors.append(
                f"{requirement.name} {requirement.version} is required but is not installed."
            )
            continue
        except Exception as exc:  # pragma: no cover - defensive metadata boundary
            errors.append(f"Could not inspect {requirement.name}: {type(exc).__name__}.")
            continue
        installed.append(f"{requirement.name}=={actual}")
        if actual != requirement.version:
            errors.append(
                f"{requirement.name}=={actual} is installed; "
                f"the release lock requires {requirement.name}=={requirement.version}."
            )
    return installed, errors


def _numeric_version(value: str) -> tuple[int, ...]:
    match = re.match(r"^(\d+(?:\.\d+)*)", value)
    if match is None:
        return ()
    return tuple(int(part) for part in match.group(1).split("."))


def check_pyinstaller_version(
    version_lookup: Callable[[str], str] = metadata.version,
) -> tuple[str | None, list[str]]:
    try:
        actual = version_lookup("PyInstaller")
    except metadata.PackageNotFoundError:
        return None, ["PyInstaller >=6.11,<7 is required but is not installed."]
    parsed = _numeric_version(actual)
    comparable = (parsed + (0, 0))[:2]
    if not parsed or not (
        MINIMUM_PYINSTALLER <= comparable < MAXIMUM_PYINSTALLER
    ):
        return actual, [f"PyInstaller=={actual} is installed; the build requires >=6.11,<7."]
    return actual, []


def _probe_openai(importer: Callable[[str], object] = import_module) -> None:
    module = importer("openai")
    client_type = getattr(module, "OpenAI")
    # Construction loads the SDK's HTTP/Pydantic dependencies but does not make
    # a network request.  It catches incomplete OpenAI bundles before release.
    client = client_type(api_key="sk-build-preflight-placeholder", max_retries=0)
    close = getattr(client, "close", None)
    if callable(close):
        close()


def _probe_cryptography(importer: Callable[[str], object] = import_module) -> None:
    aead_module = importer("cryptography.hazmat.primitives.ciphers.aead")
    asymmetric_module = importer("cryptography.hazmat.primitives.asymmetric.ed25519")

    aesgcm_type = getattr(aead_module, "AESGCM")
    key = aesgcm_type.generate_key(bit_length=256)
    cipher = aesgcm_type(key)
    nonce = b"build-check!"  # AES-GCM requires a 12-byte nonce.
    plaintext = b"School CSM Control Center build dependency check"
    associated_data = b"v0.5-release"
    encrypted = cipher.encrypt(nonce, plaintext, associated_data)
    if cipher.decrypt(nonce, encrypted, associated_data) != plaintext:
        raise RuntimeError("AES-256-GCM round trip failed.")

    private_key = getattr(asymmetric_module, "Ed25519PrivateKey").generate()
    signature = private_key.sign(plaintext)
    private_key.public_key().verify(signature, plaintext)


def run_functional_probes(
    importer: Callable[[str], object] = import_module,
    openai_probe: Callable[[Callable[[str], object]], None] = _probe_openai,
    cryptography_probe: Callable[[Callable[[str], object]], None] = _probe_cryptography,
) -> list[str]:
    errors: list[str] = []
    for module_name in RUNTIME_IMPORTS:
        try:
            importer(module_name)
        except Exception as exc:
            errors.append(f"Could not import {module_name}: {type(exc).__name__}: {exc}")
    for label, probe in (
        ("OpenAI SDK", openai_probe),
        ("cryptography AES-256-GCM/Ed25519", cryptography_probe),
    ):
        try:
            probe(importer)
        except Exception as exc:
            errors.append(f"{label} functional probe failed: {type(exc).__name__}: {exc}")
    return errors


def scan_pyinstaller_warnings(path: Path) -> list[str]:
    """Find missing release dependencies in PyInstaller's analysis warnings."""

    if not path.is_file():
        return [f"PyInstaller warning report was not created: {path}"]
    errors: list[str] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = _MISSING_MODULE_PATTERN.search(line)
        if match is None:
            continue
        module_name = match.group("name")
        root = module_name.split(".", 1)[0]
        if module_name in REQUIRED_WARNING_MODULES or root == "cryptography":
            errors.append(f"PyInstaller did not bundle required module {module_name}: {line}")
    return errors


def _python_version_error() -> str | None:
    current = sys.version_info[:3]
    if MINIMUM_PYTHON <= current < MAXIMUM_PYTHON:
        return None
    return (
        f"Python {current[0]}.{current[1]}.{current[2]} is unsupported; "
        "the release requires Python >=3.11,<3.15."
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--requirements", type=Path, help="Exact release lock file.")
    parser.add_argument(
        "--pyinstaller-warnings",
        type=Path,
        help="PyInstaller warning report to verify after analysis.",
    )
    parser.add_argument(
        "--warnings-only",
        action="store_true",
        help="Only verify the supplied PyInstaller warning report.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    errors: list[str] = []
    installed_rows: list[str] = []

    if args.warnings_only:
        if args.pyinstaller_warnings is None:
            errors.append("--warnings-only requires --pyinstaller-warnings.")
    else:
        if args.requirements is None:
            errors.append("--requirements is required for the build preflight.")
        else:
            try:
                requirements = parse_locked_requirements(args.requirements)
            except (OSError, ValueError) as exc:
                errors.append(str(exc))
            else:
                installed_rows, version_errors = check_locked_versions(requirements)
                errors.extend(version_errors)
        python_error = _python_version_error()
        if python_error:
            errors.append(python_error)
        pyinstaller_version, pyinstaller_errors = check_pyinstaller_version()
        errors.extend(pyinstaller_errors)
        if pyinstaller_version is not None:
            installed_rows.append(f"PyInstaller=={pyinstaller_version}")
        errors.extend(run_functional_probes())

    if args.pyinstaller_warnings is not None:
        errors.extend(scan_pyinstaller_warnings(args.pyinstaller_warnings))

    if errors:
        print("Windows build dependency verification FAILED:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        if installed_rows:
            print(
                "Detected direct/build packages: " + ", ".join(installed_rows),
                file=sys.stderr,
            )
        if not args.warnings_only:
            print(
                "Install the exact requirements-lock.txt set and PyInstaller >=6.11,<7, "
                "then rebuild from a clean output directory.",
                file=sys.stderr,
            )
        return 1

    if installed_rows:
        print("Verified build dependencies: " + ", ".join(installed_rows))
    else:
        print("Verified PyInstaller analysis warnings: no required modules are missing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
