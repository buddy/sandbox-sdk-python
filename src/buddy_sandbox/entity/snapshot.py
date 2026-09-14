"""A point-in-time snapshot of a sandbox."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from buddy_sandbox.api.openapi.pydantic_gen import SnapshotView
from buddy_sandbox.errors import error_handler
from buddy_sandbox.utils.poll import poll_until, resolve_poll_interval

if TYPE_CHECKING:
    from buddy_sandbox.core.buddy_api_client import BuddyApiClient

SNAPSHOT_MAX_INTERVAL_MS: Final = 2000

_PRIVATE_CONSTRUCTOR_KEY: Final = object()

INITIALIZE_INSTRUCTIONS: Final = (
    "Snapshots are obtained via sandbox.create_snapshot(), sandbox.list_snapshots(), "
    "sandbox.get_snapshot(id), or Sandbox.get_snapshot_by_id(sandbox_id, snapshot_id)."
)


class Snapshot:
    """A snapshot of a sandbox, obtained through the :class:`Sandbox` methods."""

    def __init__(
        self,
        data: SnapshotView,
        client: BuddyApiClient,
        sandbox_id: str,
        constructor_key: Any = None,
    ) -> None:
        if constructor_key is not _PRIVATE_CONSTRUCTOR_KEY:
            raise RuntimeError(f"Cannot construct Snapshot directly. {INITIALIZE_INSTRUCTIONS}")

        self._data = data
        self._client = client
        self._sandbox_id = sandbox_id

    @classmethod
    def _build(cls, data: SnapshotView, client: BuddyApiClient, sandbox_id: str) -> Snapshot:
        """Factory used by the Sandbox class to construct Snapshot instances."""
        return cls(data, client, sandbox_id, _PRIVATE_CONSTRUCTOR_KEY)

    @property
    def data(self) -> SnapshotView:
        """The raw snapshot response data from the API."""
        return self._data

    @property
    def id(self) -> str:
        """The snapshot ID, raising if it is not present."""
        snapshot_id = self._data.id
        if not snapshot_id:
            raise RuntimeError(f"Snapshot ID is missing. {INITIALIZE_INSTRUCTIONS}")
        return snapshot_id

    @property
    def sandbox_id(self) -> str:
        """ID of the sandbox this snapshot belongs to."""
        return self._sandbox_id

    async def refresh(self) -> None:
        """Refresh the snapshot data from the API."""
        async with error_handler("Failed to refresh snapshot"):
            self._data = await self._client.get_sandbox_snapshot(
                path={"sandbox_id": self._sandbox_id, "id": self.id}
            )

    async def wait_until_ready(
        self, poll_interval_ms: float | None = None, max_wait_ms: float = 180_000
    ) -> None:
        """Wait until the snapshot reaches the CREATED state.

        ``sandbox.create_snapshot()`` returns immediately with
        ``status: "CREATING"``. The snapshot cannot be restored until it
        transitions to ``"CREATED"``.
        """
        async with error_handler("Snapshot not ready"):

            async def check() -> bool:
                await self.refresh()

                if self._data.status == "CREATED":
                    return True

                if self._data.status == "FAILED":
                    raise RuntimeError(f"Snapshot {self.id} failed. Status: {self._data.status}")

                return False

            await poll_until(
                check,
                **resolve_poll_interval(poll_interval_ms, SNAPSHOT_MAX_INTERVAL_MS),
                max_wait_ms=max_wait_ms,
                on_timeout=lambda: TimeoutError(
                    f"Timeout waiting for snapshot {self.id} to be CREATED. "
                    f"Current: {self._data.status}"
                ),
            )

    async def delete(self) -> None:
        """Delete this snapshot permanently."""
        async with error_handler("Failed to delete snapshot"):
            await self._client.delete_sandbox_snapshot(
                path={"sandbox_id": self._sandbox_id, "id": self.id}
            )
