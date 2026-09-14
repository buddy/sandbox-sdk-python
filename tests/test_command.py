"""The Command entity against a live sandbox."""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator

import pytest

from buddy_sandbox import Command, Sandbox
from tests.shared.naming import sandbox_identifier, sandbox_name

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
async def sandbox() -> AsyncIterator[Sandbox]:
    instance = await Sandbox.create(
        name=sandbox_name("command-test"), identifier=sandbox_identifier("command_test")
    )
    await instance.wait_until_running()

    try:
        yield instance
    finally:
        with contextlib.suppress(Exception):
            await instance.destroy()


async def detached(sandbox: Sandbox, command: str) -> Command:
    """Run a command without touching the process streams."""
    return await sandbox.run_command(command=command, stdout=None, stderr=None, detached=True)


class TestData:
    async def test_returns_the_command_response_data(self, sandbox: Sandbox) -> None:
        command = await sandbox.run_command(command="echo 'test'", stdout=None, stderr=None)

        assert command.data.id is not None
        assert command.data.status is not None

    async def test_includes_the_exit_code_after_completion(self, sandbox: Sandbox) -> None:
        command = await sandbox.run_command(command="exit 0", stdout=None, stderr=None)

        assert command.data.exit_code == 0


class TestOutput:
    async def test_returns_stdout_only_with_the_stdout_option(self, sandbox: Sandbox) -> None:
        command = await detached(sandbox, "echo 'stdout' && echo 'stderr' >&2")

        output = await command.output("STDOUT")

        assert "stdout" in output
        assert "stderr" not in output

    async def test_returns_stderr_only_with_the_stderr_option(self, sandbox: Sandbox) -> None:
        command = await detached(sandbox, "echo 'stdout' && echo 'stderr' >&2")

        output = await command.output("STDERR")

        assert "stderr" in output
        assert "stdout" not in output

    async def test_returns_both_streams_with_the_both_option(self, sandbox: Sandbox) -> None:
        command = await detached(sandbox, "echo 'stdout' && echo 'stderr' >&2")

        output = await command.output("BOTH")

        assert "stdout" in output
        assert "stderr" in output

    async def test_defaults_to_both(self, sandbox: Sandbox) -> None:
        command = await detached(sandbox, "echo 'out' && echo 'err' >&2")

        output = await command.output()

        assert "out" in output
        assert "err" in output


class TestStdoutAndStderr:
    async def test_returns_only_stdout(self, sandbox: Sandbox) -> None:
        command = await detached(sandbox, "echo 'hello stdout'")

        assert "hello stdout" in await command.stdout()

    async def test_returns_only_stderr(self, sandbox: Sandbox) -> None:
        command = await detached(sandbox, "echo 'hello stderr' >&2")

        assert "hello stderr" in await command.stderr()


class TestWait:
    async def test_waits_for_the_command_to_complete(self, sandbox: Sandbox) -> None:
        command = await detached(sandbox, "sleep 1 && echo 'done'")

        assert command.data.status == "INPROGRESS"

        finished = await command.wait()

        assert finished.data.status == "SUCCESSFUL"

    async def test_returns_a_new_instance_with_updated_data(self, sandbox: Sandbox) -> None:
        command = await detached(sandbox, "echo 'test'")

        finished = await command.wait()

        assert finished is not command
        assert finished.data.status == "SUCCESSFUL"


class TestLogs:
    async def test_streams_logs_with_the_follow_option(self, sandbox: Sandbox) -> None:
        command = await detached(sandbox, "echo 'log1' && echo 'log2'")

        logs = [log.data async for log in command.logs(follow=True) if log.data]

        assert any("log1" in line for line in logs)
        assert any("log2" in line for line in logs)

    async def test_includes_the_log_type(self, sandbox: Sandbox) -> None:
        command = await detached(sandbox, "echo 'out' && echo 'err' >&2")

        types = [log.type async for log in command.logs(follow=True) if log.type]

        assert "STDOUT" in types
        assert "STDERR" in types


class TestKill:
    async def test_terminates_a_running_command(self, sandbox: Sandbox) -> None:
        command = await detached(sandbox, "sleep 60")

        assert command.data.status == "INPROGRESS"

        await command.kill()
        finished = await command.wait()

        assert finished.data.status == "FAILED"
