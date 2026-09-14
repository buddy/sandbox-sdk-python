"""Mocked-API helpers shared by the suites that never touch the network."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any, Final

import httpx
import pytest
import respx

from buddy_sandbox import ConnectionConfig
from buddy_sandbox.core.buddy_api_client import BuddyApiClient

TEST_API_URL: Final = "https://api.test.buddy.works"
TEST_WORKSPACE: Final = "test-workspace"
TEST_PROJECT: Final = "test-project"
TEST_TOKEN: Final = "test-token"

TEST_CONNECTION: Final = ConnectionConfig(
    workspace=TEST_WORKSPACE, token=TEST_TOKEN, api_url=TEST_API_URL
)

SANDBOXES_URL: Final = f"{TEST_API_URL}/workspaces/{TEST_WORKSPACE}/sandboxes"
SNAPSHOTS_URL: Final = f"{SANDBOXES_URL}/snapshots"
IDENTIFIERS_URL: Final = f"{TEST_API_URL}/workspaces/{TEST_WORKSPACE}/identifiers"


def route_path(url: str) -> str:
    """The path of a URL, so a route matches regardless of query params."""
    return httpx.URL(url).path


@pytest.fixture
def api() -> Iterator[respx.MockRouter]:
    """A mock API wired into the test's lifecycle, rejecting unhandled requests."""
    with respx.mock(base_url=TEST_API_URL, assert_all_mocked=True, assert_all_called=False) as mock:
        yield mock


def build_client(**scope: Any) -> BuddyApiClient:
    """A client pointed at the mock API, scoped however the test needs."""
    return BuddyApiClient(workspace=TEST_WORKSPACE, api_url=TEST_API_URL, token=TEST_TOKEN, **scope)


def record_queries(
    api: respx.MockRouter, url: str, response: dict[str, Any]
) -> list[httpx.QueryParams]:
    """Capture the query params of every GET to ``url``, answering with ``response``."""
    queries: list[httpx.QueryParams] = []

    def handler(request: httpx.Request) -> httpx.Response:
        queries.append(request.url.params)
        return httpx.Response(200, json=response)

    api.get(path=route_path(url)).mock(side_effect=handler)

    return queries


def capture_body(api: respx.MockRouter, url: str, response: dict[str, Any]) -> dict[str, Any]:
    """Capture the JSON body of every POST to ``url``, answering 201 with ``response``."""
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.clear()
        captured.update(json.loads(request.content))
        return httpx.Response(201, json=response)

    api.post(path=route_path(url)).mock(side_effect=handler)

    return captured
