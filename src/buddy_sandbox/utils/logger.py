"""The SDK's logger.

Deliberately not built on :mod:`logging`: a library logging through the stdlib
without a handler stays silent, which would make ``BUDDY_LOGGER_LEVEL=debug``
do nothing. The levels below also run the opposite way round to the stdlib
ones, and :class:`~buddy_sandbox.core.http_client.HttpClient` reads them
directly to decide whether to trace requests.
"""

from __future__ import annotations

import json
import sys
from typing import Any, Final

from buddy_sandbox.core.const import PACKAGE_NAME, PACKAGE_VERSION
from buddy_sandbox.utils.environment import environment

LOG_LEVELS: Final[dict[str, int]] = {
    "debug": 5,
    "info": 4,
    "warn": 3,
    "error": 2,
    "silent": 0,
}

MAX_ERROR_DEPTH: Final = 10

_CIRCULAR = "[Circular chain]"


def _sanitise(value: Any, seen: set[int]) -> Any:
    """Replace any container already rendered once, cycle or not."""
    if isinstance(value, dict | list | tuple):
        if id(value) in seen:
            return _CIRCULAR
        seen.add(id(value))
        if isinstance(value, dict):
            return {str(key): _sanitise(item, seen) for key, item in value.items()}
        return [_sanitise(item, seen) for item in value]
    return value


class Logger:
    """Prefixed console logger gated on ``BUDDY_LOGGER_LEVEL``."""

    def __init__(self) -> None:
        self._prefix = f"{PACKAGE_NAME}@{PACKAGE_VERSION}"
        self.level = LOG_LEVELS.get(environment.BUDDY_LOGGER_LEVEL or "", LOG_LEVELS["warn"])

    @property
    def prefix(self) -> str:
        return self._prefix

    def set_level(self, name: str) -> None:
        """Change the level after import, which is when the env var is read."""
        if name not in LOG_LEVELS:
            raise ValueError(
                f'Invalid log level: "{name}". Valid levels are: {", ".join(LOG_LEVELS)}'
            )
        self.level = LOG_LEVELS[name]

    def safe_stringify(self, value: Any) -> str:
        """Render a value as JSON, tolerating cycles and unserialisable members."""
        if value is None:
            return ""

        try:
            return json.dumps(_sanitise(value, set()), indent=2, default=repr)
        except (TypeError, ValueError):
            return repr(value)

    def _format_error(self, error: BaseException, depth: int = 0) -> str:
        if depth >= MAX_ERROR_DEPTH:
            return "[Max depth reached - possible circular cause chain]"

        output = f"{type(error).__name__}: {error}"

        cause = error.__cause__ or error.__context__
        if cause is not None:
            output += f"\n  Caused by: {self._format_error(cause, depth + 1)}"

        return output

    def _line(self, level: str, message: str, data: Any) -> str:
        rendered = self.safe_stringify(data) if data is not None else ""
        return f"[{self._prefix}] {level}: {message}{f' {rendered}' if rendered else ''}"

    def debug(self, message: str, data: Any = None) -> None:
        if self.level >= LOG_LEVELS["debug"]:
            print(self._line("DEBUG", message, data))

    def info(self, message: str, data: Any = None) -> None:
        if self.level >= LOG_LEVELS["info"]:
            print(self._line("INFO", message, data))

    def warn(self, message: str, data: Any = None) -> None:
        if self.level >= LOG_LEVELS["warn"]:
            print(self._line("WARN", message, data), file=sys.stderr)

    def error(self, message: str, error: Any = None) -> None:
        if self.level < LOG_LEVELS["error"]:
            return

        if self.level >= LOG_LEVELS["debug"] and isinstance(error, BaseException):
            detail = f"\n{self._format_error(error)}"
        elif isinstance(error, BaseException):
            detail = f" - {error}" if str(error) else ""
        else:
            rendered = self.safe_stringify(error)
            detail = f" - {rendered}" if rendered else ""

        print(f"[{self._prefix}] ERROR: {message}{detail}", file=sys.stderr)


logger = Logger()
