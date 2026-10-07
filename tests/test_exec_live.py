"""``Sandbox.exec`` against the live Buddy API."""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator

import pytest

from buddy_sandbox import Sandbox
from tests.shared.naming import sandbox_identifier, sandbox_name

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
async def sandbox() -> AsyncIterator[Sandbox]:
    instance = await Sandbox.create(
        name=sandbox_name("exec"), identifier=sandbox_identifier("exec")
    )
    await instance.wait_until_running()

    try:
        yield instance
    finally:
        with contextlib.suppress(Exception):
            await instance.destroy()


class TestExec:
    async def test_returns_stdout_and_a_zero_exit_code(self, sandbox: Sandbox) -> None:
        result = await sandbox.exec(command="echo hello")

        assert result.exit_code == 0
        assert "hello" in (result.stdout or "")
        assert result.stderr == ""

    async def test_reports_a_non_zero_exit_code_as_a_result(self, sandbox: Sandbox) -> None:
        result = await sandbox.exec(command="echo boom >&2 && exit 3")

        assert result.exit_code == 3
        assert "boom" in (result.stderr or "")

    async def test_echoes_the_command_back(self, sandbox: Sandbox) -> None:
        result = await sandbox.exec(command="true")

        assert result.command == "true"

    async def test_runs_in_a_non_default_runtime_and_echoes_it(self, sandbox: Sandbox) -> None:
        result = await sandbox.exec(command="print(6 * 7)", runtime="PYTHON")

        assert result.exit_code == 0
        assert "42" in (result.stdout or "")
        assert result.runtime == "PYTHON"

    async def test_leaves_no_trace_in_the_command_history(self, sandbox: Sandbox) -> None:
        before = await sandbox.list_commands()
        await sandbox.exec(command="echo untracked")
        after = await sandbox.list_commands()

        assert len(after) == len(before)
