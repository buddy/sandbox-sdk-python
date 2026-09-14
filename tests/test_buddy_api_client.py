"""Every endpoint the API client exposes, against a mocked Buddy API."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx

from buddy_sandbox.api.openapi.pydantic_gen import SandboxCommandView
from buddy_sandbox.core.buddy_api_client import BuddyApiClient
from buddy_sandbox.core.http_client import HttpError
from tests.shared.api import (
    SANDBOXES_URL,
    TEST_API_URL,
    TEST_PROJECT,
    TEST_WORKSPACE,
    build_client,
    route_path,
)
from tests.shared.api import api as api  # noqa: RUF100 - re-exported fixture


@pytest.fixture
def client() -> BuddyApiClient:
    return build_client(project_name=TEST_PROJECT)


def echo_body(api: respx.MockRouter, method: str, url: str, build: Any) -> dict[str, Any]:
    """Answer a request with a body derived from what it sent, and record it."""
    received: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        received.clear()
        received.update(json.loads(request.content))
        return httpx.Response(200, json=build(received))

    getattr(api, method)(path=route_path(url)).mock(side_effect=handler)

    return received


class TestConstructor:
    def test_creates_a_client_from_a_valid_config(self, client: BuddyApiClient) -> None:
        assert client.workspace == TEST_WORKSPACE
        assert client.project_name == TEST_PROJECT

    def test_raises_when_the_token_is_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("BUDDY_TOKEN", raising=False)

        with pytest.raises(ValueError, match="Buddy API token is required"):
            BuddyApiClient(
                workspace=TEST_WORKSPACE, project_name=TEST_PROJECT, api_url=TEST_API_URL
            )


class TestSandboxes:
    async def test_fetches_the_sandboxes_list(
        self, api: respx.MockRouter, client: BuddyApiClient
    ) -> None:
        route = api.get(path=route_path(SANDBOXES_URL)).mock(
            return_value=httpx.Response(
                200,
                json={
                    "sandboxes": [
                        {"id": "sandbox-1", "name": "Test 1"},
                        {"id": "sandbox-2", "name": "Test 2"},
                    ]
                },
            )
        )

        response = await client.get_sandboxes()

        assert route.calls.last.request.url.params["project_name"] == TEST_PROJECT
        assert len(response.sandboxes or []) == 2
        assert (response.sandboxes or [])[0].id == "sandbox-1"

    async def test_fetches_a_sandbox_by_id(
        self, api: respx.MockRouter, client: BuddyApiClient
    ) -> None:
        api.get(path=route_path(f"{SANDBOXES_URL}/sandbox-123")).mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "sandbox-123",
                    "name": "My Sandbox",
                    "status": "RUNNING",
                    "os": "ubuntu:24.04",
                },
            )
        )

        response = await client.get_sandbox_by_id(path={"id": "sandbox-123"})

        assert response.id == "sandbox-123"
        assert response.name == "My Sandbox"

    async def test_raises_an_http_error_on_404(
        self, api: respx.MockRouter, client: BuddyApiClient
    ) -> None:
        api.get(path=route_path(f"{SANDBOXES_URL}/non-existent")).mock(
            return_value=httpx.Response(404, json={"errors": [{"message": "Sandbox not found"}]})
        )

        with pytest.raises(HttpError):
            await client.get_sandbox_by_id(path={"id": "non-existent"})

    async def test_accepts_a_response_that_fills_in_none_of_the_optional_fields(
        self, api: respx.MockRouter, client: BuddyApiClient
    ) -> None:
        api.get(path=route_path(f"{SANDBOXES_URL}/sandbox-id")).mock(
            return_value=httpx.Response(200, json={})
        )

        assert await client.get_sandbox_by_id(path={"id": "sandbox-id"}) is not None


class TestAddSandbox:
    async def test_creates_a_new_sandbox(
        self, api: respx.MockRouter, client: BuddyApiClient
    ) -> None:
        echo_body(
            api,
            "post",
            SANDBOXES_URL,
            lambda body: {
                "id": "new-sandbox-id",
                "name": body["name"],
                "identifier": body["identifier"],
                "status": "STARTING",
            },
        )

        response = await client.add_sandbox(
            body={"name": "New Sandbox", "identifier": "new-sandbox", "os": "ubuntu:24.04"}
        )

        assert response.id == "new-sandbox-id"
        assert response.identifier == "new-sandbox"

    async def test_forwards_the_timeout_in_the_request_body(
        self, api: respx.MockRouter, client: BuddyApiClient
    ) -> None:
        received = echo_body(
            api, "post", SANDBOXES_URL, lambda body: {"id": "with-timeout", **body}
        )

        response = await client.add_sandbox(
            body={
                "name": "Timeout Sandbox",
                "identifier": "timeout-sandbox",
                "os": "ubuntu:24.04",
                "timeout": 600,
            }
        )

        assert received["timeout"] == 600
        assert response.timeout == 600

    async def test_forwards_source_sandbox_id_when_cloning(
        self, api: respx.MockRouter, client: BuddyApiClient
    ) -> None:
        received = echo_body(
            api, "post", SANDBOXES_URL, lambda body: {"id": "clone-id", "name": body["name"]}
        )

        response = await client.add_sandbox(
            body={
                "source_sandbox_id": "source-sandbox",
                "name": "Clone Sandbox",
                "identifier": "clone-sandbox",
            }
        )

        assert received["source_sandbox_id"] == "source-sandbox"
        assert response.id == "clone-id"

    async def test_forwards_fetch_items_in_the_request_body(
        self, api: respx.MockRouter, client: BuddyApiClient
    ) -> None:
        fetch_items = [
            {
                "type": "PUBLIC_REPO",
                "repository": "https://github.com/octocat/Hello-World",
                "ref": "master",
            }
        ]
        received = echo_body(api, "post", SANDBOXES_URL, lambda body: {"id": "with-fetch", **body})

        response = await client.add_sandbox(
            body={
                "name": "Fetch Sandbox",
                "identifier": "fetch-sandbox",
                "os": "ubuntu:24.04",
                "fetch": fetch_items,
            }
        )

        assert received["fetch"] == fetch_items
        assert [item.model_dump(exclude_unset=True) for item in response.fetch or []] == fetch_items


class TestDeleteSandbox:
    async def test_deletes_a_sandbox(self, api: respx.MockRouter, client: BuddyApiClient) -> None:
        api.delete(path=route_path(f"{SANDBOXES_URL}/sandbox-to-delete")).mock(
            return_value=httpx.Response(204)
        )

        await client.delete_sandbox_by_id(path={"id": "sandbox-to-delete"})

    async def test_does_not_raise_on_404_because_it_is_already_deleted(
        self, api: respx.MockRouter, client: BuddyApiClient
    ) -> None:
        api.delete(path=route_path(f"{SANDBOXES_URL}/already-deleted")).mock(
            return_value=httpx.Response(404, json={"errors": [{"message": "Not found"}]})
        )

        await client.delete_sandbox_by_id(path={"id": "already-deleted"})


class TestLifecycle:
    @pytest.mark.parametrize(
        ("action", "method", "status"),
        [
            ("start_sandbox", "start", "STARTING"),
            ("stop_sandbox", "stop", "STOPPING"),
            ("restart_sandbox", "restart", "STARTING"),
        ],
    )
    async def test_moves_the_sandbox_through_its_lifecycle(
        self,
        api: respx.MockRouter,
        client: BuddyApiClient,
        action: str,
        method: str,
        status: str,
    ) -> None:
        api.post(path=route_path(f"{SANDBOXES_URL}/sandbox-id/{method}")).mock(
            return_value=httpx.Response(200, json={"id": "sandbox-id", "status": status})
        )

        response = await getattr(client, action)(path={"sandbox_id": "sandbox-id"})

        assert response.status == status


class TestApps:
    async def test_starts_an_app_and_returns_the_updated_sandbox(
        self, api: respx.MockRouter, client: BuddyApiClient
    ) -> None:
        api.post(path=route_path(f"{SANDBOXES_URL}/sandbox-id/apps/app-1/start")).mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "sandbox-id",
                    "status": "RUNNING",
                    "apps": [
                        {"id": "app-1", "command": "node server.js", "app_status": "RUNNING"},
                        {"id": "app-2", "command": "python worker.py", "app_status": "RUNNING"},
                    ],
                },
            )
        )

        response = await client.start_sandbox_app(
            path={"sandbox_id": "sandbox-id", "app_id": "app-1"}
        )

        apps = {app.id: app.app_status for app in response.apps or []}
        assert apps["app-1"] == "RUNNING"

    async def test_stops_an_app_without_affecting_the_others(
        self, api: respx.MockRouter, client: BuddyApiClient
    ) -> None:
        api.post(path=route_path(f"{SANDBOXES_URL}/sandbox-id/apps/app-1/stop")).mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "sandbox-id",
                    "status": "RUNNING",
                    "apps": [
                        {"id": "app-1", "command": "node server.js", "app_status": "ENDED"},
                        {"id": "app-2", "command": "python worker.py", "app_status": "RUNNING"},
                    ],
                },
            )
        )

        response = await client.stop_sandbox_app(
            path={"sandbox_id": "sandbox-id", "app_id": "app-1"}
        )

        apps = {app.id: app.app_status for app in response.apps or []}
        assert apps["app-1"] == "ENDED"
        assert apps["app-2"] == "RUNNING"

    async def test_fetches_app_logs_with_a_pagination_cursor(
        self, api: respx.MockRouter, client: BuddyApiClient
    ) -> None:
        route = api.get(path=route_path(f"{SANDBOXES_URL}/sandbox-id/apps/app-1/logs")).mock(
            return_value=httpx.Response(
                200, json={"logs": ["line 1", "line 2", "line 3"], "cursor": "next-page-cursor"}
            )
        )

        response = await client.get_sandbox_app_logs(
            path={"sandbox_id": "sandbox-id", "app_id": "app-1"}, query={"cursor": "prev-cursor"}
        )

        assert route.calls.last.request.url.params["cursor"] == "prev-cursor"
        assert response.logs == ["line 1", "line 2", "line 3"]
        assert response.cursor == "next-page-cursor"


class TestFileOperations:
    async def test_gets_sandbox_content(
        self, api: respx.MockRouter, client: BuddyApiClient
    ) -> None:
        api.get(path=route_path(f"{SANDBOXES_URL}/sandbox-id/content/path/to/dir")).mock(
            return_value=httpx.Response(
                200,
                json={
                    "contents": [
                        {"name": "file.txt", "type": "FILE", "path": "/path/to/dir/file.txt"},
                        {"name": "subdir", "type": "DIR", "path": "/path/to/dir/subdir"},
                    ]
                },
            )
        )

        response = await client.get_sandbox_content(
            path={"sandbox_id": "sandbox-id", "path": "path/to/dir"}
        )

        assert len(response.contents or []) == 2
        assert (response.contents or [])[0].name == "file.txt"

    async def test_deletes_a_sandbox_file(
        self, api: respx.MockRouter, client: BuddyApiClient
    ) -> None:
        api.delete(path=route_path(f"{SANDBOXES_URL}/sandbox-id/content/file.txt")).mock(
            return_value=httpx.Response(204)
        )

        await client.delete_sandbox_file(path={"sandbox_id": "sandbox-id", "path": "file.txt"})

    async def test_creates_a_sandbox_directory(
        self, api: respx.MockRouter, client: BuddyApiClient
    ) -> None:
        api.post(path=route_path(f"{SANDBOXES_URL}/sandbox-id/content/new-dir")).mock(
            return_value=httpx.Response(
                200, json={"name": "new-dir", "type": "DIR", "path": "/new-dir"}
            )
        )

        response = await client.create_sandbox_directory(
            path={"sandbox_id": "sandbox-id", "path": "new-dir"}
        )

        assert response.name == "new-dir"
        assert response.type == "DIR"

    async def test_rejects_a_download_that_is_not_a_success(
        self, api: respx.MockRouter, client: BuddyApiClient
    ) -> None:
        api.get(path=route_path(f"{SANDBOXES_URL}/sandbox-id/download/file.txt")).mock(
            return_value=httpx.Response(304)
        )

        with pytest.raises(HttpError):
            await client.download_sandbox_content(
                path={"sandbox_id": "sandbox-id", "path": "file.txt"}
            )


class TestCommands:
    async def test_executes_a_command(self, api: respx.MockRouter, client: BuddyApiClient) -> None:
        echo_body(
            api,
            "post",
            f"{SANDBOXES_URL}/sandbox-id/commands",
            lambda body: {"id": "command-123", "command": body["command"], "status": "INPROGRESS"},
        )

        response = await client.execute_command(
            path={"sandbox_id": "sandbox-id"}, body={"command": "echo hello"}
        )

        assert isinstance(response, SandboxCommandView)
        assert response.id == "command-123"
        assert response.status == "INPROGRESS"

    async def test_gets_command_details(
        self, api: respx.MockRouter, client: BuddyApiClient
    ) -> None:
        api.get(path=route_path(f"{SANDBOXES_URL}/sandbox-id/commands/cmd-123")).mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "cmd-123",
                    "command": "echo hello",
                    "status": "SUCCESSFUL",
                    "exit_code": 0,
                },
            )
        )

        response = await client.get_command_details(
            path={"sandbox_id": "sandbox-id", "id": "cmd-123"}
        )

        assert response.status == "SUCCESSFUL"
        assert response.exit_code == 0

    async def test_terminates_a_command(
        self, api: respx.MockRouter, client: BuddyApiClient
    ) -> None:
        api.post(path=route_path(f"{SANDBOXES_URL}/sandbox-id/commands/cmd-123/terminate")).mock(
            return_value=httpx.Response(200, json={})
        )

        response = await client.terminate_command(
            path={"sandbox_id": "sandbox-id", "command_id": "cmd-123"}
        )

        assert response is not None

    async def test_lists_commands_in_a_sandbox(
        self, api: respx.MockRouter, client: BuddyApiClient
    ) -> None:
        api.get(path=route_path(f"{SANDBOXES_URL}/sandbox-id/commands")).mock(
            return_value=httpx.Response(
                200,
                json={
                    "commands": [
                        {
                            "id": "cmd-a",
                            "command": "echo first",
                            "status": "SUCCESSFUL",
                            "exit_code": 0,
                        },
                        {"id": "cmd-b", "command": "sleep 60", "status": "INPROGRESS"},
                    ]
                },
            )
        )

        response = await client.get_sandbox_commands(path={"sandbox_id": "sandbox-id"})

        commands = response.commands or []
        assert len(commands) == 2
        assert commands[0].id == "cmd-a"
        assert commands[1].status == "INPROGRESS"


class TestUpdateSandbox:
    async def test_patches_the_sandbox_and_returns_the_updated_one(
        self, api: respx.MockRouter, client: BuddyApiClient
    ) -> None:
        received = echo_body(
            api,
            "patch",
            f"{SANDBOXES_URL}/sandbox-id",
            lambda body: {"id": "sandbox-id", "status": "RUNNING", **body},
        )

        response = await client.update_sandbox(
            path={"id": "sandbox-id"}, body={"timeout": 1200, "tags": ["updated"]}
        )

        assert api.calls.last.request.method == "PATCH"
        assert received == {"timeout": 1200, "tags": ["updated"]}
        assert response.timeout == 1200
        assert response.tags == ["updated"]


class TestRequestShaping:
    async def test_leaves_an_unset_field_out_of_the_body(
        self, api: respx.MockRouter, client: BuddyApiClient
    ) -> None:
        # Sending null would clear the field rather than leave it alone.
        received = echo_body(
            api, "patch", f"{SANDBOXES_URL}/sandbox-id", lambda body: {"id": "sandbox-id"}
        )

        await client.update_sandbox(
            path={"id": "sandbox-id"}, body={"timeout": None, "tags": ["kept"]}
        )

        assert received == {"tags": ["kept"]}

    async def test_types_an_uploaded_file_rather_than_guessing_from_its_name(
        self, api: respx.MockRouter, client: BuddyApiClient
    ) -> None:
        route = api.post(
            path=route_path(f"{SANDBOXES_URL}/sandbox-id/content/upload/notes.txt")
        ).mock(return_value=httpx.Response(200, json={"name": "notes.txt", "type": "FILE"}))

        await client.upload_sandbox_file(
            body=b"hello", path={"sandbox_id": "sandbox-id", "path": "notes.txt"}
        )

        body = route.calls.last.request.content.decode()

        assert "Content-Type: application/octet-stream" in body


class TestSnapshots:
    async def test_lists_all_snapshots_in_the_scope_across_sandboxes(
        self, api: respx.MockRouter, client: BuddyApiClient
    ) -> None:
        route = api.get(path=route_path(f"{SANDBOXES_URL}/snapshots")).mock(
            return_value=httpx.Response(
                200,
                json={
                    "snapshots": [
                        {"id": "snap-a", "name": "From sandbox A", "status": "CREATED"},
                        {"id": "snap-b", "name": "Orphan", "status": "CREATED"},
                    ]
                },
            )
        )

        response = await client.get_project_snapshots()

        assert route.calls.last.request.url.params["project_name"] == TEST_PROJECT
        assert [snapshot.id for snapshot in response.snapshots or []] == ["snap-a", "snap-b"]

    async def test_lists_the_snapshots_of_one_sandbox(
        self, api: respx.MockRouter, client: BuddyApiClient
    ) -> None:
        api.get(path=route_path(f"{SANDBOXES_URL}/sandbox-id/snapshots")).mock(
            return_value=httpx.Response(
                200,
                json={
                    "snapshots": [
                        {"id": "snap-1", "name": "First"},
                        {"id": "snap-2", "name": "Second"},
                    ]
                },
            )
        )

        response = await client.get_sandbox_snapshots(path={"sandbox_id": "sandbox-id"})

        assert len(response.snapshots or []) == 2
        assert (response.snapshots or [])[0].id == "snap-1"

    async def test_creates_a_snapshot_with_an_optional_name(
        self, api: respx.MockRouter, client: BuddyApiClient
    ) -> None:
        received = echo_body(
            api,
            "post",
            f"{SANDBOXES_URL}/sandbox-id/snapshots",
            lambda body: {"id": "snap-new", "name": body["name"]},
        )

        response = await client.add_sandbox_snapshot(
            path={"sandbox_id": "sandbox-id"}, body={"name": "before-deploy"}
        )

        assert received == {"name": "before-deploy"}
        assert response.id == "snap-new"

    async def test_fetches_a_specific_snapshot_by_id(
        self, api: respx.MockRouter, client: BuddyApiClient
    ) -> None:
        api.get(path=route_path(f"{SANDBOXES_URL}/sandbox-id/snapshots/snap-1")).mock(
            return_value=httpx.Response(200, json={"id": "snap-1", "name": "First"})
        )

        response = await client.get_sandbox_snapshot(
            path={"sandbox_id": "sandbox-id", "id": "snap-1"}
        )

        assert response.id == "snap-1"

    @pytest.mark.parametrize("status", [204, 404])
    async def test_deletes_a_sandbox_snapshot_whether_or_not_it_is_still_there(
        self, api: respx.MockRouter, client: BuddyApiClient, status: int
    ) -> None:
        api.delete(path=route_path(f"{SANDBOXES_URL}/sandbox-id/snapshots/snap-1")).mock(
            return_value=httpx.Response(status, json={} if status == 404 else None)
        )

        await client.delete_sandbox_snapshot(path={"sandbox_id": "sandbox-id", "id": "snap-1"})

    @pytest.mark.parametrize("status", [204, 404])
    async def test_deletes_a_snapshot_at_the_project_level(
        self, api: respx.MockRouter, client: BuddyApiClient, status: int
    ) -> None:
        route = api.delete(path=route_path(f"{SANDBOXES_URL}/snapshots/orphan-snap")).mock(
            return_value=httpx.Response(status, json={} if status == 404 else None)
        )

        await client.delete_snapshot(path={"id": "orphan-snap"})

        assert route.called
