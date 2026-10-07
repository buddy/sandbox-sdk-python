"""The sandbox entity - the SDK's main entry point."""

from __future__ import annotations

import asyncio
import builtins
import sys
import time
from collections.abc import Mapping
from contextlib import aclosing
from datetime import UTC, datetime
from typing import Any, Final, TextIO

from pydantic import BaseModel

from buddy_sandbox.api.openapi.pydantic_gen import (
    CloneSandboxRequest,
    CreateFromSnapshotRequestWritable,
    CreateNewSandboxRequestWritable,
    ExecuteSandboxCommandRequest,
    SandboxAppLogsView,
    SandboxCommandResultView,
    SandboxIdView,
    SandboxResponse,
    ShortSnapshotView,
    UpdateSandboxRequestWritable,
)
from buddy_sandbox.core.buddy_api_client import BuddyApiClient
from buddy_sandbox.entity.command import Command
from buddy_sandbox.entity.filesystem import FileSystem
from buddy_sandbox.entity.snapshot import Snapshot
from buddy_sandbox.errors import error_handler
from buddy_sandbox.utils.client import ConnectionConfig, create_client
from buddy_sandbox.utils.hybrid import hybridmethod
from buddy_sandbox.utils.logger import logger
from buddy_sandbox.utils.poll import poll_until, resolve_poll_interval

CREATE_MAX_INTERVAL_MS: Final = 500

_PRIVATE_CONSTRUCTOR_KEY: Final = object()

INITIALIZE_INSTRUCTIONS: Final = (
    "Use Sandbox.create(), Sandbox.get_by_id(), or Sandbox.get_by_identifier() "
    "to obtain an instance."
)

#: Filled in by the client from the connection, never by the caller.
_SCOPE_FIELDS: Final = frozenset({"scope", "environment"})


class _Default:
    """Marker for an argument that was not passed at all."""


#: Leave a stream out for the process default, pass ``None`` to silence it.
DEFAULT_STREAM: Final = _Default()

StreamArg = TextIO | None | _Default


def _check_fields(model: type[BaseModel], config: Mapping[str, Any], omit: frozenset[str]) -> None:
    """Reject unknown keys, which validation would otherwise drop in silence."""
    allowed = set(model.model_fields) - omit
    unknown = sorted(set(config) - allowed)

    if unknown:
        raise ValueError(
            f"Unknown field(s): {', '.join(unknown)}. Accepted: {', '.join(sorted(allowed))}."
        )


def _default_name() -> str:
    return f"Sandbox {datetime.now(UTC).isoformat(timespec='milliseconds').replace('+00:00', 'Z')}"


def _default_identifier() -> str:
    return f"sandbox_{int(time.time() * 1000)}"


class Sandbox:
    """An isolated Ubuntu environment for running commands."""

    def __init__(
        self,
        sandbox_data: SandboxResponse,
        client: BuddyApiClient,
        constructor_key: Any = None,
    ) -> None:
        if constructor_key is not _PRIVATE_CONSTRUCTOR_KEY:
            raise RuntimeError(f"Cannot construct Sandbox directly. {INITIALIZE_INSTRUCTIONS}")

        self._sandbox_data = sandbox_data
        self._client = client
        self._fs: FileSystem | None = None

    @property
    def data(self) -> SandboxResponse:
        """The raw sandbox response data from the API."""
        return self._sandbox_data

    @property
    def initialized_id(self) -> str:
        """The sandbox ID, raising if the instance carries none."""
        sandbox_id = self._sandbox_data.id
        if not sandbox_id:
            raise RuntimeError(
                "Sandbox ID is missing. The sandbox may have been deleted or not properly "
                f"initialized. {INITIALIZE_INSTRUCTIONS}"
            )
        return sandbox_id

    @property
    def fs(self) -> FileSystem:
        """File system operations for this sandbox."""
        sandbox_id = self.initialized_id
        if self._fs is None:
            self._fs = FileSystem(self._client, sandbox_id)
        return self._fs

    # ------------------------------------------------------------------
    # Creation
    # ------------------------------------------------------------------

    @classmethod
    async def create(
        cls,
        *,
        connection: ConnectionConfig | None = None,
        wait: bool = True,
        **config: Any,
    ) -> Sandbox:
        """Create a new sandbox.

        Blocks until the sandbox has finished setup and reached ``RUNNING``,
        unless ``wait`` is false.
        """
        async with error_handler("Failed to create sandbox"):
            _check_fields(CreateNewSandboxRequestWritable, config, _SCOPE_FIELDS)
            client = create_client(connection)

            body = {
                "name": _default_name(),
                "identifier": _default_identifier(),
                "os": "ubuntu:24.04",
                **config,
            }

            return await cls._finalize_new_sandbox(
                await client.add_sandbox(body=body), client, wait=wait
            )

    @classmethod
    async def create_from_snapshot(
        cls,
        snapshot_id: str,
        *,
        connection: ConnectionConfig | None = None,
        wait: bool = True,
        **config: Any,
    ) -> Sandbox:
        """Create a new sandbox restored from an existing snapshot."""
        async with error_handler("Failed to create sandbox from snapshot"):
            _check_fields(
                CreateFromSnapshotRequestWritable, config, _SCOPE_FIELDS | {"snapshot_id"}
            )
            client = create_client(connection)

            body = {
                "snapshot_id": snapshot_id,
                "name": _default_name(),
                "identifier": _default_identifier(),
                **config,
            }

            return await cls._finalize_new_sandbox(
                await client.add_sandbox(body=body), client, wait=wait
            )

    @classmethod
    async def clone(
        cls,
        source_sandbox_id: str,
        *,
        connection: ConnectionConfig | None = None,
        wait: bool = True,
        **config: Any,
    ) -> Sandbox:
        """Clone a live sandbox into a new one, without going through a snapshot."""
        async with error_handler("Failed to clone sandbox"):
            _check_fields(CloneSandboxRequest, config, _SCOPE_FIELDS | {"source_sandbox_id"})
            client = create_client(connection)

            body = {
                "source_sandbox_id": source_sandbox_id,
                "name": _default_name(),
                "identifier": _default_identifier(),
                **config,
            }

            return await cls._finalize_new_sandbox(
                await client.add_sandbox(body=body), client, wait=wait
            )

    @classmethod
    async def _finalize_new_sandbox(
        cls, sandbox_response: SandboxResponse, client: BuddyApiClient, *, wait: bool = True
    ) -> Sandbox:
        """Wrap a create response, optionally waiting for the sandbox to run."""
        sandbox = cls(sandbox_response, client, _PRIVATE_CONSTRUCTOR_KEY)

        if not wait:
            return sandbox

        logger.debug(f"Waiting for sandbox {sandbox.data.id} to be ready...")
        await sandbox._wait_until_ready_and_running()
        logger.debug(
            f"Sandbox {sandbox.data.id} is now running "
            f"(Setup status: {sandbox.data.setup_status}, Status: {sandbox.data.status})"
        )

        return sandbox

    async def _wait_until_ready_and_running(self, max_running_wait_ms: float = 60_000) -> None:
        """Wait for setup to finish and the sandbox to reach RUNNING.

        One loop covers both, since a single refresh already carries both
        fields. Setup is unbounded because ``first_boot_commands`` may run long;
        the RUNNING deadline only starts once setup has succeeded.
        """
        sandbox_id = self.initialized_id

        async with error_handler("Sandbox not running"):
            ready_at: float | None = None

            async def check() -> bool:
                nonlocal ready_at

                await self.refresh()
                self._assert_setup_not_failed(sandbox_id)

                if self.data.status == "FAILED":
                    raise RuntimeError(f"Sandbox {sandbox_id} failed. Status: {self.data.status}")

                if self.data.setup_status != "SUCCESS":
                    return False

                if self.data.status == "RUNNING":
                    return True

                if ready_at is None:
                    ready_at = time.monotonic()

                if (time.monotonic() - ready_at) * 1000 > max_running_wait_ms:
                    raise TimeoutError(
                        f"Timeout waiting for sandbox {sandbox_id} to be RUNNING. "
                        f"Current: {self.data.status}"
                    )

                return False

            await poll_until(check, initial_interval_ms=100, max_interval_ms=CREATE_MAX_INTERVAL_MS)

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    @classmethod
    async def get_by_id(
        cls, sandbox_id: str, *, connection: ConnectionConfig | None = None
    ) -> Sandbox:
        """Get an existing sandbox by its ID."""
        async with error_handler("Failed to get sandbox"):
            client = create_client(connection)
            sandbox_response = await client.get_sandbox_by_id(path={"id": sandbox_id})

            if not sandbox_response:
                raise RuntimeError(f"Sandbox with ID '{sandbox_id}' not found")

            return cls(sandbox_response, client, _PRIVATE_CONSTRUCTOR_KEY)

    @classmethod
    async def get_by_identifier(
        cls, identifier: str, *, connection: ConnectionConfig | None = None
    ) -> Sandbox:
        """Get an existing sandbox by its identifier."""
        async with error_handler("Failed to get sandbox by identifier"):
            client = create_client(connection)

            sandbox_id = await cls._resolve_sandbox_id(client, identifier)

            if not sandbox_id:
                raise RuntimeError(f"Sandbox with identifier '{identifier}' not found")

            sandbox_response = await client.get_sandbox_by_id(path={"id": sandbox_id})

            if not sandbox_response:
                raise RuntimeError(f"Sandbox with ID '{sandbox_id}' not found")

            return cls(sandbox_response, client, _PRIVATE_CONSTRUCTOR_KEY)

    @staticmethod
    async def _resolve_sandbox_id(client: BuddyApiClient, identifier: str) -> str | None:
        """Resolve a sandbox identifier to its ID.

        ``/identifiers`` looks the sandbox up in whichever scope it is given -
        the environment, else the project, else the workspace - so one request
        covers all three, and an environment needs no ID resolved first.
        """
        identifiers = await client.get_identifiers(
            query={**client.identifiers_scope_query(), "sandbox": identifier}
        )
        return identifiers.sandbox_id

    @classmethod
    async def list(cls, *, connection: ConnectionConfig | None = None) -> list[SandboxIdView]:
        """List sandboxes in the current scope.

        Scopes are disjoint: with a project configured you get that project's
        sandboxes, with an environment - that environment's, and with neither
        only workspace-level ones. Listing across scopes means one call per
        scope.
        """
        async with error_handler("Failed to list sandboxes"):
            client = create_client(connection)
            sandbox_list = await client.get_sandboxes()
            return list(sandbox_list.sandboxes or [])

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    async def exec(
        self, *, command: str, timeout_ms: float | None = None, **request: Any
    ) -> SandboxCommandResultView:
        """Run a command in the sandbox and wait for it to finish.

        Returns the exit code and the output it produced, with no polling and no
        log stream in between. The command leaves no trace in the sandbox's
        command history, streams no logs and cannot be terminated, and the API
        fails it after 60 seconds. Use :meth:`run_command` for anything longer,
        to follow the output as it arrives, or to leave a command running
        detached.

        ``timeout_ms`` defaults to just past that 60 second ceiling, so the API
        decides the outcome rather than the client giving up first.
        """
        sandbox_id = self.initialized_id

        async with error_handler("Failed to execute command"):
            _check_fields(ExecuteSandboxCommandRequest, request, frozenset({"command"}))

            logger.debug(f"Executing command: $ {command}")

            return await self._client.exec_command(
                path={"sandbox_id": sandbox_id},
                body={"command": command, **request},
                timeout_ms=timeout_ms,
            )

    async def run_command(
        self,
        *,
        command: str,
        stdout: StreamArg = DEFAULT_STREAM,
        stderr: StreamArg = DEFAULT_STREAM,
        detached: bool = False,
        **request: Any,
    ) -> Command:
        """Execute a command in the sandbox.

        Blocks until the command finishes unless ``detached`` is true, streaming
        its output to ``stdout``/``stderr`` meanwhile.
        """
        sandbox_id = self.initialized_id

        async with error_handler("Failed to run command"):
            _check_fields(ExecuteSandboxCommandRequest, request, frozenset({"command"}))

            out = _resolve_stream(stdout, sys.stdout)
            err = _resolve_stream(stderr, sys.stderr)

            logger.debug(f"Executing command: $ {command}")

            command_response = await self._client.execute_command(
                path={"sandbox_id": sandbox_id}, body={"command": command, **request}
            )

            entity = Command(
                command_response=command_response, client=self._client, sandbox_id=sandbox_id
            )

            if out is None and err is None:
                return entity if detached else await entity.wait()

            stream_task = asyncio.create_task(_pump_logs(entity, out, err))

            if detached:
                entity._stream_task = stream_task
                return entity

            wait_task = asyncio.create_task(entity.wait())

            try:
                finished, _ = await asyncio.gather(wait_task, stream_task)
            except BaseException:
                for task in (wait_task, stream_task):
                    task.cancel()
                await asyncio.gather(wait_task, stream_task, return_exceptions=True)
                raise

            return finished

    async def list_commands(self) -> builtins.list[Command]:
        """List every command execution in this sandbox, newest run included."""
        sandbox_id = self.initialized_id

        async with error_handler("Failed to list commands"):
            response = await self._client.get_sandbox_commands(path={"sandbox_id": sandbox_id})
            return [
                Command(
                    command_response=command_response, client=self._client, sandbox_id=sandbox_id
                )
                for command_response in response.commands or []
            ]

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    async def update(self, **config: Any) -> None:
        """Update the sandbox configuration in place.

        Changing ``first_boot_commands`` leaves the sandbox in
        ``setup_status: STALE`` - the new commands only apply on first boot, so
        the sandbox must be recreated for them to take effect.
        """
        sandbox_id = self.initialized_id

        async with error_handler("Failed to update sandbox"):
            _check_fields(UpdateSandboxRequestWritable, config, _SCOPE_FIELDS | {"project"})

            self._sandbox_data = await self._client.update_sandbox(
                path={"id": sandbox_id}, body=config
            )

    # ------------------------------------------------------------------
    # Snapshots
    # ------------------------------------------------------------------

    async def create_snapshot(self, *, name: str | None = None) -> Snapshot:
        """Create a snapshot of the current sandbox.

        Returns immediately with a snapshot whose status is ``"CREATING"``. Call
        :meth:`Snapshot.wait_until_ready` before restoring from it.
        """
        sandbox_id = self.initialized_id

        async with error_handler("Failed to create snapshot"):
            data = await self._client.add_sandbox_snapshot(
                path={"sandbox_id": sandbox_id}, body={"name": name} if name else {}
            )
            return Snapshot._build(data, self._client, sandbox_id)

    @hybridmethod
    async def list_snapshots(
        cls, *, connection: ConnectionConfig | None = None
    ) -> builtins.list[ShortSnapshotView]:
        """List every snapshot in the current scope, across all sandboxes.

        Includes snapshots whose parent sandbox has been deleted, and is scoped
        exactly like :meth:`Sandbox.list`.
        """
        async with error_handler("Failed to list project snapshots"):
            client = create_client(connection)
            response = await client.get_project_snapshots()
            return list(response.snapshots or [])

    @list_snapshots.instancemethod  # type: ignore[no-redef]
    async def list_snapshots(self) -> builtins.list[Snapshot]:
        """List this sandbox's own snapshots."""
        sandbox_id = self.initialized_id

        async with error_handler("Failed to list snapshots"):
            response = await self._client.get_sandbox_snapshots(path={"sandbox_id": sandbox_id})
            return [
                Snapshot._build(data, self._client, sandbox_id) for data in response.snapshots or []
            ]

    @classmethod
    async def get_snapshot_by_id(
        cls, sandbox_id: str, snapshot_id: str, *, connection: ConnectionConfig | None = None
    ) -> Snapshot:
        """Get a snapshot without first fetching its parent sandbox."""
        async with error_handler("Failed to get snapshot"):
            client = create_client(connection)
            data = await client.get_sandbox_snapshot(
                path={"sandbox_id": sandbox_id, "id": snapshot_id}
            )
            return Snapshot._build(data, client, sandbox_id)

    async def get_snapshot(self, snapshot_id: str) -> Snapshot:
        """Get a single snapshot of this sandbox by ID."""
        sandbox_id = self.initialized_id

        async with error_handler("Failed to get snapshot"):
            data = await self._client.get_sandbox_snapshot(
                path={"sandbox_id": sandbox_id, "id": snapshot_id}
            )
            return Snapshot._build(data, self._client, sandbox_id)

    @hybridmethod
    async def delete_snapshot(
        cls, snapshot_id: str, *, connection: ConnectionConfig | None = None
    ) -> None:
        """Delete a snapshot without needing its parent sandbox.

        Useful for cleaning up snapshots whose parent sandbox is already gone.
        """
        async with error_handler("Failed to delete snapshot"):
            client = create_client(connection)
            await client.delete_snapshot(path={"id": snapshot_id})

    @delete_snapshot.instancemethod  # type: ignore[no-redef]
    async def delete_snapshot(self, snapshot_id: str) -> None:
        """Delete a snapshot of this sandbox by ID."""
        sandbox_id = self.initialized_id

        async with error_handler("Failed to delete snapshot"):
            await self._client.delete_sandbox_snapshot(
                path={"sandbox_id": sandbox_id, "id": snapshot_id}
            )

    async def wait_for_snapshot_ready(
        self,
        snapshot_id: str,
        poll_interval_ms: float | None = None,
        max_wait_ms: float = 180_000,
    ) -> None:
        """Wait until a snapshot of this sandbox reaches CREATED."""
        snapshot = await self.get_snapshot(snapshot_id)
        await snapshot.wait_until_ready(poll_interval_ms, max_wait_ms)

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    async def refresh(self) -> None:
        """Refresh the sandbox data from the API."""
        sandbox_id = self.initialized_id

        async with error_handler("Failed to refresh sandbox"):
            self._sandbox_data = await self._client.get_sandbox_by_id(path={"id": sandbox_id})

    async def destroy(self) -> None:
        """Delete the sandbox permanently."""
        sandbox_id = self.initialized_id

        async with error_handler("Failed to destroy sandbox"):
            await self._client.delete_sandbox_by_id(path={"id": sandbox_id})

    def _assert_setup_not_failed(self, sandbox_id: str) -> None:
        """Raise if ``setup_status`` has reached a terminal failure state."""
        if self.data.setup_status == "FAILED":
            logs = self.data.boot_logs or []
            recent = logs[-20:]
            tail = "\n".join(recent)
            detail = (
                f"\nBoot logs (last {len(recent)} of {len(logs)} lines):\n{tail}"
                if tail
                else " No boot logs were returned."
            )
            raise RuntimeError(f"Sandbox {sandbox_id} setup failed.{detail}")

        if self.data.setup_status == "STALE":
            raise RuntimeError(
                f"Sandbox {sandbox_id} setup is stale. The first_boot_commands were changed "
                "but not applied. Recreate the sandbox to apply them."
            )

    async def wait_until_ready(self, poll_interval_ms: float | None = None) -> None:
        """Wait until the sandbox setup is complete."""
        sandbox_id = self.initialized_id

        async with error_handler("Sandbox not ready"):

            async def check() -> bool:
                await self.refresh()

                if self.data.setup_status == "SUCCESS":
                    return True

                self._assert_setup_not_failed(sandbox_id)

                return False

            await poll_until(check, **resolve_poll_interval(poll_interval_ms))

    async def wait_until_running(
        self, poll_interval_ms: float | None = None, max_wait_ms: float = 60_000
    ) -> None:
        """Wait until the sandbox is in the RUNNING state."""
        await self._wait_until_status("RUNNING", poll_interval_ms, max_wait_ms)

    async def wait_until_stopped(
        self, poll_interval_ms: float | None = None, max_wait_ms: float = 60_000
    ) -> None:
        """Wait until the sandbox is in the STOPPED state."""
        await self._wait_until_status("STOPPED", poll_interval_ms, max_wait_ms)

    async def _wait_until_status(
        self, target: str, poll_interval_ms: float | None, max_wait_ms: float
    ) -> None:
        sandbox_id = self.initialized_id

        async with error_handler(f"Sandbox not {target.lower()}"):

            async def check() -> bool:
                await self.refresh()

                if self.data.status == target:
                    return True

                if self.data.status == "FAILED":
                    raise RuntimeError(f"Sandbox {sandbox_id} failed. Status: {self.data.status}")

                return False

            await poll_until(
                check,
                **resolve_poll_interval(poll_interval_ms),
                max_wait_ms=max_wait_ms,
                on_timeout=lambda: TimeoutError(
                    f"Timeout waiting for sandbox {sandbox_id} to be {target}. "
                    f"Current: {self.data.status}"
                ),
            )

    async def start(self) -> None:
        """Start a stopped sandbox, returning at once if it already runs."""
        sandbox_id = self.initialized_id

        async with error_handler("Failed to start sandbox"):
            await self.refresh()

            if self.data.setup_status == "SUCCESS" and self.data.status == "RUNNING":
                logger.debug(f"Sandbox {sandbox_id} is already running.")
                return

            logger.debug(f"Starting sandbox {sandbox_id}...")

            self._sandbox_data = await self._client.start_sandbox(path={"sandbox_id": sandbox_id})

            await self.wait_until_ready()
            logger.debug(f"Sandbox {sandbox_id} is ready (Setup status: {self.data.setup_status})")

            await self.wait_until_running()
            logger.debug(f"Sandbox {sandbox_id} is now running. Status: {self.data.status}")

    async def stop(self) -> None:
        """Stop a running sandbox, returning at once if it is already stopped."""
        sandbox_id = self.initialized_id

        async with error_handler("Failed to stop sandbox"):
            await self.refresh()

            if self.data.status == "STOPPED":
                logger.debug(f"Sandbox {sandbox_id} is already stopped.")
                return

            logger.debug(f"Stopping sandbox {sandbox_id}...")

            self._sandbox_data = await self._client.stop_sandbox(path={"sandbox_id": sandbox_id})

            await self.wait_until_stopped()
            logger.debug(f"Sandbox {sandbox_id} is now stopped. Status: {self.data.status}")

    async def restart(self) -> None:
        """Restart the sandbox and wait for it to run again."""
        sandbox_id = self.initialized_id

        async with error_handler("Failed to restart sandbox"):
            logger.debug(f"Restarting sandbox {sandbox_id}...")

            self._sandbox_data = await self._client.restart_sandbox(path={"sandbox_id": sandbox_id})

            await self.wait_until_running()
            await self.wait_until_ready()

            logger.debug(
                f"Sandbox {sandbox_id} has been restarted and is ready. "
                f"Status: {self.data.status}, Setup status: {self.data.setup_status}"
            )

    # ------------------------------------------------------------------
    # Apps
    # ------------------------------------------------------------------

    async def start_app(self, app_id: str) -> None:
        """Start a specific app within the sandbox."""
        sandbox_id = self.initialized_id

        async with error_handler("Failed to start app"):
            self._sandbox_data = await self._client.start_sandbox_app(
                path={"sandbox_id": sandbox_id, "app_id": app_id}
            )

    async def stop_app(self, app_id: str) -> None:
        """Stop a specific app within the sandbox."""
        sandbox_id = self.initialized_id

        async with error_handler("Failed to stop app"):
            self._sandbox_data = await self._client.stop_sandbox_app(
                path={"sandbox_id": sandbox_id, "app_id": app_id}
            )

    async def get_app_logs(self, app_id: str, cursor: str | None = None) -> SandboxAppLogsView:
        """Get a page of logs for a specific app, plus a cursor for the next one."""
        sandbox_id = self.initialized_id

        async with error_handler("Failed to get app logs"):
            return await self._client.get_sandbox_app_logs(
                path={"sandbox_id": sandbox_id, "app_id": app_id}, query={"cursor": cursor}
            )


def _resolve_stream(argument: StreamArg, default: TextIO) -> TextIO | None:
    return default if isinstance(argument, _Default) else argument


async def _pump_logs(command: Command, out: TextIO | None, err: TextIO | None) -> None:
    """Copy a command's log stream to the given streams as it arrives."""
    async with aclosing(command.logs(follow=True)) as entries:
        async for log in entries:
            text = f"{log.data or ''}\n"

            if log.type == "STDOUT" and out is not None:
                out.write(text)
                out.flush()
            elif log.type == "STDERR" and err is not None:
                err.write(text)
                err.flush()
