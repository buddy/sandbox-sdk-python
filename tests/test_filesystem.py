"""The FileSystem entity against a live sandbox."""

from __future__ import annotations

import contextlib
import time
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from buddy_sandbox import FileSystem, Sandbox
from tests.shared.naming import sandbox_identifier, sandbox_name

pytestmark = pytest.mark.integration


def unique(suffix: str) -> str:
    return f"{suffix}_{int(time.time() * 1000)}"


@pytest.fixture(scope="module")
async def sandbox() -> AsyncIterator[Sandbox]:
    instance = await Sandbox.create(
        name=sandbox_name("filesystem-test"), identifier=sandbox_identifier("filesystem_test")
    )
    await instance.wait_until_running()

    try:
        yield instance
    finally:
        with contextlib.suppress(Exception):
            await instance.destroy()


async def names_in(sandbox: Sandbox, path: str) -> list[str]:
    return [entry.name for entry in await sandbox.fs.list_files(path)]


class TestForSandbox:
    def test_creates_an_instance_from_a_sandbox_id(self, sandbox: Sandbox) -> None:
        assert isinstance(FileSystem.for_sandbox(sandbox.initialized_id), FileSystem)

    async def test_the_created_instance_works(self, sandbox: Sandbox) -> None:
        filesystem = FileSystem.for_sandbox(sandbox.initialized_id)

        assert isinstance(await filesystem.list_files("/"), list)


class TestFsProperty:
    def test_returns_a_filesystem(self, sandbox: Sandbox) -> None:
        assert isinstance(sandbox.fs, FileSystem)

    def test_returns_the_same_instance_every_time(self, sandbox: Sandbox) -> None:
        assert sandbox.fs is sandbox.fs


class TestListFiles:
    async def test_lists_the_root_directory(self, sandbox: Sandbox) -> None:
        assert isinstance(await sandbox.fs.list_files("/"), list)

    async def test_returns_entries_carrying_the_required_properties(self, sandbox: Sandbox) -> None:
        entries = await sandbox.fs.list_files("/")

        for entry in entries:
            assert entry.name is not None
            assert entry.path is not None
            assert entry.type in ("FILE", "DIR")

    async def test_handles_paths_without_a_leading_slash(self, sandbox: Sandbox) -> None:
        await sandbox.fs.create_folder("/listtest")

        try:
            assert isinstance(await sandbox.fs.list_files("listtest"), list)
        finally:
            await sandbox.fs.delete_file("/listtest")


class TestCreateFolder:
    async def test_creates_a_new_directory(self, sandbox: Sandbox) -> None:
        name = unique("testdir")

        await sandbox.fs.create_folder(name)

        try:
            entries = {entry.name: entry for entry in await sandbox.fs.list_files("/")}
            assert entries[name].type == "DIR"
        finally:
            await sandbox.fs.delete_file(name)

    async def test_creates_nested_directories(self, sandbox: Sandbox) -> None:
        parent = unique("parent")
        child = f"{parent}/child"

        await sandbox.fs.create_folder(parent)
        await sandbox.fs.create_folder(child)

        try:
            entries = {entry.name: entry for entry in await sandbox.fs.list_files(parent)}
            assert entries["child"].type == "DIR"
        finally:
            await sandbox.fs.delete_file(child)
            await sandbox.fs.delete_file(parent)


class TestUploadFile:
    async def test_uploads_from_bytes(self, sandbox: Sandbox) -> None:
        name = f"{unique('buffer_upload')}.txt"
        content = b"Hello from bytes!"

        await sandbox.fs.upload_file(content, name)

        try:
            entries = {entry.name: entry for entry in await sandbox.fs.list_files("/")}
            assert entries[name].type == "FILE"
            assert await sandbox.fs.download_file(name) == content
        finally:
            await sandbox.fs.delete_file(name)

    async def test_uploads_from_a_local_file_path(self, sandbox: Sandbox, tmp_path: Path) -> None:
        local = tmp_path / "local_upload.txt"
        remote = f"{unique('remote_upload')}.txt"
        content = b"Hello from a local file!"
        local.write_bytes(content)

        await sandbox.fs.upload_file(local, remote)

        try:
            assert await sandbox.fs.download_file(remote) == content
        finally:
            await sandbox.fs.delete_file(remote)

    async def test_uploads_binary_content(self, sandbox: Sandbox) -> None:
        name = f"{unique('binary')}.bin"
        content = bytes([0x00, 0x01, 0x02, 0xFF, 0xFE, 0xFD])

        await sandbox.fs.upload_file(content, name)

        try:
            assert await sandbox.fs.download_file(name) == content
        finally:
            await sandbox.fs.delete_file(name)


class TestDownloadFile:
    CONTENT = b"Download test content"

    @pytest.fixture
    async def uploaded(self, sandbox: Sandbox) -> AsyncIterator[str]:
        name = f"{unique('download_test')}.txt"
        await sandbox.fs.upload_file(self.CONTENT, name)

        try:
            yield name
        finally:
            with contextlib.suppress(Exception):
                await sandbox.fs.delete_file(name)

    async def test_downloads_a_file_as_bytes(self, sandbox: Sandbox, uploaded: str) -> None:
        assert await sandbox.fs.download_file(uploaded) == self.CONTENT

    async def test_saves_to_a_local_path_when_one_is_given(
        self, sandbox: Sandbox, uploaded: str, tmp_path: Path
    ) -> None:
        local = tmp_path / "downloaded.txt"

        content = await sandbox.fs.download_file(uploaded, local)

        assert content == self.CONTENT
        assert local.read_bytes() == self.CONTENT

    async def test_downloads_a_directory_as_a_gzip_archive(self, sandbox: Sandbox) -> None:
        name = unique("download_dir")
        await sandbox.fs.create_folder(name)
        await sandbox.fs.upload_file(b"inside-dir-1", f"{name}/file1.txt")
        await sandbox.fs.upload_file(b"inside-dir-2", f"{name}/file2.txt")

        try:
            content = await sandbox.fs.download_file(name)

            assert len(content) > 0
            # The API serves directories as gzip.
            assert content[:2] == b"\x1f\x8b"
        finally:
            with contextlib.suppress(Exception):
                await sandbox.fs.delete_file(name)


class TestDeleteFile:
    async def test_deletes_a_file(self, sandbox: Sandbox) -> None:
        name = f"{unique('to_delete')}.txt"
        await sandbox.fs.upload_file(b"delete me", name)
        assert name in await names_in(sandbox, "/")

        await sandbox.fs.delete_file(name)

        assert name not in await names_in(sandbox, "/")

    async def test_deletes_a_directory(self, sandbox: Sandbox) -> None:
        name = unique("dir_to_delete")
        await sandbox.fs.create_folder(name)
        assert name in await names_in(sandbox, "/")

        await sandbox.fs.delete_file(name)

        assert name not in await names_in(sandbox, "/")


class TestPathNormalization:
    @pytest.mark.parametrize("prefix", ["/", ""])
    async def test_handles_paths_with_and_without_a_leading_slash(
        self, sandbox: Sandbox, prefix: str
    ) -> None:
        name = unique("slash_test")

        await sandbox.fs.create_folder(f"{prefix}{name}")

        try:
            assert name in await names_in(sandbox, "/")
        finally:
            await sandbox.fs.delete_file(f"{prefix}{name}")
