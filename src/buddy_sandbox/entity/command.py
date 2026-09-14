"""A running or completed command execution inside a sandbox."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import aclosing
from typing import TYPE_CHECKING, Literal

from buddy_sandbox.api.openapi.pydantic_gen import (
    SandboxCommandLog,
    SandboxCommandResultView,
    SandboxCommandView,
)
from buddy_sandbox.utils.poll import poll_until, resolve_poll_interval

if TYPE_CHECKING:
    from buddy_sandbox.core.buddy_api_client import BuddyApiClient

CommandResponse = SandboxCommandView | SandboxCommandResultView

Stream = Literal["STDOUT", "STDERR", "BOTH"]


class Command:
    """Represents a running or completed command execution in a sandbox."""

    def __init__(
        self,
        *,
        command_response: CommandResponse,
        client: BuddyApiClient,
        sandbox_id: str,
    ) -> None:
        if not isinstance(command_response, SandboxCommandView) or not command_response.id:
            raise ValueError("Command response must have an id")

        command_id = command_response.id

        self._command_response = command_response
        self._client = client
        self._sandbox_id = sandbox_id
        self._command_id: str = command_id
        # An async task holds only a weak reference, so a detached run's log
        # pump has to be kept alive by the command it belongs to.
        self._stream_task: asyncio.Task[None] | None = None

    @property
    def data(self) -> SandboxCommandView:
        """The raw command response data from the API."""
        return self._command_response

    @property
    def id(self) -> str:
        """The command ID."""
        return self._command_id

    def logs(self, *, follow: bool | None = None) -> AsyncGenerator[SandboxCommandLog, None]:
        """Stream logs from the command in real time."""
        return self._client.stream_command_logs(
            path={"command_id": self._command_id, "sandbox_id": self._sandbox_id},
            query={"follow": follow},
        )

    async def wait(self) -> Command:
        """Wait for the command to finish execution."""
        final_response = await self._poll_for_command_completion()
        return Command(
            command_response=final_response,
            client=self._client,
            sandbox_id=self._sandbox_id,
        )

    async def output(self, stream: Stream = "BOTH") -> str:
        """Get all output from the command, waiting for it to complete."""
        data = ""
        async with aclosing(self.logs(follow=True)) as entries:
            async for log in entries:
                if stream in ("BOTH", log.type):
                    data += f"{log.data or ''}\n"
        return data

    async def stdout(self) -> str:
        """Get all stdout output from the command, waiting for it to complete."""
        return await self.output("STDOUT")

    async def stderr(self) -> str:
        """Get all stderr output from the command, waiting for it to complete."""
        return await self.output("STDERR")

    async def kill(self) -> None:
        """Terminate the running command."""
        await self._client.terminate_command(
            path={"sandbox_id": self._sandbox_id, "command_id": self._command_id}
        )

    async def _poll_for_command_completion(
        self, poll_interval_ms: float | None = None
    ) -> SandboxCommandView:
        """Poll the API until the command reaches a terminal state."""
        final_response: SandboxCommandView | None = None

        async def check() -> bool:
            nonlocal final_response

            command_response = await self._client.get_command_details(
                path={"sandbox_id": self._sandbox_id, "id": self._command_id}
            )

            if command_response.status in ("SUCCESSFUL", "FAILED"):
                final_response = command_response
                return True

            return False

        await poll_until(check, **resolve_poll_interval(poll_interval_ms))

        if final_response is None:
            raise RuntimeError(f"Command {self._command_id} finished without a response.")

        return final_response
