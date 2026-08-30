"""Single source of truth for application and Windows release versions."""

from __future__ import annotations


__version__ = "0.5.1"
VERSION = (0, 5, 1)
"""Public semantic version components."""

WINDOWS_FILE_VERSION = VERSION + (0,)
WINDOWS_FILE_VERSION_TEXT = ".".join(str(component) for component in WINDOWS_FILE_VERSION)
RELEASE_TAG = f"v{__version__}"


def version_tuple() -> tuple[int, int, int]:
    """Return the public version in a comparison-friendly form."""

    return VERSION
