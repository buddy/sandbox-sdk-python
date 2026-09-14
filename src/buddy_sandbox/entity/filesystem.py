"""File system operations inside a sandbox."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from buddy_sandbox.api.openapi.pydantic_gen import SandboxContentItem
from buddy_sandbox.errors import error_handler
from buddy_sandbox.utils.client import ConnectionConfig, create_client

if TYPE_CHECKING:
    from buddy_sandbox.core.buddy_api_client import BuddyApiClient


@dataclass(slots=True)
class FileInfo:
    """File information returned by file system operations."""

    name: str
    """File or directory name."""

    path: str
    """Full path to the file or directory."""

    type: str
    """Either ``"FILE"`` or ``"DIR"``."""

    size: int | None = None
    """Size in bytes (for files)."""

    url: str | None = None
    """API URL for this item."""

    html_url: str | None = None
    """Web URL for viewing in Buddy.works."""


class FileSystem:
    """File system operations within a sandbox.

    Instances come either from :meth:`FileSystem.for_sandbox` or from the
    :attr:`Sandbox.fs` property.
    """

    def __init__(self, client: BuddyApiClient, sandbox_id: str) -> None:
        self._client = client
        self._sandbox_id = sandbox_id

    @classmethod
    def for_sandbox(
        cls, sandbox_id: str, *, connection: ConnectionConfig | None = None
    ) -> FileSystem:
        """Create a FileSystem instance for a specific sandbox."""
        return cls(create_client(connection), sandbox_id)

    async def list_files(self, dir_path: str) -> list[FileInfo]:
        """List the contents of a directory in the sandbox.

        Relative paths are resolved against the sandbox working directory.
        """
        async with error_handler("Failed to list files"):
            response = await self._client.get_sandbox_content(
                path={"sandbox_id": self._sandbox_id, "path": _normalize_path(dir_path)}
            )

            return [_to_file_info(item) for item in response.contents or []]

    async def create_folder(self, dir_path: str) -> None:
        """Create a new directory in the sandbox."""
        async with error_handler("Failed to create folder"):
            await self._client.create_sandbox_directory(
                path={"sandbox_id": self._sandbox_id, "path": _normalize_path(dir_path)}
            )

    async def delete_file(self, file_path: str) -> None:
        """Delete a file or directory from the sandbox."""
        async with error_handler("Failed to delete file"):
            await self._client.delete_sandbox_file(
                path={"sandbox_id": self._sandbox_id, "path": _normalize_path(file_path)}
            )

    async def download_file(self, remote_path: str, local_path: str | Path | None = None) -> bytes:
        """Download a file from the sandbox, optionally saving it locally."""
        async with error_handler("Failed to download file"):
            content, _ = await self._client.download_sandbox_content(
                path={"sandbox_id": self._sandbox_id, "path": _normalize_path(remote_path)}
            )

            if local_path is not None:
                await asyncio.to_thread(Path(local_path).write_bytes, content)

            return content

    async def upload_file(self, source: bytes | str | Path, remote_path: str) -> None:
        """Upload a file to the sandbox from bytes or a local path."""
        async with error_handler("Failed to upload file"):
            content = (
                source
                if isinstance(source, bytes)
                else await asyncio.to_thread(Path(source).read_bytes)
            )

            await self._client.upload_sandbox_file(
                body=content,
                path={"sandbox_id": self._sandbox_id, "path": _normalize_path(remote_path)},
            )


def _to_file_info(item: SandboxContentItem) -> FileInfo:
    return FileInfo(
        name=item.name or "",
        path=item.path or "",
        type=item.type or "FILE",
        size=item.size,
        url=item.url,
        html_url=item.html_url,
    )


def _normalize_path(file_path: str) -> str:
    """Strip the leading slash - the API expects ``buddy/src``, not ``/buddy/src``."""
    return file_path[1:] if file_path.startswith("/") else file_path
