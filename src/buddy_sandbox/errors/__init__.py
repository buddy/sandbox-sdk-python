"""The SDK's single error type and the boundary that produces it."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from enum import StrEnum
from typing import Any

from pydantic import ValidationError

from buddy_sandbox.core.http_client import HttpError


class ErrorCode(StrEnum):
    """Only three codes - ``status_code`` carries the HTTP granularity."""

    HTTP_ERROR = "HTTP_ERROR"
    """HTTP errors - use the ``status_code`` field to distinguish (401, 403, 404, 500, ...)."""

    VALIDATION_ERROR = "VALIDATION_ERROR"
    """Validation errors raised while checking user input against the schemas."""

    GENERIC_ERROR = "GENERIC_ERROR"
    """All other errors (config, streaming, internal, ...)."""


ERROR_CODES = ErrorCode


class BuddySDKError(Exception):
    """Every error the SDK raises from a public method."""

    def __init__(
        self,
        message: str,
        *,
        code: ErrorCode = ErrorCode.GENERIC_ERROR,
        status_code: int | None = None,
        details: Any = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code
        self.details = details


def _from_http_error(operation: str, error: HttpError) -> BuddySDKError:
    """Convert an :class:`HttpError` into the SDK's error type."""
    status_text = f" (HTTP {error.status})" if error.status else ""

    if error.errors:
        details = ", ".join(
            str(entry["message"]) if isinstance(entry, dict) and "message" in entry else str(entry)
            for entry in error.errors
        )
    else:
        details = error.message

    return BuddySDKError(
        f"{operation}{status_text}: {details}",
        code=ErrorCode.HTTP_ERROR,
        status_code=error.status,
        details=error.errors,
    )


def _from_validation_error(operation: str, error: ValidationError) -> BuddySDKError:
    """Convert a pydantic validation error into the SDK's error type."""
    return BuddySDKError(f"{operation}:\n{error}", code=ErrorCode.VALIDATION_ERROR)


@asynccontextmanager
async def error_handler(operation: str) -> AsyncIterator[None]:
    """Single error boundary wrapping every public method.

    ``CancelledError`` derives from ``BaseException`` and passes straight
    through, so cancelling a call does not surface as an SDK error.
    """
    try:
        yield
    except BuddySDKError:
        # Already wrapped - pass through.
        raise
    except HttpError as error:
        raise _from_http_error(operation, error) from error
    except ValidationError as error:
        raise _from_validation_error(operation, error) from error
    except Exception as error:
        raise BuddySDKError(str(error), code=ErrorCode.GENERIC_ERROR) from error
