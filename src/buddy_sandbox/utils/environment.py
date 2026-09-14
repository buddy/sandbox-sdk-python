"""Environment variables the SDK reads.

Values are read from ``os.environ`` on every access rather than captured once,
because the test suite mutates the environment between calls.
"""

from __future__ import annotations

import os


def get_environment(key: str, *, required: bool = False) -> str | None:
    """Read a variable, treating whitespace-only values as unset."""
    value = os.environ.get(key)
    trimmed = value.strip() if value is not None else None

    if not trimmed:
        if required:
            raise ValueError(
                f"Missing required configuration. Please set the {key} environment variable."
            )
        return None

    return trimmed


class Environment:
    """The SDK's view of the environment, re-read on every attribute access."""

    __slots__ = ()

    @property
    def BUDDY_TOKEN(self) -> str | None:  # noqa: N802 - mirrors the variable name
        return get_environment("BUDDY_TOKEN")

    @property
    def BUDDY_API_URL(self) -> str | None:  # noqa: N802
        return get_environment("BUDDY_API_URL")

    @property
    def BUDDY_REGION(self) -> str | None:  # noqa: N802
        return get_environment("BUDDY_REGION")

    @property
    def BUDDY_WORKSPACE(self) -> str | None:  # noqa: N802
        return get_environment("BUDDY_WORKSPACE")

    @property
    def BUDDY_PROJECT(self) -> str | None:  # noqa: N802
        return get_environment("BUDDY_PROJECT")

    @property
    def BUDDY_ENVIRONMENT(self) -> str | None:  # noqa: N802
        return get_environment("BUDDY_ENVIRONMENT")

    @property
    def BUDDY_LOGGER_LEVEL(self) -> str | None:  # noqa: N802
        return get_environment("BUDDY_LOGGER_LEVEL")

    @property
    def BUDDY_TLS_REJECT_UNAUTHORIZED(self) -> str | None:  # noqa: N802
        return get_environment("BUDDY_TLS_REJECT_UNAUTHORIZED")


environment = Environment()
