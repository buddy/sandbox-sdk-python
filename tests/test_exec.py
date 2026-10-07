"""``Sandbox.exec``: run a command and wait for its result."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any, Final

import httpx
import pytest
import respx

from buddy_sandbox import BuddySDKError, ConnectionConfig, Sandbox
from buddy_sandbox.core.http_client import HttpError
from tests.shared.api import (
    SANDBOXES_URL,
    TEST_API_URL,
    TEST_PROJECT,
    TEST_TOKEN,
    TEST_WORKSPACE,
    build_client,
    route_path,
)
from tests.shared.api import api as api  # noqa: RUF100 - re-exported fixture

SANDBOX_ID: Final = "sandbox-123"
SANDBOX_URL: Final = f"{SANDBOXES_URL}/{SANDBOX_ID}"
EXEC_URL: Final = f"{SANDBOX_URL}/exec"

CONNECTION: Final = ConnectionConfig(
    workspace=TEST_WORKSPACE, token=TEST_TOKEN, api_url=TEST_API_URL, project=TEST_PROJECT
)


async def get_sandbox(api: respx.MockRouter) -> Sandbox:
    """The sandbox the instance is built from."""
    api.get(path=route_path(SANDBOX_URL)).mock(
        return_value=httpx.Response(200, json={"id": SANDBOX_ID, "status": "RUNNING"})
    )
    return await Sandbox.get_by_id(SANDBOX_ID, connection=CONNECTION)


def record_exec(api: respx.MockRouter, response: dict[str, Any]) -> list[dict[str, Any]]:
    """Capture the JSON body of every exec request, answering with ``response``."""
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=response)

    api.post(path=route_path(EXEC_URL)).mock(side_effect=handler)

    return bodies


def answer_after(
    seconds: float, response: dict[str, Any]
) -> Callable[[httpx.Request], Awaitable[httpx.Response]]:
    async def handler(_: httpx.Request) -> httpx.Response:
        await asyncio.sleep(seconds)
        return httpx.Response(200, json=response)

    return handler


class TestExec:
    async def test_returns_the_result_of_the_command(self, api: respx.MockRouter) -> None:
        sandbox = await get_sandbox(api)
        bodies = record_exec(
            api,
            {"command": "npm test", "exit_code": 0, "stdout": "All tests passed\n", "stderr": ""},
        )

        result = await sandbox.exec(command="npm test")

        assert bodies == [{"command": "npm test"}]
        assert result.exit_code == 0
        assert result.stdout == "All tests passed\n"
        assert result.stderr == ""

    async def test_passes_the_runtime_through_and_reports_a_failing_exit_code(
        self, api: respx.MockRouter
    ) -> None:
        sandbox = await get_sandbox(api)
        bodies = record_exec(
            api,
            {
                "command": "import sys; sys.exit(2)",
                "runtime": "PYTHON",
                "exit_code": 2,
                "stdout": "",
                "stderr": "boom\n",
            },
        )

        result = await sandbox.exec(command="import sys; sys.exit(2)", runtime="PYTHON")

        assert bodies == [{"command": "import sys; sys.exit(2)", "runtime": "PYTHON"}]
        assert result.exit_code == 2
        assert result.stderr == "boom\n"

    async def test_gives_up_on_its_own_deadline_rather_than_the_client_wide_one(
        self, api: respx.MockRouter
    ) -> None:
        sandbox = await get_sandbox(api)
        api.post(path=route_path(EXEC_URL)).mock(side_effect=answer_after(0.2, {"exit_code": 0}))

        with pytest.raises(BuddySDKError, match="Request timeout"):
            await sandbox.exec(command="sleep 10", timeout_ms=50)

    async def test_outlasts_the_client_wide_timeout_without_being_asked_to(
        self, api: respx.MockRouter
    ) -> None:
        slow = answer_after(0.05, {"id": "command-1", "exit_code": 0})
        api.post(path=route_path(f"{SANDBOX_URL}/commands")).mock(side_effect=slow)
        api.post(path=route_path(EXEC_URL)).mock(side_effect=slow)

        client = build_client(timeout_ms=10)

        with pytest.raises(HttpError, match="Request timeout"):
            await client.execute_command(path={"sandbox_id": SANDBOX_ID}, body={"command": "slow"})

        result = await client.exec_command(
            path={"sandbox_id": SANDBOX_ID}, body={"command": "slow"}
        )

        assert result.exit_code == 0

    async def test_does_not_rerun_a_command_after_an_ambiguous_failure(
        self, api: respx.MockRouter
    ) -> None:
        sandbox = await get_sandbox(api)
        route = api.post(path=route_path(EXEC_URL)).mock(
            side_effect=httpx.ConnectError("connection reset")
        )

        with pytest.raises(BuddySDKError):
            await sandbox.exec(command="deploy")

        assert route.call_count == 1
