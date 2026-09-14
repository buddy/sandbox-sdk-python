"""The scope matrix for PROJECT, ENVIRONMENT and WORKSPACE sandboxes.

Which query params go out, what lands in the create body, how many lookups an
environment costs. The body assertions rely on the patches applied by
``scripts/cleanup_schemas.py``.
"""

from __future__ import annotations

import asyncio
from typing import Any, Final

import httpx
import pytest
import respx

from buddy_sandbox import BuddySDKError, ConnectionConfig, Sandbox
from buddy_sandbox.utils.client import create_client
from tests.shared.api import (
    IDENTIFIERS_URL,
    SANDBOXES_URL,
    SNAPSHOTS_URL,
    TEST_API_URL,
    TEST_CONNECTION,
    TEST_PROJECT,
    TEST_TOKEN,
    TEST_WORKSPACE,
    build_client,
    capture_body,
    record_queries,
    route_path,
)
from tests.shared.api import api as api  # noqa: RUF100 - re-exported fixture

ENVIRONMENT: Final = "staging"
ENVIRONMENT_ID: Final = "3a4KbBQl"


def record_list_requests(api: respx.MockRouter) -> list[httpx.QueryParams]:
    return record_queries(api, SANDBOXES_URL, {"sandboxes": []})


def resolve_environment(
    api: respx.MockRouter, *, in_project: bool = False
) -> list[httpx.QueryParams]:
    """Resolve ``staging`` only for the given lookup shape, omitting it otherwise."""
    lookups: list[httpx.QueryParams] = []

    def handler(request: httpx.Request) -> httpx.Response:
        query = request.url.params
        lookups.append(query)

        project = query.get("project")
        asked_in_project = project is not None
        matches = asked_in_project if in_project else not asked_in_project

        return httpx.Response(
            200,
            json={
                # The endpoint echoes back the project it resolved.
                **({"project_identifier": project} if project else {}),
                **({"environment_id": ENVIRONMENT_ID} if matches else {}),
            },
        )

    api.get(path=route_path(IDENTIFIERS_URL)).mock(side_effect=handler)

    return lookups


class TestScopeResolution:
    async def test_pins_requests_to_the_project_when_only_a_project_is_configured(
        self, api: respx.MockRouter
    ) -> None:
        queries = record_list_requests(api)

        await build_client(project_name=TEST_PROJECT).get_sandboxes()

        assert queries[0]["project_name"] == TEST_PROJECT
        assert "environment_id" not in queries[0]

    async def test_sends_no_scope_params_at_all_when_neither_is_configured(
        self, api: respx.MockRouter
    ) -> None:
        queries = record_list_requests(api)

        await build_client().get_sandboxes()

        assert list(queries[0].keys()) == []

    async def test_pins_requests_to_the_environment_not_the_project_when_both_are_given(
        self, api: respx.MockRouter
    ) -> None:
        lookups = resolve_environment(api, in_project=True)
        queries = record_list_requests(api)

        await build_client(project_name=TEST_PROJECT, environment=ENVIRONMENT).get_sandboxes()

        assert lookups[0]["project"] == TEST_PROJECT
        assert lookups[0]["environment"] == ENVIRONMENT
        assert queries[0]["environment_id"] == ENVIRONMENT_ID
        assert "project_name" not in queries[0]

    async def test_stays_in_the_project_instead_of_looking_one_level_up(
        self, api: respx.MockRouter
    ) -> None:
        lookups = resolve_environment(api, in_project=False)

        with pytest.raises(
            ValueError,
            match=f"Environment '{ENVIRONMENT}' not found in project '{TEST_PROJECT}'.",
        ):
            await build_client(project_name=TEST_PROJECT, environment=ENVIRONMENT).get_sandboxes()

        assert len(lookups) == 1

    async def test_resolves_the_environment_once_and_reuses_it(self, api: respx.MockRouter) -> None:
        lookups = resolve_environment(api)
        record_list_requests(api)

        client = build_client(environment=ENVIRONMENT)
        await client.get_sandboxes()
        await client.get_sandboxes()
        await client.get_sandboxes()

        assert len(lookups) == 1

    async def test_retries_the_lookup_after_a_failure_instead_of_caching_it(
        self, api: respx.MockRouter
    ) -> None:
        api.get(path=route_path(IDENTIFIERS_URL)).mock(
            side_effect=[
                # First call comes back empty, the next one resolves.
                httpx.Response(200, json={}),
                httpx.Response(200, json={"environment_id": ENVIRONMENT_ID}),
            ]
        )
        queries = record_list_requests(api)

        client = build_client(environment=ENVIRONMENT)

        with pytest.raises(ValueError, match=f"Environment '{ENVIRONMENT}' not found"):
            await client.get_sandboxes()

        await client.get_sandboxes()

        assert queries[0]["environment_id"] == ENVIRONMENT_ID

    async def test_keeps_the_lookup_when_one_caller_is_cancelled(
        self, api: respx.MockRouter
    ) -> None:
        lookups: list[int] = []

        async def slow(request: httpx.Request) -> httpx.Response:
            lookups.append(1)
            await asyncio.sleep(0.05)
            return httpx.Response(200, json={"environment_id": ENVIRONMENT_ID})

        api.get(path=route_path(IDENTIFIERS_URL)).mock(side_effect=slow)
        record_list_requests(api)

        client = build_client(environment=ENVIRONMENT)

        cancelled = asyncio.ensure_future(client.get_sandboxes())
        await asyncio.sleep(0.01)
        cancelled.cancel()

        with pytest.raises(asyncio.CancelledError):
            await cancelled

        await client.get_sandboxes()

        # The shield keeps the lookup alive, so the cache has to survive too.
        assert len(lookups) == 1

    async def test_skips_the_lookup_entirely_when_the_environment_id_is_known_upfront(
        self, api: respx.MockRouter
    ) -> None:
        queries = record_list_requests(api)

        await build_client(environment_id=ENVIRONMENT_ID).get_sandboxes()

        assert queries[0]["environment_id"] == ENVIRONMENT_ID

    async def test_reports_an_unknown_project_instead_of_using_a_workspace_environment(
        self, api: respx.MockRouter
    ) -> None:
        # No project_identifier in the response means the project did not
        # resolve - the environment_id then answers a different question.
        api.get(path=route_path(IDENTIFIERS_URL)).mock(
            return_value=httpx.Response(200, json={"environment_id": ENVIRONMENT_ID})
        )

        with pytest.raises(ValueError, match=f"Project '{TEST_PROJECT}' not found."):
            await build_client(project_name=TEST_PROJECT, environment=ENVIRONMENT).get_sandboxes()

    async def test_surfaces_a_failed_lookup_as_is_instead_of_blaming_the_project(
        self, api: respx.MockRouter
    ) -> None:
        api.get(path=route_path(IDENTIFIERS_URL)).mock(
            return_value=httpx.Response(404, json={"errors": [{"message": "Not found"}]})
        )

        with pytest.raises(Exception, match="404"):
            await build_client(project_name=TEST_PROJECT, environment=ENVIRONMENT).get_sandboxes()

    async def test_points_at_the_workspace_when_no_environment_is_found_there(
        self, api: respx.MockRouter
    ) -> None:
        api.get(path=route_path(IDENTIFIERS_URL)).mock(return_value=httpx.Response(200, json={}))

        with pytest.raises(
            ValueError, match=f"Environment '{ENVIRONMENT}' not found at workspace level."
        ):
            await build_client(environment=ENVIRONMENT).get_sandboxes()


class TestSnapshotListing:
    async def test_follows_the_project_scope(self, api: respx.MockRouter) -> None:
        queries = record_queries(api, SNAPSHOTS_URL, {"snapshots": []})

        await build_client(project_name=TEST_PROJECT).get_project_snapshots()

        assert queries[0]["project_name"] == TEST_PROJECT

    async def test_follows_the_environment_scope(self, api: respx.MockRouter) -> None:
        resolve_environment(api)
        queries = record_queries(api, SNAPSHOTS_URL, {"snapshots": []})

        await build_client(environment=ENVIRONMENT).get_project_snapshots()

        assert queries[0]["environment_id"] == ENVIRONMENT_ID
        assert "project_name" not in queries[0]

    async def test_asks_for_workspace_level_snapshots_when_nothing_is_configured(
        self, api: respx.MockRouter
    ) -> None:
        queries = record_queries(api, SNAPSHOTS_URL, {"snapshots": []})

        await build_client().get_project_snapshots()

        assert list(queries[0].keys()) == []


class TestFileUpload:
    async def test_does_not_tag_the_request_with_a_project(self, api: respx.MockRouter) -> None:
        # project_name is not a parameter of this endpoint - the backend ignored
        # it, and outside a project there is nothing to send anyway.
        route = api.post(
            path=route_path(f"{SANDBOXES_URL}/sandbox-1/content/upload/hello.txt")
        ).mock(
            return_value=httpx.Response(
                200, json={"type": "FILE", "name": "hello.txt", "path": "hello.txt"}
            )
        )

        await build_client(project_name=TEST_PROJECT).upload_sandbox_file(
            body=b"hello", path={"sandbox_id": "sandbox-1", "path": "hello.txt"}
        )

        assert list(route.calls.last.request.url.params.keys()) == []


class TestScopeInTheCreateBody:
    @pytest.mark.parametrize(
        ("method", "args"),
        [
            (Sandbox.create, {}),
            (Sandbox.clone, {"source_sandbox_id": "sandbox-1"}),
            (Sandbox.create_from_snapshot, {"snapshot_id": "snap-1"}),
        ],
    )
    @pytest.mark.parametrize("field", ["scope", "environment"])
    async def test_scope_is_not_settable_through_the_public_config(
        self, method: Any, args: dict[str, Any], field: str
    ) -> None:
        positional = list(args.values())

        with pytest.raises(BuddySDKError, match=f"Unknown field\\(s\\): {field}"):
            await method(*positional, connection=TEST_CONNECTION, **{field: "x"})

    @pytest.mark.parametrize("field", ["scope", "environment", "project"])
    async def test_update_does_not_accept_scope_fields(
        self, api: respx.MockRouter, field: str
    ) -> None:
        api.get(path=route_path(f"{SANDBOXES_URL}/sandbox-1")).mock(
            return_value=httpx.Response(200, json={"id": "sandbox-1"})
        )
        sandbox = await Sandbox.get_by_id("sandbox-1", connection=TEST_CONNECTION)

        with pytest.raises(BuddySDKError, match=f"Unknown field\\(s\\): {field}"):
            await sandbox.update(**{field: "x"})

    async def test_attaches_the_environment_reference_for_environment_scoped_sandboxes(
        self, api: respx.MockRouter
    ) -> None:
        resolve_environment(api)
        captured = capture_body(api, SANDBOXES_URL, {"id": "sandbox-1"})

        await build_client(environment=ENVIRONMENT).add_sandbox(
            body={"name": "New sandbox", "os": "ubuntu:24.04"}
        )

        assert captured["scope"] == "ENVIRONMENT"
        assert captured["environment"] == {"id": ENVIRONMENT_ID}

    async def test_leaves_the_body_untouched_for_project_scoped_sandboxes(
        self, api: respx.MockRouter
    ) -> None:
        captured = capture_body(api, SANDBOXES_URL, {"id": "sandbox-1"})

        await build_client(project_name=TEST_PROJECT).add_sandbox(
            body={"name": "New sandbox", "os": "ubuntu:24.04"}
        )

        assert captured == {"name": "New sandbox", "os": "ubuntu:24.04"}


class TestGetByIdentifier:
    @pytest.mark.parametrize(
        ("scope", "expected"),
        [
            ({"project_name": TEST_PROJECT}, {"project": TEST_PROJECT, "sandbox": "my_sandbox"}),
            ({}, {"sandbox": "my_sandbox"}),
            (
                {"environment": ENVIRONMENT},
                {"environment": ENVIRONMENT, "sandbox": "my_sandbox"},
            ),
            (
                {"environment_id": ENVIRONMENT_ID},
                {"environment": ENVIRONMENT_ID, "sandbox": "my_sandbox"},
            ),
            (
                {"project_name": TEST_PROJECT, "environment": ENVIRONMENT},
                {"project": TEST_PROJECT, "environment": ENVIRONMENT, "sandbox": "my_sandbox"},
            ),
        ],
        ids=["project", "workspace", "environment", "environment-id", "project-environment"],
    )
    async def test_resolves_through_identifiers_in_every_scope(
        self, api: respx.MockRouter, scope: dict[str, str], expected: dict[str, str]
    ) -> None:
        identifiers = api.get(path=route_path(IDENTIFIERS_URL)).mock(
            return_value=httpx.Response(200, json={"sandbox_id": "sandbox-1"})
        )
        api.get(path=route_path(f"{SANDBOXES_URL}/sandbox-1")).mock(
            return_value=httpx.Response(200, json={"id": "sandbox-1", "identifier": "my_sandbox"})
        )

        sandbox = await Sandbox.get_by_identifier(
            "my_sandbox",
            connection=ConnectionConfig(
                workspace=TEST_WORKSPACE,
                token=TEST_TOKEN,
                api_url=TEST_API_URL,
                project=scope.get("project_name"),
                environment=scope.get("environment"),
                environment_id=scope.get("environment_id"),
            ),
        )

        # One request whatever the scope: /identifiers resolves against the
        # environment it is handed, so no ID has to be looked up first.
        assert identifiers.call_count == 1
        assert dict(identifiers.calls.last.request.url.params) == expected
        assert sandbox.data.id == "sandbox-1"

    async def test_reports_a_missing_identifier_instead_of_guessing(
        self, api: respx.MockRouter
    ) -> None:
        api.get(path=route_path(IDENTIFIERS_URL)).mock(return_value=httpx.Response(200, json={}))

        with pytest.raises(BuddySDKError, match="Sandbox with identifier 'ghost' not found"):
            await Sandbox.get_by_identifier("ghost", connection=TEST_CONNECTION)


class TestConnectionPool:
    async def test_every_client_shares_one_pool(self, api: respx.MockRouter) -> None:
        # A pool per call would mean a TLS handshake per call.
        record_queries(api, SANDBOXES_URL, {"sandboxes": []})
        record_queries(api, SNAPSHOTS_URL, {"snapshots": []})

        await Sandbox.list(connection=TEST_CONNECTION)
        await Sandbox.list_snapshots(connection=TEST_CONNECTION)

        pools = {id(build_client()._http) for _ in range(2)}

        assert len(pools) == 1


@pytest.fixture
def base_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BUDDY_WORKSPACE", TEST_WORKSPACE)
    monkeypatch.setenv("BUDDY_API_URL", TEST_API_URL)
    monkeypatch.setenv("BUDDY_TOKEN", TEST_TOKEN)
    monkeypatch.delenv("BUDDY_PROJECT", raising=False)
    monkeypatch.delenv("BUDDY_ENVIRONMENT", raising=False)


@pytest.mark.usefixtures("base_env")
class TestConnectionConfig:
    def test_takes_the_scope_from_env_vars_when_the_caller_says_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BUDDY_PROJECT", TEST_PROJECT)

        assert create_client().scope == "PROJECT"

    def test_lets_an_explicit_project_override_a_globally_set_environment(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BUDDY_ENVIRONMENT", ENVIRONMENT)

        assert create_client(ConnectionConfig(project=TEST_PROJECT)).scope == "PROJECT"

    def test_lets_an_explicit_environment_override_a_globally_set_project(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BUDDY_PROJECT", TEST_PROJECT)

        client = create_client(ConnectionConfig(environment=ENVIRONMENT))

        assert client.scope == "ENVIRONMENT"
        assert client.project_name is None

    def test_prefers_the_environment_when_both_env_vars_are_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BUDDY_PROJECT", TEST_PROJECT)
        monkeypatch.setenv("BUDDY_ENVIRONMENT", ENVIRONMENT)

        client = create_client()

        assert client.scope == "ENVIRONMENT"
        # The project stays around: a project-scoped environment can only be
        # looked up through it.
        assert client.project_name == TEST_PROJECT

    def test_keeps_the_project_for_the_lookup_when_the_caller_passes_both(self) -> None:
        client = create_client(ConnectionConfig(project=TEST_PROJECT, environment=ENVIRONMENT))

        assert client.scope == "ENVIRONMENT"
        assert client.project_name == TEST_PROJECT

    def test_states_workspace_placement_by_naming_only_the_workspace(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BUDDY_PROJECT", TEST_PROJECT)

        assert create_client(ConnectionConfig(workspace=TEST_WORKSPACE)).scope == "WORKSPACE"

    def test_rejects_an_empty_string_instead_of_guessing_what_it_means(self) -> None:
        with pytest.raises(ValueError, match=r"connection\.project is empty"):
            create_client(ConnectionConfig(project=""))

        with pytest.raises(ValueError, match=r"connection\.workspace is empty"):
            create_client(ConnectionConfig(workspace="", environment=ENVIRONMENT))

    def test_falls_back_to_workspace_scope_when_nothing_is_configured(self) -> None:
        assert create_client().scope == "WORKSPACE"
