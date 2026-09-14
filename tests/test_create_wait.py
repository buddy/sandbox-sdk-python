"""How ``Sandbox.create`` waits for a new sandbox to come up."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Final

import httpx
import pytest
import respx

from buddy_sandbox import BuddySDKError, ConnectionConfig, Sandbox
from tests.shared.api import (
    SANDBOXES_URL,
    TEST_API_URL,
    TEST_PROJECT,
    TEST_TOKEN,
    TEST_WORKSPACE,
    route_path,
)
from tests.shared.api import api as api  # noqa: RUF100 - re-exported fixture
from tests.shared.clock import FakeClock

SANDBOX_ID: Final = "sandbox-123"

CONNECTION: Final = ConnectionConfig(
    workspace=TEST_WORKSPACE, token=TEST_TOKEN, api_url=TEST_API_URL, project=TEST_PROJECT
)


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    return FakeClock.install(monkeypatch, "buddy_sandbox.utils.poll")


@dataclass
class Lifecycle:
    """What the mocked lifecycle saw."""

    requested_bodies: list[dict[str, Any]] = field(default_factory=list)
    get_count: int = 0


def not_ready_for(count: int) -> list[dict[str, str]]:
    """Keep the sandbox un-ready for ``count`` polls, then report it ready."""
    return [
        *[{"setup_status": "INPROGRESS", "status": "STARTING"} for _ in range(count)],
        {"setup_status": "SUCCESS", "status": "RUNNING"},
    ]


def mock_sandbox_lifecycle(api: respx.MockRouter, states: list[dict[str, str]]) -> Lifecycle:
    """Report STARTING on create, then walk the GET through ``states``.

    The last state repeats once the list is exhausted.
    """
    lifecycle = Lifecycle()

    def create(request: httpx.Request) -> httpx.Response:
        lifecycle.requested_bodies.append(json.loads(request.content))
        return httpx.Response(
            201,
            json={
                "id": SANDBOX_ID,
                "name": "Test Sandbox",
                "os": "ubuntu:24.04",
                "setup_status": "INPROGRESS",
                "status": "STARTING",
            },
        )

    def get(request: httpx.Request) -> httpx.Response:
        state = states[min(lifecycle.get_count, len(states) - 1)]
        lifecycle.get_count += 1
        return httpx.Response(
            200,
            json={"id": SANDBOX_ID, "name": "Test Sandbox", "os": "ubuntu:24.04", **state},
        )

    api.post(path=route_path(SANDBOXES_URL)).mock(side_effect=create)
    api.get(path=route_path(f"{SANDBOXES_URL}/{SANDBOX_ID}")).mock(side_effect=get)

    return lifecycle


@pytest.mark.usefixtures("clock")
class TestCreateReadiness:
    async def test_issues_a_single_get_per_poll_while_waiting_for_both_fields(
        self, api: respx.MockRouter
    ) -> None:
        lifecycle = mock_sandbox_lifecycle(
            api,
            [
                {"setup_status": "INPROGRESS", "status": "STARTING"},
                {"setup_status": "SUCCESS", "status": "STARTING"},
                {"setup_status": "SUCCESS", "status": "RUNNING"},
            ],
        )

        sandbox = await Sandbox.create(connection=CONNECTION)

        assert sandbox.data.status == "RUNNING"
        assert sandbox.data.setup_status == "SUCCESS"
        assert lifecycle.get_count == 3

    async def test_returns_as_soon_as_the_first_poll_sees_a_ready_sandbox(
        self, api: respx.MockRouter, clock: FakeClock
    ) -> None:
        lifecycle = mock_sandbox_lifecycle(api, [{"setup_status": "SUCCESS", "status": "RUNNING"}])

        await Sandbox.create(connection=CONNECTION)

        assert lifecycle.get_count == 1
        assert clock.delays_ms == []

    async def test_backs_off_from_100ms_while_waiting(
        self, api: respx.MockRouter, clock: FakeClock
    ) -> None:
        mock_sandbox_lifecycle(api, not_ready_for(3))

        await Sandbox.create(connection=CONNECTION)

        assert clock.delays_ms == [100, 150, 225]

    async def test_caps_the_create_backoff_at_500ms(
        self, api: respx.MockRouter, clock: FakeClock
    ) -> None:
        mock_sandbox_lifecycle(api, not_ready_for(8))

        await Sandbox.create(connection=CONNECTION)

        assert clock.rounded_delays_ms == [100, 150, 225, 338, 500, 500, 500, 500]
        assert max(clock.delays_ms) == 500

    async def test_skips_polling_entirely_when_wait_is_false(self, api: respx.MockRouter) -> None:
        lifecycle = mock_sandbox_lifecycle(
            api, [{"setup_status": "INPROGRESS", "status": "STARTING"}]
        )

        sandbox = await Sandbox.create(connection=CONNECTION, wait=False)

        assert sandbox.data.id == SANDBOX_ID
        assert lifecycle.get_count == 0

    async def test_does_not_forward_wait_to_the_api_request_body(
        self, api: respx.MockRouter
    ) -> None:
        lifecycle = mock_sandbox_lifecycle(api, [{"setup_status": "SUCCESS", "status": "RUNNING"}])

        await Sandbox.create(connection=CONNECTION, wait=False, name="Named Sandbox")

        assert len(lifecycle.requested_bodies) == 1
        assert "wait" not in lifecycle.requested_bodies[0]
        assert lifecycle.requested_bodies[0]["name"] == "Named Sandbox"

    async def test_surfaces_boot_logs_when_setup_fails(self, api: respx.MockRouter) -> None:
        mock_sandbox_lifecycle(
            api,
            [
                {"setup_status": "INPROGRESS", "status": "STARTING"},
                {"setup_status": "FAILED", "status": "STARTING"},
            ],
        )

        with pytest.raises(BuddySDKError, match=f"Sandbox {SANDBOX_ID} setup failed"):
            await Sandbox.create(connection=CONNECTION)

    async def test_reports_a_stale_setup(self, api: respx.MockRouter) -> None:
        mock_sandbox_lifecycle(api, [{"setup_status": "STALE", "status": "STARTING"}])

        with pytest.raises(BuddySDKError, match="setup is stale"):
            await Sandbox.create(connection=CONNECTION)

    async def test_fails_fast_when_the_sandbox_reports_failed(self, api: respx.MockRouter) -> None:
        mock_sandbox_lifecycle(api, [{"setup_status": "INPROGRESS", "status": "FAILED"}])

        with pytest.raises(BuddySDKError, match=f"Sandbox {SANDBOX_ID} failed"):
            await Sandbox.create(connection=CONNECTION)
