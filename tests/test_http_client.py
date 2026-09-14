"""The base HTTP client: headers, query params, retries and error shaping."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator

import httpx
import pytest
import respx

from buddy_sandbox.core.http_client import HttpClient, HttpError, HttpResponse
from tests.shared.clock import FakeClock

BASE_URL = "https://test-api.example.com"


@pytest.fixture
def api() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as mock:
        yield mock


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    """Retry backoff really sleeps for seconds, so the suite fakes it away."""
    return FakeClock.install(monkeypatch, "buddy_sandbox.core.http_client")


@pytest.fixture
def client() -> HttpClient:
    return HttpClient(base_url=BASE_URL)


class TestBasicRequests:
    async def test_makes_a_get_request(self, api: respx.MockRouter, client: HttpClient) -> None:
        api.get(f"{BASE_URL}/test").mock(return_value=httpx.Response(200, json={"success": True}))

        response = await client.get("/test")

        assert response.status == 200
        assert response.data == {"success": True}

    async def test_makes_a_post_request_with_a_body(
        self, api: respx.MockRouter, client: HttpClient
    ) -> None:
        api.post(f"{BASE_URL}/test").mock(
            side_effect=lambda request: httpx.Response(
                200, json={"received": json.loads(request.content)}
            )
        )

        response = await client.post("/test", {"foo": "bar"})

        assert response.status == 200
        assert response.data == {"received": {"foo": "bar"}}

    async def test_makes_a_delete_request(self, api: respx.MockRouter, client: HttpClient) -> None:
        api.delete(f"{BASE_URL}/test/123").mock(
            return_value=httpx.Response(200, json={"deleted": True})
        )

        response = await client.delete("/test/123")

        assert response.status == 200
        assert response.data == {"deleted": True}


class TestQueryParameters:
    async def test_appends_query_params_to_the_url(
        self, api: respx.MockRouter, client: HttpClient
    ) -> None:
        route = api.get(f"{BASE_URL}/search").mock(return_value=httpx.Response(200, json={}))

        await client.get("/search", query_params={"q": "test", "limit": 10})

        params = route.calls.last.request.url.params
        assert params["q"] == "test"
        assert params["limit"] == "10"

    async def test_skips_unset_query_params(
        self, api: respx.MockRouter, client: HttpClient
    ) -> None:
        route = api.get(f"{BASE_URL}/search").mock(return_value=httpx.Response(200, json={}))

        await client.get("/search", query_params={"q": "test", "missing": None})

        params = route.calls.last.request.url.params
        assert "q" in params
        assert "missing" not in params

    async def test_serialises_booleans_the_way_the_api_expects(
        self, api: respx.MockRouter, client: HttpClient
    ) -> None:
        route = api.get(f"{BASE_URL}/search").mock(return_value=httpx.Response(200, json={}))

        await client.get("/search", query_params={"follow": True})

        assert route.calls.last.request.url.params["follow"] == "true"


class TestHeaders:
    async def test_sends_default_headers(self, api: respx.MockRouter, client: HttpClient) -> None:
        route = api.get(f"{BASE_URL}/headers").mock(return_value=httpx.Response(200, json={}))

        await client.get("/headers")

        assert route.calls.last.request.headers["Content-Type"] == "application/json"

    async def test_sends_custom_headers_from_config(self, api: respx.MockRouter) -> None:
        route = api.get(f"{BASE_URL}/headers").mock(return_value=httpx.Response(200, json={}))

        client = HttpClient(base_url=BASE_URL, headers={"X-Custom-Header": "custom-value"})

        await client.get("/headers")

        assert route.calls.last.request.headers["X-Custom-Header"] == "custom-value"

    async def test_sends_per_request_headers(
        self, api: respx.MockRouter, client: HttpClient
    ) -> None:
        route = api.get(f"{BASE_URL}/headers").mock(return_value=httpx.Response(200, json={}))

        await client.get("/headers", headers={"X-Per-Request": "request-value"})

        assert route.calls.last.request.headers["X-Per-Request"] == "request-value"

    async def test_sends_the_auth_token_once_set(
        self, api: respx.MockRouter, client: HttpClient
    ) -> None:
        route = api.get(f"{BASE_URL}/auth").mock(return_value=httpx.Response(200, json={}))

        client.set_auth_token("my-token")
        await client.get("/auth")

        assert route.calls.last.request.headers["Authorization"] == "Bearer my-token"


@pytest.mark.usefixtures("clock")
class TestRetryLogic:
    async def test_retries_on_503_and_eventually_succeeds(
        self, api: respx.MockRouter, client: HttpClient
    ) -> None:
        route = api.get(f"{BASE_URL}/retry").mock(
            side_effect=[
                httpx.Response(503, json={"error": "Service unavailable"}),
                httpx.Response(503, json={"error": "Service unavailable"}),
                httpx.Response(200, json={"success": True}),
            ]
        )

        response = await client.get("/retry")

        assert route.call_count == 3
        assert response.data == {"success": True}

    async def test_sleeps_one_two_and_four_seconds_between_attempts(
        self, api: respx.MockRouter, client: HttpClient, clock: FakeClock
    ) -> None:
        api.get(f"{BASE_URL}/always-fail").mock(return_value=httpx.Response(500, json={}))

        with pytest.raises(HttpError):
            await client.get("/always-fail")

        assert clock.delays_ms == [1000, 2000, 4000]

    async def test_retries_on_429(self, api: respx.MockRouter, client: HttpClient) -> None:
        route = api.get(f"{BASE_URL}/rate-limit").mock(
            side_effect=[
                httpx.Response(429, json={"error": "Too many requests"}),
                httpx.Response(200, json={"success": True}),
            ]
        )

        response = await client.get("/rate-limit")

        assert route.call_count == 2
        assert response.data == {"success": True}

    @pytest.mark.parametrize("status", [400, 401, 404])
    async def test_does_not_retry_client_errors(
        self, api: respx.MockRouter, client: HttpClient, status: int
    ) -> None:
        route = api.get(f"{BASE_URL}/client-error").mock(
            return_value=httpx.Response(status, json={"error": "nope"})
        )

        with pytest.raises(HttpError):
            await client.get("/client-error")

        assert route.call_count == 1

    async def test_skips_retry_when_asked_to(
        self, api: respx.MockRouter, client: HttpClient
    ) -> None:
        route = api.get(f"{BASE_URL}/no-retry").mock(return_value=httpx.Response(503, json={}))

        with pytest.raises(HttpError):
            await client.get("/no-retry", skip_retry=True)

        assert route.call_count == 1

    async def test_does_not_repeat_a_non_idempotent_request_after_a_5xx(
        self, api: respx.MockRouter, client: HttpClient
    ) -> None:
        route = api.post(f"{BASE_URL}/create").mock(
            return_value=httpx.Response(502, json={"error": "Bad gateway"})
        )

        with pytest.raises(HttpError):
            await client.post("/create", {"name": "x"}, idempotent=False)

        assert route.call_count == 1

    async def test_does_not_repeat_a_non_idempotent_request_after_a_network_error(
        self, api: respx.MockRouter, client: HttpClient
    ) -> None:
        route = api.post(f"{BASE_URL}/create-drop").mock(
            side_effect=httpx.ConnectError("connection dropped")
        )

        with pytest.raises(HttpError):
            await client.post("/create-drop", {"name": "x"}, idempotent=False)

        assert route.call_count == 1

    async def test_still_retries_a_non_idempotent_request_on_429(
        self, api: respx.MockRouter, client: HttpClient
    ) -> None:
        route = api.post(f"{BASE_URL}/create-limited").mock(
            side_effect=[
                httpx.Response(429, json={"error": "Too many requests"}),
                httpx.Response(200, json={"id": "sb-1"}),
            ]
        )

        response = await client.post("/create-limited", {"name": "x"}, idempotent=False)

        assert response.data == {"id": "sb-1"}
        assert route.call_count == 2

    async def test_keeps_retrying_idempotent_posts_on_5xx_by_default(
        self, api: respx.MockRouter, client: HttpClient
    ) -> None:
        route = api.post(f"{BASE_URL}/stop").mock(
            side_effect=[
                httpx.Response(503, json={"error": "Service unavailable"}),
                httpx.Response(200, json={"ok": True}),
            ]
        )

        response = await client.post("/stop")

        assert response.data == {"ok": True}
        assert route.call_count == 2

    async def test_fails_after_max_retries_on_a_persistent_500(
        self, api: respx.MockRouter, client: HttpClient
    ) -> None:
        route = api.get(f"{BASE_URL}/always-fail").mock(
            return_value=httpx.Response(500, json={"error": "Internal server error"})
        )

        with pytest.raises(HttpError):
            await client.get("/always-fail")

        assert route.call_count == 4  # 1 initial + 3 retries

    async def test_treats_a_malformed_body_as_a_retryable_network_failure(
        self, api: respx.MockRouter, client: HttpClient
    ) -> None:
        route = api.get(f"{BASE_URL}/garbage").mock(
            return_value=httpx.Response(200, content=b"{not json")
        )

        with pytest.raises(HttpError):
            await client.get("/garbage")

        assert route.call_count == 4


class TestResponseTypes:
    async def test_parses_json_by_default(self, api: respx.MockRouter, client: HttpClient) -> None:
        api.get(f"{BASE_URL}/json").mock(return_value=httpx.Response(200, json={"key": "value"}))

        response = await client.get("/json")

        assert response.data == {"key": "value"}

    async def test_returns_text_when_asked_for_it(
        self, api: respx.MockRouter, client: HttpClient
    ) -> None:
        api.get(f"{BASE_URL}/text").mock(
            return_value=httpx.Response(200, text="plain text response")
        )

        response = await client.get("/text", response_type="text")

        assert response.data == "plain text response"

    @pytest.mark.usefixtures("clock")
    @pytest.mark.parametrize("status", [204, 304])
    async def test_treats_only_2xx_as_success(
        self, api: respx.MockRouter, client: HttpClient, status: int
    ) -> None:
        api.get(f"{BASE_URL}/status").mock(return_value=httpx.Response(status))

        if status < 300:
            assert (await client.get("/status")).status == status
        else:
            with pytest.raises(HttpError, match=f"HTTP {status}"):
                await client.get("/status")

    async def test_handles_an_empty_response_body(
        self, api: respx.MockRouter, client: HttpClient
    ) -> None:
        api.get(f"{BASE_URL}/empty").mock(return_value=httpx.Response(204))

        response = await client.get("/empty")

        assert response.status == 204
        assert response.data is None


class TestTlsVerification:
    @staticmethod
    def _pool_verify(monkeypatch: pytest.MonkeyPatch) -> object:
        """The `verify` the pool is built with, whatever httpx does with it."""
        import buddy_sandbox.core.http_client as module

        captured: list[object] = []
        original = httpx.AsyncClient

        def spy(*args: object, **kwargs: object) -> httpx.AsyncClient:
            captured.append(kwargs.get("verify"))
            return original(*args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr("buddy_sandbox.core.http_client.httpx.AsyncClient", spy)
        module._POOLS.clear()

        async def build() -> None:
            module._pool()
            await module.aclose()

        asyncio.run(build())

        return captured[0]

    def test_is_on_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("BUDDY_TLS_REJECT_UNAUTHORIZED", raising=False)

        assert self._pool_verify(monkeypatch) is True

    def test_is_off_when_explicitly_switched_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BUDDY_TLS_REJECT_UNAUTHORIZED", "0")

        assert self._pool_verify(monkeypatch) is False

    def test_a_changed_setting_rebuilds_the_pool(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import buddy_sandbox.core.http_client as module

        async def pools() -> tuple[httpx.AsyncClient, httpx.AsyncClient]:
            monkeypatch.delenv("BUDDY_TLS_REJECT_UNAUTHORIZED", raising=False)
            verifying = module._pool()

            monkeypatch.setenv("BUDDY_TLS_REJECT_UNAUTHORIZED", "0")
            unverifying = module._pool()

            await module.aclose()
            return verifying, unverifying

        module._POOLS.clear()
        verifying, unverifying = asyncio.run(pools())

        assert verifying is not unverifying
        assert verifying.is_closed


class TestPoolDeadlines:
    def test_leaves_every_deadline_to_the_caller(self) -> None:
        import buddy_sandbox.core.http_client as module

        async def timeout() -> httpx.Timeout:
            pool = module._pool()
            deadlines = pool.timeout
            await module.aclose()
            return deadlines

        module._POOLS.clear()
        deadlines = asyncio.run(timeout())

        assert (deadlines.connect, deadlines.read, deadlines.write, deadlines.pool) == (
            None,
            None,
            None,
            None,
        )


class TestHttpError:
    def test_includes_the_status_code_in_the_message(self) -> None:
        error = HttpError("Not Found", 404)

        assert error.message == "HTTP 404: Not Found"
        assert error.status == 404

    def test_extracts_the_api_errors_array_from_the_response(self) -> None:
        response = HttpResponse(
            status=400,
            status_text="Bad Request",
            data={"errors": [{"message": "Field is required"}, {"message": "Invalid format"}]},
        )

        error = HttpError("Bad Request", 400, response)

        assert "Field is required" in error.message
        assert "Invalid format" in error.message
        assert error.errors == [{"message": "Field is required"}, {"message": "Invalid format"}]

    def test_handles_string_errors_in_the_array(self) -> None:
        response = HttpResponse(
            status=400, status_text="Bad Request", data={"errors": ["Error 1", "Error 2"]}
        )

        error = HttpError("Bad Request", 400, response)

        assert "Error 1" in error.message
        assert "Error 2" in error.message

    def test_handles_a_response_without_an_errors_array(self) -> None:
        response = HttpResponse(
            status=500, status_text="Internal Server Error", data={"message": "Something failed"}
        )

        error = HttpError("Internal Server Error", 500, response)

        assert error.message == "HTTP 500: Internal Server Error"
        assert error.errors is None

    def test_is_an_exception(self) -> None:
        error = HttpError("Test", 500)

        assert isinstance(error, Exception)
        assert type(error).__name__ == "HttpError"
