"""Shared fixtures for the test suite."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator

import pytest

from buddy_sandbox import ConnectionConfig, Sandbox
from buddy_sandbox.utils.client import create_client
from tests.shared.clock import FakeClock
from tests.shared.naming import TEST_NAME_PREFIX, is_test_sandbox
from tests.shared.scope import (
    project_environment_connection,
    workspace_connection,
    workspace_environment_connection,
)


@pytest.fixture(scope="session", autouse=True)
def _pin_scope() -> Iterator[None]:
    """Keep BUDDY_ENVIRONMENT out of the suite.

    The scope suites set ``BUDDY_TEST_*`` themselves; left set, this one would
    move every other test into that environment. Session-scoped so it runs
    before the module-scoped fixtures that create a sandbox - a function-scoped
    one runs after them, and the sandbox lands in the wrong scope.
    """
    os.environ.pop("BUDDY_ENVIRONMENT", None)
    yield


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    """A clock that advances instantly whenever the code under test sleeps."""
    return FakeClock.install(monkeypatch, "buddy_sandbox.utils.poll")


def _scopes_to_sweep() -> list[tuple[str, ConnectionConfig]]:
    """Every scope the suite might have created a sandbox in."""
    project = os.environ.get("BUDDY_PROJECT")
    workspace = os.environ.get("BUDDY_WORKSPACE")

    candidates = [
        ("project", ConnectionConfig(project=project) if project else None),
        ("workspace", ConnectionConfig(workspace=workspace) if workspace else None),
        ("test workspace", workspace_connection),
        ("workspace environment", workspace_environment_connection),
        ("project environment", project_environment_connection),
    ]

    return [(label, connection) for label, connection in candidates if connection is not None]


async def _cleanup_test_sandboxes() -> None:
    """Delete everything this suite left behind, in every configured scope."""
    print(f"\n🧹 Cleaning up '{TEST_NAME_PREFIX}' sandboxes...")

    for label, connection in _scopes_to_sweep():
        try:
            sandboxes = await Sandbox.list(connection=connection)
        except Exception as error:
            print(f"  ! Could not list the {label} scope: {error}")
            continue

        leftovers = [s for s in sandboxes if s.id and is_test_sandbox(s)]

        if not leftovers:
            print(f"  {label}: nothing to clean up.")
            continue

        client = create_client(connection)
        results = await asyncio.gather(
            *(client.delete_sandbox_by_id(path={"id": sandbox.id}) for sandbox in leftovers),
            return_exceptions=True,
        )

        for sandbox, result in zip(leftovers, results, strict=True):
            name = sandbox.identifier or sandbox.name
            if isinstance(result, BaseException):
                print(f"  ✗ {label}: {result}")
            else:
                print(f"  ✓ {label}: destroyed {name}")


def pytest_sessionstart(session: pytest.Session) -> None:
    """Sweep leftovers from an earlier run before this one starts."""
    if _integration_selected(session):
        asyncio.run(_cleanup_test_sandboxes())


def pytest_sessionfinish(session: pytest.Session) -> None:
    """Sweep again, catching anything a failing test did not clean up itself."""
    if _integration_selected(session):
        asyncio.run(_cleanup_test_sandboxes())


def _integration_selected(session: pytest.Session) -> bool:
    """Whether to sweep: only for the live suite, and only from the controller.

    Under xdist every worker is its own session, and a worker sweeping would
    delete the sandboxes the other workers are still using.
    """
    if hasattr(session.config, "workerinput"):
        return False

    return "not integration" not in session.config.getoption("-m", default="")
