"""The Sandbox entity against the live Buddy API.

Needs BUDDY_WORKSPACE, BUDDY_PROJECT and BUDDY_TOKEN. The scope suites at the
bottom additionally need the BUDDY_TEST_* variables.
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import AsyncIterator

import pytest

from buddy_sandbox import BuddySDKError, Sandbox, SandboxAppView
from tests.shared.naming import sandbox_identifier, sandbox_name
from tests.shared.scope import (
    project_environment,
    project_environment_connection,
    workspace_connection,
    workspace_environment,
    workspace_environment_connection,
)

pytestmark = pytest.mark.integration

SANDBOX_NAME = sandbox_name()


@pytest.fixture(scope="module")
async def sandbox() -> AsyncIterator[Sandbox]:
    instance = await Sandbox.create(name=SANDBOX_NAME, identifier=sandbox_identifier())
    await instance.wait_until_running()

    try:
        yield instance
    finally:
        with contextlib.suppress(Exception):
            await instance.destroy()


class TestDataProperties:
    def test_has_an_id(self, sandbox: Sandbox) -> None:
        assert isinstance(sandbox.data.id, str)

    def test_has_an_identifier(self, sandbox: Sandbox) -> None:
        assert isinstance(sandbox.data.identifier, str)

    def test_has_the_name_it_was_created_with(self, sandbox: Sandbox) -> None:
        assert sandbox.data.name == SANDBOX_NAME

    def test_has_a_known_status(self, sandbox: Sandbox) -> None:
        assert sandbox.data.status in (
            "STARTING",
            "RUNNING",
            "STOPPING",
            "STOPPED",
            "FAILED",
            "RESTORING",
        )

    def test_has_an_os(self, sandbox: Sandbox) -> None:
        assert "ubuntu" in (sandbox.data.os or "")

    def test_has_a_setup_status(self, sandbox: Sandbox) -> None:
        assert sandbox.data.setup_status in ("INPROGRESS", "SUCCESS", "FAILED")

    def test_has_a_url_and_an_html_url(self, sandbox: Sandbox) -> None:
        assert sandbox.data.url is not None
        assert sandbox.data.html_url is not None


class TestLifecycle:
    async def test_gets_a_sandbox_by_id(self, sandbox: Sandbox) -> None:
        fetched = await Sandbox.get_by_id(sandbox.initialized_id)

        assert fetched.data.id == sandbox.data.id

    async def test_gets_a_sandbox_by_identifier(self, sandbox: Sandbox) -> None:
        identifier = sandbox.data.identifier
        assert identifier is not None

        fetched = await Sandbox.get_by_identifier(identifier)

        assert fetched.data.id == sandbox.data.id
        assert fetched.data.identifier == identifier

    async def test_raises_when_the_identifier_is_not_found(self) -> None:
        with pytest.raises(BuddySDKError, match="not found"):
            await Sandbox.get_by_identifier("non_existent_identifier_12345")

    async def test_lists_sandboxes(self, sandbox: Sandbox) -> None:
        sandboxes = await Sandbox.list()

        assert any(entry.id == sandbox.data.id for entry in sandboxes)

    async def test_refreshes_the_sandbox_data(self, sandbox: Sandbox) -> None:
        sandbox_id = sandbox.data.id

        await sandbox.refresh()

        assert sandbox.data.id == sandbox_id


class TestCommands:
    async def test_runs_a_command_and_gets_its_result(self, sandbox: Sandbox) -> None:
        command = await sandbox.run_command(command="echo 'hello world'", stdout=None, stderr=None)

        assert command.data.id is not None

        finished = await command.wait()

        assert finished.data.status == "SUCCESSFUL"
        assert finished.data.exit_code == 0

    async def test_runs_a_detached_command(self, sandbox: Sandbox) -> None:
        command = await sandbox.run_command(
            command="sleep 1 && echo 'done'", stdout=None, stderr=None, detached=True
        )

        # Detached returns immediately, before the command has finished.
        assert command.data.id is not None
        assert command.data.status == "INPROGRESS"

        finished = await command.wait()

        assert finished.data.status == "SUCCESSFUL"

    async def test_captures_stdout(self, sandbox: Sandbox) -> None:
        command = await sandbox.run_command(command="echo 'test output'", stdout=None, stderr=None)

        assert "test output" in await command.stdout()

    async def test_captures_stderr(self, sandbox: Sandbox) -> None:
        command = await sandbox.run_command(
            command="echo 'error message' >&2", stdout=None, stderr=None
        )

        assert "error message" in await command.stderr()

    async def test_captures_both_streams(self, sandbox: Sandbox) -> None:
        command = await sandbox.run_command(
            command="echo 'stdout' && echo 'stderr' >&2", stdout=None, stderr=None
        )

        both = await command.output("BOTH")

        assert "stdout" in both
        assert "stderr" in both

        other = await sandbox.run_command(
            command="echo 'out' && echo 'err' >&2", stdout=None, stderr=None
        )

        stdout_only = await other.output("STDOUT")

        assert "out" in stdout_only
        assert "err" not in stdout_only

    async def test_handles_a_failed_command(self, sandbox: Sandbox) -> None:
        command = await sandbox.run_command(command="exit 1", stdout=None, stderr=None)

        finished = await command.wait()

        assert finished.data.status == "FAILED"
        assert finished.data.exit_code == 1

    async def test_handles_an_arbitrary_exit_code(self, sandbox: Sandbox) -> None:
        command = await sandbox.run_command(command="exit 42", stdout=None, stderr=None)

        finished = await command.wait()

        assert finished.data.exit_code == 42

    async def test_streams_logs(self, sandbox: Sandbox) -> None:
        command = await sandbox.run_command(
            command="echo 'line1' && echo 'line2'", stdout=None, stderr=None
        )

        logs = [log.data async for log in command.logs(follow=True) if log.data]

        assert any("line1" in line for line in logs)
        assert any("line2" in line for line in logs)

    async def test_kills_a_running_command(self, sandbox: Sandbox) -> None:
        command = await sandbox.run_command(
            command="sleep 60", stdout=None, stderr=None, detached=True
        )

        assert command.data.status == "INPROGRESS"

        await command.kill()
        finished = await command.wait()

        assert finished.data.status == "FAILED"

    async def test_lists_the_command_history(self, sandbox: Sandbox) -> None:
        marker = f"echo list-commands-marker-{int(time.time() * 1000)}"

        command = await sandbox.run_command(command=marker, stdout=None, stderr=None)
        await command.wait()

        history = await sandbox.list_commands()

        assert any(entry.data.command == marker for entry in history)


class TestFileSystem:
    DIR = "test-dir"
    FILE = "test-file.txt"
    CONTENT = b"Hello from integration test!"

    async def test_lists_files_in_the_root_directory(self, sandbox: Sandbox) -> None:
        assert isinstance(await sandbox.fs.list_files("/"), list)

    async def test_creates_a_folder(self, sandbox: Sandbox) -> None:
        await sandbox.fs.create_folder(self.DIR)

        entries = {entry.name: entry for entry in await sandbox.fs.list_files("/")}

        assert entries[self.DIR].type == "DIR"

    async def test_uploads_a_file(self, sandbox: Sandbox) -> None:
        await sandbox.fs.upload_file(self.CONTENT, f"{self.DIR}/{self.FILE}")

        entries = {entry.name: entry for entry in await sandbox.fs.list_files(self.DIR)}

        assert entries[self.FILE].type == "FILE"

    async def test_downloads_a_file(self, sandbox: Sandbox) -> None:
        assert await sandbox.fs.download_file(f"{self.DIR}/{self.FILE}") == self.CONTENT

    async def test_deletes_a_file(self, sandbox: Sandbox) -> None:
        await sandbox.fs.delete_file(f"{self.DIR}/{self.FILE}")

        names = [entry.name for entry in await sandbox.fs.list_files(self.DIR)]

        assert self.FILE not in names

    async def test_deletes_a_folder(self, sandbox: Sandbox) -> None:
        await sandbox.fs.delete_file(self.DIR)

        names = [entry.name for entry in await sandbox.fs.list_files("/")]

        assert self.DIR not in names


@pytest.fixture(scope="module")
async def app_sandbox() -> AsyncIterator[Sandbox]:
    instance = await Sandbox.create(
        name=sandbox_name("apps"),
        identifier=sandbox_identifier("apps"),
        apps=["echo 'app1 running' && sleep 3600", "echo 'app2 running' && sleep 3600"],
    )
    await instance.wait_until_running()

    try:
        yield instance
    finally:
        with contextlib.suppress(Exception):
            await instance.destroy()


def apps_of(sandbox: Sandbox) -> list[SandboxAppView]:
    apps = sandbox.data.apps or []
    if len(apps) < 2:
        raise AssertionError("Expected at least 2 apps")
    return apps


def app_id(sandbox: Sandbox, index: int) -> str:
    identifier = apps_of(sandbox)[index].id
    if not identifier:
        raise AssertionError(f"App at index {index} has no id")
    return identifier


class TestApps:
    def test_reports_every_app(self, app_sandbox: Sandbox) -> None:
        apps = apps_of(app_sandbox)

        assert len(apps) == 2
        for app in apps:
            assert app.id is not None
            assert app.command is not None
            assert app.app_status in ("NONE", "RUNNING", "ENDED", "FAILED")

    def test_gives_each_app_a_distinct_id_and_command(self, app_sandbox: Sandbox) -> None:
        apps = apps_of(app_sandbox)

        assert len({app.id for app in apps}) == 2
        assert len({app.command for app in apps}) == 2

    async def test_stops_one_app_without_affecting_the_other(self, app_sandbox: Sandbox) -> None:
        first, second = app_id(app_sandbox, 0), app_id(app_sandbox, 1)

        await app_sandbox.stop_app(first)

        statuses = {app.id: app.app_status for app in app_sandbox.data.apps or []}

        assert statuses[first] in ("ENDED", "NONE")
        assert statuses[second] == "RUNNING"

    async def test_starts_a_stopped_app(self, app_sandbox: Sandbox) -> None:
        identifier = app_id(app_sandbox, 0)

        await app_sandbox.start_app(identifier)

        statuses = {app.id: app.app_status for app in app_sandbox.data.apps or []}

        assert statuses[identifier] == "RUNNING"

    async def test_gets_app_logs(self, app_sandbox: Sandbox) -> None:
        logs = await app_sandbox.get_app_logs(app_id(app_sandbox, 0))

        assert isinstance(logs.logs, list)


class TestStateManagement:
    async def test_stops_the_sandbox(self, sandbox: Sandbox) -> None:
        await sandbox.stop()
        await sandbox.wait_until_stopped()
        await sandbox.refresh()

        assert sandbox.data.status == "STOPPED"

    async def test_starts_the_sandbox(self, sandbox: Sandbox) -> None:
        await sandbox.start()
        await sandbox.wait_until_running()
        await sandbox.refresh()

        assert sandbox.data.status == "RUNNING"

    async def test_restarts_the_sandbox(self, sandbox: Sandbox) -> None:
        await sandbox.restart()
        await sandbox.wait_until_running()
        await sandbox.refresh()

        assert sandbox.data.status == "RUNNING"

    async def test_start_on_a_running_sandbox_is_a_no_op(self, sandbox: Sandbox) -> None:
        await sandbox.refresh()
        assert sandbox.data.status == "RUNNING"

        await sandbox.start()

        assert sandbox.data.status == "RUNNING"

    async def test_stop_on_a_stopped_sandbox_is_a_no_op(self, sandbox: Sandbox) -> None:
        await sandbox.stop()
        await sandbox.wait_until_stopped()
        assert sandbox.data.status == "STOPPED"

        await sandbox.stop()

        assert sandbox.data.status == "STOPPED"

        # Leave it running for whatever comes next.
        await sandbox.start()
        await sandbox.wait_until_running()


class TestUpdate:
    async def test_updates_timeout_and_tags_in_place(self, sandbox: Sandbox) -> None:
        await sandbox.update(timeout=1200, tags=["updated"])

        assert sandbox.data.timeout == 1200
        assert "updated" in (sandbox.data.tags or [])


class TestSnapshots:
    async def test_creates_lists_gets_and_deletes_a_snapshot(self, sandbox: Sandbox) -> None:
        snapshot = await sandbox.create_snapshot(name=f"test-snap-{int(time.time() * 1000)}")

        listed = await sandbox.list_snapshots()
        assert any(entry.id == snapshot.id for entry in listed)

        fetched = await sandbox.get_snapshot(snapshot.id)
        assert fetched.id == snapshot.id

        await sandbox.delete_snapshot(snapshot.id)

        after_delete = await sandbox.list_snapshots()
        assert not any(entry.id == snapshot.id for entry in after_delete)


class TestCreateFromSnapshot:
    async def test_restores_a_sandbox_carrying_the_snapshot_contents(self) -> None:
        marker_name = f"snapshot_marker_{int(time.time() * 1000)}.txt"
        marker_content = f"restored at {int(time.time() * 1000)}".encode()

        base = await Sandbox.create(
            name=sandbox_name("snapshot-base"), identifier=sandbox_identifier("snapshot_base")
        )
        restored: Sandbox | None = None

        try:
            await base.fs.upload_file(marker_content, marker_name)
            snapshot = await base.create_snapshot(name=f"test-base-snap-{int(time.time() * 1000)}")
            await base.wait_for_snapshot_ready(snapshot.id)

            name = sandbox_name("snapshot-restored")
            identifier = sandbox_identifier("snapshot_restored")
            restored = await Sandbox.create_from_snapshot(
                snapshot.id, name=name, identifier=identifier
            )

            assert restored.data.status == "RUNNING"
            assert restored.data.name == name
            assert restored.data.identifier == identifier
            assert await restored.fs.download_file(marker_name) == marker_content

            assert any(entry.id == snapshot.id for entry in await Sandbox.list_snapshots())

            await Sandbox.delete_snapshot(snapshot.id)

            assert not any(entry.id == snapshot.id for entry in await Sandbox.list_snapshots())
        finally:
            for instance in (restored, base):
                if instance is not None:
                    with contextlib.suppress(Exception):
                        await instance.destroy()


class TestClone:
    async def test_clones_a_sandbox_under_a_new_name_and_identifier(self) -> None:
        source = await Sandbox.create(
            name=sandbox_name("clone-source"), identifier=sandbox_identifier("clone_source")
        )
        clone: Sandbox | None = None

        try:
            name = sandbox_name("clone-target")
            identifier = sandbox_identifier("clone_target")

            clone = await Sandbox.clone(source.initialized_id, name=name, identifier=identifier)

            assert clone.data.id != source.data.id
            assert clone.data.status == "RUNNING"
            assert clone.data.name == name
            assert clone.data.identifier == identifier
        finally:
            for instance in (clone, source):
                if instance is not None:
                    with contextlib.suppress(Exception):
                        await instance.destroy()


@pytest.mark.skipif(workspace_connection is None, reason="BUDDY_TEST_WORKSPACE is not set")
class TestWorkspaceScope:
    async def test_creates_the_sandbox_outside_of_any_project(self) -> None:
        sandbox = await Sandbox.create(
            name=sandbox_name("workspace-scope"),
            identifier=sandbox_identifier("workspace_scope"),
            connection=workspace_connection,
        )

        try:
            assert sandbox.data.scope == "WORKSPACE"
            assert sandbox.data.project is None
        finally:
            with contextlib.suppress(Exception):
                await sandbox.destroy()


@pytest.mark.skipif(
    workspace_environment_connection is None, reason="BUDDY_TEST_ENVIRONMENT is not set"
)
class TestWorkspaceEnvironmentScope:
    async def test_creates_the_sandbox_in_the_environment_not_the_workspace(self) -> None:
        sandbox = await Sandbox.create(
            name=sandbox_name("workspace-environment"),
            identifier=sandbox_identifier("workspace_environment"),
            connection=workspace_environment_connection,
        )

        try:
            assert sandbox.data.scope == "ENVIRONMENT"
            assert sandbox.data.environment is not None
            assert sandbox.data.environment.identifier == workspace_environment
            assert isinstance(sandbox.data.environment.id, str)
            assert sandbox.data.project is None
        finally:
            with contextlib.suppress(Exception):
                await sandbox.destroy()


@pytest.mark.skipif(
    project_environment_connection is None,
    reason="BUDDY_TEST_PROJECT_ENVIRONMENT is not set",
)
class TestProjectEnvironmentScope:
    async def test_creates_the_sandbox_in_the_environment_not_its_project(self) -> None:
        sandbox = await Sandbox.create(
            name=sandbox_name("project-environment"),
            identifier=sandbox_identifier("project_environment"),
            connection=project_environment_connection,
        )

        try:
            assert sandbox.data.scope == "ENVIRONMENT"
            assert sandbox.data.environment is not None
            assert sandbox.data.environment.identifier == project_environment
            assert sandbox.data.project is None
        finally:
            with contextlib.suppress(Exception):
                await sandbox.destroy()
