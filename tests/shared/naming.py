"""Naming for everything the suite creates.

The suite runs against a shared workspace, so everything it creates carries one
prefix - visible in the UI, and enough for the teardown to sweep by.
"""

from __future__ import annotations

import time
from typing import Final, Protocol

TEST_NAME_PREFIX: Final = "test-sandbox-"
TEST_IDENTIFIER_PREFIX: Final = "test_sandbox_"


class Named(Protocol):
    name: str | None
    identifier: str | None


def sandbox_name(label: str | None = None) -> str:
    """``test-sandbox-command-1699999999999``, or unlabelled ``test-sandbox-1699…``."""
    return f"{TEST_NAME_PREFIX}{f'{label}-' if label else ''}{int(time.time() * 1000)}"


def sandbox_identifier(label: str | None = None) -> str:
    """``test_sandbox_command_1699999999999``."""
    return f"{TEST_IDENTIFIER_PREFIX}{f'{label}_' if label else ''}{int(time.time() * 1000)}"


def is_test_sandbox(sandbox: Named) -> bool:
    """Whether a sandbox was created by this suite."""
    return bool(
        (sandbox.identifier or "").startswith(TEST_IDENTIFIER_PREFIX)
        or (sandbox.name or "").startswith(TEST_NAME_PREFIX)
    )
