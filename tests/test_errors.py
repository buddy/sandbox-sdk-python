"""Error handling and edge cases, mostly without creating real sandboxes."""

from __future__ import annotations

import contextlib

import pytest

from buddy_sandbox import BuddySDKError, FileSystem, Sandbox
from tests.shared.naming import sandbox_identifier, sandbox_name

pytestmark = pytest.mark.integration


def test_does_not_allow_direct_sandbox_construction() -> None:
    with pytest.raises(RuntimeError, match="Cannot construct Sandbox directly"):
        Sandbox(None, None, object())  # type: ignore[arg-type]


class TestGetById:
    async def test_raises_for_a_non_existent_sandbox_id(self) -> None:
        with pytest.raises(BuddySDKError):
            await Sandbox.get_by_id("non-existent-id-12345")


class TestList:
    async def test_returns_a_list_without_raising(self) -> None:
        assert isinstance(await Sandbox.list(), list)


class TestFileSystemForSandbox:
    def test_creates_an_instance_for_any_sandbox_id(self) -> None:
        assert FileSystem.for_sandbox("test-sandbox-id") is not None

    async def test_fails_when_listing_files_for_a_non_existent_sandbox(self) -> None:
        filesystem = FileSystem.for_sandbox("non-existent-sandbox-id")

        with pytest.raises(BuddySDKError):
            await filesystem.list_files("/")


class TestCreateFromSnapshot:
    async def test_raises_when_the_snapshot_id_does_not_exist(self) -> None:
        with pytest.raises(BuddySDKError):
            await Sandbox.create_from_snapshot(
                "non-existent-snapshot-id-12345",
                name=sandbox_name("restore-fail"),
                identifier=sandbox_identifier("restore_fail"),
            )


class TestDestroyedSandbox:
    async def test_update_raises_on_a_deleted_sandbox(self) -> None:
        sandbox = await Sandbox.create(
            name=sandbox_name("error"), identifier=sandbox_identifier("error")
        )
        await sandbox.destroy()

        with pytest.raises(BuddySDKError):
            await sandbox.update(timeout=600)

    async def test_get_snapshot_raises_for_a_non_existent_snapshot_id(self) -> None:
        # A fresh sandbox: a destroyed one would fail at path resolution before
        # ever reaching the snapshot lookup.
        sandbox = await Sandbox.create(
            name=sandbox_name("snapshot-404"), identifier=sandbox_identifier("snapshot_404")
        )

        try:
            with pytest.raises(BuddySDKError):
                await sandbox.get_snapshot("non-existent-snapshot-id-12345")
        finally:
            with contextlib.suppress(Exception):
                await sandbox.destroy()
