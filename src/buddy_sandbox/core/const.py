"""Package identity, read from the installed distribution metadata."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

PACKAGE_NAME = "buddy-sandbox-sdk"

try:
    PACKAGE_VERSION = version(PACKAGE_NAME)
except PackageNotFoundError:  # running from a source tree without an install
    PACKAGE_VERSION = "0.0.0"
