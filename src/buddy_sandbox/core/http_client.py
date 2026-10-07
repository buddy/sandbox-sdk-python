"""Base HTTP client with retry logic, timeout handling and authentication."""

from __future__ import annotations

import asyncio
import json
import weakref
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Final, Literal, NamedTuple
from urllib.parse import urljoin

import httpx

from buddy_sandbox.utils.environment import environment
from buddy_sandbox.utils.logger import LOG_LEVELS, logger

DEFAULT_TIMEOUT_MS: Final = 30_000

# Four attempts in total, sleeping 1s, 2s and 4s in between. No jitter.
RETRIES: Final = 3
BACKOFF_FACTOR: Final = 2.0
MIN_RETRY_TIMEOUT_MS: Final = 1_000
MAX_RETRY_TIMEOUT_MS: Final = 10_000

QueryValue = str | int | float | bool | None


class _Pool(NamedTuple):
    pool: httpx.AsyncClient
    verify: bool


_POOLS: Final[weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, _Pool]] = (
    weakref.WeakKeyDictionary()
)

_CLOSING: Final[set[asyncio.Task[None]]] = set()


def _discard(pool: httpx.AsyncClient) -> None:
    """Close a pool that has been replaced, without blocking the caller."""
    task = asyncio.get_running_loop().create_task(pool.aclose())
    _CLOSING.add(task)
    task.add_done_callback(_CLOSING.discard)


def _pool() -> httpx.AsyncClient:
    """The connection pool every client shares.

    Keyed by running loop, because a pool cannot be carried between them, and
    weakly so that a finished loop takes its pool with it.
    """
    loop = asyncio.get_running_loop()
    verify = environment.BUDDY_TLS_REJECT_UNAUTHORIZED != "0"
    current = _POOLS.get(loop)

    if current is not None and not current.pool.is_closed and current.verify == verify:
        return current.pool

    if current is not None and not current.pool.is_closed:
        _discard(current.pool)

    if not verify:
        logger.warn("TLS verification disabled by BUDDY_TLS_REJECT_UNAUTHORIZED=0")

    # No base_url: paths are resolved against it with urljoin, whose RFC 3986
    # rules differ from httpx's, which concatenates instead. Every deadline is
    # left to the caller - `_request` sets one, and the calls that bypass it
    # are meant to run unbounded.
    pool = httpx.AsyncClient(
        follow_redirects=True,
        verify=verify,
        timeout=httpx.Timeout(connect=None, read=None, write=None, pool=None),
    )
    _POOLS[loop] = _Pool(pool=pool, verify=verify)

    return pool


async def aclose() -> None:
    """Release the shared connection pool.

    Only worth calling from a program that keeps running once it is done with
    its sandboxes; a script can just exit.
    """
    current = _POOLS.pop(asyncio.get_running_loop(), None)

    if current is not None:
        await current.pool.aclose()


@dataclass(slots=True)
class HttpResponse:
    """Normalized HTTP response with status, data and headers."""

    status: int
    status_text: str
    data: Any
    headers: httpx.Headers = field(default_factory=httpx.Headers)


class HttpError(Exception):
    """Raised for HTTP request failures, carrying the status and response."""

    def __init__(self, message: str, status: int, response: HttpResponse | None = None) -> None:
        data = response.data if response is not None else None
        api_errors = data.get("errors") if isinstance(data, dict) else None

        full_message = f"HTTP {status}: {message}" if status else message

        if isinstance(api_errors, list) and api_errors:
            messages = [
                entry["message"] if isinstance(entry, dict) and "message" in entry else str(entry)
                for entry in api_errors
            ]
            messages = [entry for entry in messages if entry]
            if messages:
                full_message += "\n" + "\n".join(messages)

        super().__init__(full_message)

        self.message = full_message
        self.status = status
        self.response = response
        self.errors: list[Any] | None = api_errors if isinstance(api_errors, list) else None


class HttpClient:
    """Async HTTP client with retries, a request deadline and bearer auth."""

    def __init__(
        self,
        *,
        base_url: str = "",
        timeout_ms: float = DEFAULT_TIMEOUT_MS,
        headers: Mapping[str, str] | None = None,
        debug_mode: bool | None = None,
    ) -> None:
        self._debug_mode = (
            debug_mode if debug_mode is not None else logger.level >= LOG_LEVELS["debug"]
        )
        self._base_url = base_url
        self._timeout_ms = timeout_ms
        self._default_headers: dict[str, str] = {
            "Content-Type": "application/json",
            **(headers or {}),
        }
        self._auth_token: str | None = None

    @property
    def debug_mode(self) -> bool:
        return self._debug_mode

    def set_auth_token(self, token: str) -> None:
        """Set the Bearer token for authenticated requests."""
        self._auth_token = token

    @property
    def _http(self) -> httpx.AsyncClient:
        return _pool()

    def _build_url(
        self, path: str, query_params: Mapping[str, QueryValue] | None = None
    ) -> httpx.URL:
        """Build a full URL from a path and optional query parameters."""
        url = httpx.URL(urljoin(self._base_url, path))

        if query_params:
            for key, value in query_params.items():
                if value is not None:
                    url = url.copy_set_param(key, value)

        return url

    def _get_headers(self, additional: Mapping[str, str] | None = None) -> dict[str, str]:
        """Merge default headers with per-request ones and the auth token."""
        headers = {**self._default_headers, **(additional or {})}

        if self._auth_token:
            headers["Authorization"] = f"Bearer {self._auth_token}"

        return headers

    async def _execute_with_retry(
        self,
        request: Callable[[], Awaitable[HttpResponse]],
        *,
        skip_retry: bool = False,
        idempotent: bool = True,
    ) -> HttpResponse:
        """Run a request, retrying transient failures."""
        if skip_retry:
            return await request()

        consumed = 0

        while True:
            try:
                return await request()
            except HttpError as error:
                if error.status != 429:
                    # Network errors and timeouts (status 0) and 5xx responses may
                    # have reached the server, so a non-idempotent request is not
                    # repeated.
                    if not idempotent:
                        raise
                    if 400 <= error.status < 500:
                        raise

                if consumed >= RETRIES:
                    raise

                delay_ms = min(
                    MIN_RETRY_TIMEOUT_MS * BACKOFF_FACTOR**consumed, MAX_RETRY_TIMEOUT_MS
                )
                await asyncio.sleep(delay_ms / 1000)
                consumed += 1

    async def _request(
        self,
        method: str,
        url: str,
        data: Any = None,
        *,
        skip_retry: bool = False,
        idempotent: bool = True,
        query_params: Mapping[str, QueryValue] | None = None,
        headers: Mapping[str, str] | None = None,
        response_type: Literal["json", "text"] = "json",
        timeout_ms: float | None = None,
    ) -> HttpResponse:
        """Execute an HTTP request with timeout, retry and error handling.

        ``timeout_ms`` overrides the client-wide timeout for this request only.
        """
        full_url = self._build_url(url, query_params)
        request_headers = self._get_headers(headers)
        deadline_ms = timeout_ms if timeout_ms is not None else self._timeout_ms

        async def make_request() -> HttpResponse:
            try:
                if self._debug_mode:
                    logger.debug(
                        "[HTTP REQUEST]",
                        {
                            "method": method,
                            "url": str(full_url),
                            "headers": {
                                **request_headers,
                                "Authorization": "***"
                                if "Authorization" in request_headers
                                else None,
                            },
                            "body": data,
                        },
                    )

                content = json.dumps(data).encode() if data is not None else None

                async with asyncio.timeout(deadline_ms / 1000):
                    response = await self._http.request(
                        method, full_url, headers=request_headers, content=content
                    )

                text = response.text
                if response_type == "text":
                    response_data: Any = text
                else:
                    # A malformed body is reported as a network failure, which
                    # makes it retryable: the server is what is misbehaving.
                    response_data = json.loads(text) if text else None

                http_response = HttpResponse(
                    status=response.status_code,
                    status_text=response.reason_phrase,
                    data=response_data,
                    headers=response.headers,
                )

                if self._debug_mode:
                    logger.debug(
                        "[HTTP RESPONSE]", {"status": response.status_code, "body": response_data}
                    )

                if not response.is_success:
                    raise HttpError(
                        response.reason_phrase or "Request failed",
                        response.status_code,
                        http_response,
                    )

                return http_response
            except HttpError:
                raise
            except (TimeoutError, httpx.TimeoutException):
                raise HttpError("Request timeout", 0) from None
            except Exception as error:
                raise HttpError(str(error) or "Request failed", 0) from error

        return await self._execute_with_retry(
            make_request, skip_retry=skip_retry, idempotent=idempotent
        )

    async def get(self, url: str, **config: Any) -> HttpResponse:
        """Perform a GET request."""
        return await self._request("GET", url, None, **config)

    async def post(self, url: str, data: Any = None, **config: Any) -> HttpResponse:
        """Perform a POST request with optional body data."""
        return await self._request("POST", url, {} if data is None else data, **config)

    async def delete(self, url: str, **config: Any) -> HttpResponse:
        """Perform a DELETE request."""
        return await self._request("DELETE", url, None, **config)

    async def patch(self, url: str, data: Any = None, **config: Any) -> HttpResponse:
        """Perform a PATCH request with optional body data."""
        return await self._request("PATCH", url, {} if data is None else data, **config)
