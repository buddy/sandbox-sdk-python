"""Contract tests for ``sandbox.update()``.

These pin how the backend reacts to a PATCH on each writable field:

* "soft" fields apply in place - the sandbox stays RUNNING with setup SUCCESS,
* ``first_boot_commands`` moves setup to STALE, so the sandbox must be recreated,
* ``resources`` applies in place, with no restart or recreate.

A backend change that breaks any of these fails here rather than in the wild.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from typing import Any

import pytest

from buddy_sandbox import Sandbox
from tests.shared.naming import sandbox_identifier, sandbox_name

pytestmark = pytest.mark.integration


async def _sandbox(**config: Any) -> AsyncIterator[Sandbox]:
    sandbox = await Sandbox.create(**config)
    try:
        yield sandbox
    finally:
        with contextlib.suppress(Exception):
            await sandbox.destroy()


@pytest.fixture(scope="class")
async def probe() -> AsyncIterator[Sandbox]:
    """One shared sandbox for every field that applies in place."""
    async for sandbox in _sandbox(
        name=sandbox_name("update-probe"),
        identifier=sandbox_identifier("update_probe"),
        timeout=600,
        tags=["initial"],
        apps=["echo initial-app"],
        first_boot_commands="echo initial-first-boot",
    ):
        yield sandbox


def assert_soft_update(sandbox: Sandbox) -> None:
    assert sandbox.data.setup_status == "SUCCESS"
    assert sandbox.data.status == "RUNNING"


class TestSoftFields:
    async def test_updates_timeout_in_place(self, probe: Sandbox) -> None:
        await probe.update(timeout=1800)

        assert probe.data.timeout == 1800
        assert_soft_update(probe)

    async def test_updates_tags_in_place(self, probe: Sandbox) -> None:
        await probe.update(tags=["probed", "updated"])

        assert set(probe.data.tags or []) >= {"probed", "updated"}
        assert_soft_update(probe)

    async def test_updates_name_in_place(self, probe: Sandbox) -> None:
        name = sandbox_name("renamed")

        await probe.update(name=name)

        assert probe.data.name == name
        assert_soft_update(probe)

    async def test_updates_variables_in_place(self, probe: Sandbox) -> None:
        await probe.update(variables=[{"key": "PROBE_VAR", "value": "hello", "type": "VAR"}])

        assert any(
            variable.key == "PROBE_VAR" and variable.value == "hello"
            for variable in probe.data.variables or []
        )
        assert_soft_update(probe)

    async def test_updates_apps_in_place(self, probe: Sandbox) -> None:
        await probe.update(apps=[{"command": "echo updated-app"}])

        assert any(app.command == "echo updated-app" for app in probe.data.apps or [])
        assert_soft_update(probe)

    async def test_updates_app_dir_in_place(self, probe: Sandbox) -> None:
        await probe.update(app_dir="/workspace/probed")

        assert probe.data.app_dir == "/workspace/probed"
        assert_soft_update(probe)

    async def test_updates_endpoints_in_place(self, probe: Sandbox) -> None:
        await probe.update(
            endpoints=[
                {
                    "name": "probed-endpoint",
                    "endpoint": "127.0.0.1:3000",
                    "type": "HTTP",
                    "region": "US",
                }
            ]
        )

        assert any(endpoint.name == "probed-endpoint" for endpoint in probe.data.endpoints or [])
        assert_soft_update(probe)


class TestFirstBootCommands:
    async def test_changing_them_moves_setup_status_to_stale(self) -> None:
        async for sandbox in _sandbox(
            name=sandbox_name("fbc-probe"),
            identifier=sandbox_identifier("fbc_probe"),
            first_boot_commands="echo initial-first-boot",
        ):
            await sandbox.update(first_boot_commands="echo CHANGED-first-boot")
            await sandbox.refresh()

            assert sandbox.data.first_boot_commands == "echo CHANGED-first-boot"
            assert sandbox.data.setup_status == "STALE"
            assert sandbox.data.status == "RUNNING"


class TestResources:
    async def test_changing_them_applies_in_place(self) -> None:
        async for sandbox in _sandbox(
            name=sandbox_name("resources-probe"),
            identifier=sandbox_identifier("resources_probe"),
            resources="1x2",
        ):
            await sandbox.update(resources="2x4")
            await sandbox.refresh()

            assert sandbox.data.resources == "2x4"
            assert sandbox.data.setup_status == "SUCCESS"
            assert sandbox.data.status == "RUNNING"
