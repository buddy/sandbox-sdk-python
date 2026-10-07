"""API client for Buddy sandbox operations, with request and response validation."""

from __future__ import annotations

import asyncio
import codecs
import json
import re
from collections.abc import AsyncGenerator, Mapping
from typing import Any, Final, Literal, TypeVar

import httpx
from pydantic import BaseModel, RootModel, ValidationError

from buddy_sandbox.api.openapi.pydantic_gen import (
    AddSandboxBody,
    AddSandboxPath,
    AddSandboxQuery,
    AddSandboxResponse,
    AddSandboxSnapshotBody,
    AddSandboxSnapshotPath,
    AddSandboxSnapshotResponse,
    CreateSandboxDirectoryPath,
    CreateSandboxDirectoryResponse,
    DeleteSandboxFilePath,
    DeleteSandboxFileResponse,
    DeleteSandboxPath,
    DeleteSandboxResponse,
    DeleteSandboxSnapshotPath,
    DeleteSandboxSnapshotResponse,
    DeleteSnapshotPath,
    DeleteSnapshotResponse,
    DownloadSandboxContentPath,
    ExecSandboxCommandBody,
    ExecSandboxCommandPath,
    ExecSandboxCommandResponse,
    ExecuteSandboxCommandBody,
    ExecuteSandboxCommandPath,
    GetIdentifiersPath,
    GetIdentifiersQuery,
    GetIdentifiersResponse,
    GetProjectSnapshotsPath,
    GetProjectSnapshotsQuery,
    GetProjectSnapshotsResponse,
    GetSandboxAppLogsByIdPath,
    GetSandboxAppLogsByIdQuery,
    GetSandboxAppLogsByIdResponse,
    GetSandboxCommandLogsPath,
    GetSandboxCommandLogsQuery,
    GetSandboxCommandPath,
    GetSandboxCommandResponse,
    GetSandboxCommandsPath,
    GetSandboxCommandsResponse,
    GetSandboxContentPath,
    GetSandboxContentResponse,
    GetSandboxesPath,
    GetSandboxesQuery,
    GetSandboxesResponse,
    GetSandboxPath,
    GetSandboxResponse,
    GetSandboxSnapshotPath,
    GetSandboxSnapshotResponse,
    GetSandboxSnapshotsPath,
    GetSandboxSnapshotsResponse,
    IdsView,
    RestartSandboxPath,
    RestartSandboxResponse,
    SandboxAppLogsView,
    SandboxCommandLog,
    SandboxCommandResultView,
    SandboxCommandsView,
    SandboxCommandView,
    SandboxContentItem,
    SandboxContentView,
    SandboxesView,
    SandboxResponse,
    SnapshotsView,
    SnapshotView,
    StartSandboxAppPath,
    StartSandboxAppResponse,
    StartSandboxPath,
    StartSandboxResponse,
    StopSandboxAppPath,
    StopSandboxAppResponse,
    StopSandboxPath,
    StopSandboxResponse,
    TerminateSandboxCommandPath,
    TerminateSandboxCommandResponse,
    UpdateSandboxBody,
    UpdateSandboxPath,
    UpdateSandboxResponse,
    UploadSandboxFilePath,
    UploadSandboxFileResponse,
)
from buddy_sandbox.core.http_client import HttpClient, HttpError, HttpResponse
from buddy_sandbox.utils.environment import environment
from buddy_sandbox.utils.logger import logger

#: The level a sandbox belongs to.
SandboxScope = Literal["PROJECT", "ENVIRONMENT", "WORKSPACE"]

PATH_PLACEHOLDER: Final = re.compile(r"\{(\w+)\}")

#: The API fails a synchronous command at 60s - outlast it, barely.
EXEC_TIMEOUT_MS: Final = 65_000

T = TypeVar("T")


def _declares_query_param(model: type[BaseModel] | None, param: str) -> bool:
    """Whether a generated query model declares a given param."""
    if model is None:
        return False
    fields = model.model_fields
    return param in fields or any(field.alias == param for field in fields.values())


def _omit_none(values: Mapping[str, Any] | None) -> dict[str, Any]:
    """Drop unset entries so they never reach the request."""
    return {key: value for key, value in (values or {}).items() if value is not None}


class BuddyApiClient(HttpClient):
    """Buddy API client for sandbox operations."""

    def __init__(
        self,
        *,
        workspace: str,
        api_url: str,
        project_name: str | None = None,
        environment: str | None = None,
        environment_id: str | None = None,
        token: str | None = None,
        timeout_ms: float | None = None,
        headers: Mapping[str, str] | None = None,
        debug_mode: bool | None = None,
    ) -> None:
        resolved_token = token or _env_token()

        if not resolved_token:
            raise ValueError(
                "Buddy API token is required. "
                "Set BUDDY_TOKEN environment variable or pass token in config."
            )

        super().__init__(
            base_url=api_url,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                **(headers or {}),
            },
            debug_mode=debug_mode,
            **({"timeout_ms": timeout_ms} if timeout_ms is not None else {}),
        )

        self.workspace = workspace
        self.project_name = project_name
        self.environment = environment
        self._token = resolved_token
        self._environment_id = environment_id
        self._environment_id_lookup: asyncio.Task[str] | None = None

        self.set_auth_token(resolved_token)

    @property
    def scope(self) -> SandboxScope:
        """The scope every request is pinned to - an environment wins over a project."""
        if self.environment is not None or self._environment_id is not None:
            return "ENVIRONMENT"
        return "PROJECT" if self.project_name is not None else "WORKSPACE"

    async def _scope_query(self, query_model: type[BaseModel] | None) -> dict[str, str]:
        """Query params pinning a request to the client's scope.

        ``environment_id`` goes out only where the endpoint declares it -
        resolving it costs a request, which a call about logs should not have to
        pay or fail on.
        """
        if self.scope == "ENVIRONMENT":
            if _declares_query_param(query_model, "environment_id"):
                return {"environment_id": await self._resolve_environment_id()}
            return {}

        return {"project_name": self.project_name} if self.project_name is not None else {}

    def identifiers_scope_query(self) -> dict[str, str]:
        """Scope params for ``/identifiers``.

        That endpoint takes an environment as either an identifier or an ID and
        resolves against it directly, so pinning a request to an environment
        here costs no separate lookup.
        """
        query: dict[str, str] = {}

        if self.project_name is not None:
            query["project"] = self.project_name

        environment = self.environment if self.environment is not None else self._environment_id
        if environment is not None:
            query["environment"] = environment

        return query

    async def _resolve_environment_id(self) -> str:
        """Resolve (once) and cache the ID of the configured environment.

        Only a successful lookup is kept - a rejected task left in the field
        would be replayed for the lifetime of the client.
        """
        known = self._environment_id
        if known is not None:
            return known

        lookup = self._environment_id_lookup

        if lookup is None:
            lookup = asyncio.ensure_future(self._lookup_environment_id())
            lookup.add_done_callback(self._forget_failed_lookup)
            self._environment_id_lookup = lookup

        # Shielded: one caller going away must not take the lookup with it.
        resolved = await asyncio.shield(lookup)

        self._environment_id = resolved
        return resolved

    def _forget_failed_lookup(self, lookup: asyncio.Task[str]) -> None:
        """Drop a failed lookup, whether or not anyone was still waiting on it.

        Done as a callback rather than in the awaiting caller: cancelling every
        caller leaves nobody to see the failure, and a rejected task left in the
        field would be replayed for the lifetime of the client.
        """
        if lookup.cancelled() or lookup.exception() is None:
            return

        # Only drop our own attempt, not a newer concurrent one.
        if self._environment_id_lookup is lookup:
            self._environment_id_lookup = None

    async def _lookup_environment_id(self) -> str:
        """Look up an environment identifier.

        ``/identifiers`` searches a project or the workspace, never both, and
        the caller picks which by passing a project or not - so a miss is a
        miss, not a reason to look elsewhere.
        """
        identifier = self.environment
        if identifier is None:
            raise ValueError(
                "Environment identifier is missing. "
                "Set environment in config.connection or BUDDY_ENVIRONMENT env var."
            )

        project_name = self.project_name
        identifiers = await self.get_identifiers(
            query={"project": project_name, "environment": identifier}
            if project_name is not None
            else {"environment": identifier}
        )

        # An unknown project is dropped from the response rather than failing
        # the call, which would leave the answer about something else entirely.
        if project_name is not None and identifiers.project_identifier is None:
            raise ValueError(f"Project '{project_name}' not found.")

        if identifiers.environment_id:
            return identifiers.environment_id

        raise ValueError(
            f"Environment '{identifier}' not found in project '{project_name}'."
            if project_name is not None
            else f"Environment '{identifier}' not found at workspace level. "
            "Pass a project if it belongs to one."
        )

    async def _apply_scope_to_body(self, body: Any) -> Any:
        """Pin a created sandbox to the environment.

        POST /sandboxes takes no query param for it.
        """
        if self.scope != "ENVIRONMENT" or body is None:
            return body

        return {
            **body,
            "scope": "ENVIRONMENT",
            "environment": {"id": await self._resolve_environment_id()},
        }

    def _parameterize_url(self, url: str, path: Mapping[str, Any]) -> str:
        """Build a parameterized URL by replacing path placeholders."""

        def replace(match: re.Match[str]) -> str:
            key = match.group(1)
            value = path.get(key)
            if value is None:
                raise ValueError(f"Missing path parameter: {key}")
            return str(value)

        return PATH_PLACEHOLDER.sub(replace, url)

    def _absolute_url(
        self, url: str, path_model: type[BaseModel], path: Mapping[str, Any]
    ) -> httpx.URL:
        """Validate path params and build a full URL, for calls sent by hand."""
        validated = path_model.model_validate(
            {"workspace_domain": self.workspace, **_omit_none(path)}
        ).model_dump(mode="json", by_alias=True, exclude_unset=True)

        return self._build_url(self._parameterize_url(url, validated))

    def _parse_response(self, model: type[RootModel[T]], response: HttpResponse) -> T:
        """Validate response data against a generated model and unwrap it."""
        try:
            return model.model_validate(response.data).root
        except ValidationError as error:
            raise HttpError(
                f"Response validation failed:\n{error}", response.status, response
            ) from error

    async def _request_with_validation(
        self,
        *,
        method: Literal["GET", "POST", "DELETE", "PATCH"],
        url: str,
        path_model: type[BaseModel],
        response_model: type[RootModel[T]],
        path: Mapping[str, Any] | None = None,
        query: Mapping[str, Any] | None = None,
        body: Any = None,
        body_model: type[BaseModel] | None = None,
        query_model: type[BaseModel] | None = None,
        skip_retry: bool = False,
        skip_scope: bool = False,
        idempotent: bool = True,
        timeout_ms: float | None = None,
    ) -> T:
        """Execute an HTTP request with input and output validation."""
        validated_path = path_model.model_validate(
            {"workspace_domain": self.workspace, **_omit_none(path)}
        ).model_dump(mode="json", by_alias=True, exclude_unset=True)

        validated_query: dict[str, Any] | None = None
        if query_model is not None:
            scope = {} if skip_scope else await self._scope_query(query_model)
            validated_query = query_model.model_validate({**scope, **_omit_none(query)}).model_dump(
                mode="json", by_alias=True, exclude_unset=True, exclude_none=True
            )

        validated_body = body
        if body_model is not None and body is not None:
            validated_body = body_model.model_validate(body).model_dump(
                mode="json", by_alias=True, exclude_unset=True, exclude_none=True
            )

        parameterized_url = self._parameterize_url(url, validated_path)

        config: dict[str, Any] = {
            "query_params": validated_query,
            "skip_retry": skip_retry,
            "idempotent": idempotent,
            "timeout_ms": timeout_ms,
        }

        if method == "GET":
            response = await self.get(parameterized_url, **config)
        elif method == "DELETE":
            response = await self.delete(parameterized_url, **config)
        elif method == "POST":
            response = await self.post(parameterized_url, validated_body, **config)
        else:
            response = await self.patch(parameterized_url, validated_body, **config)

        return self._parse_response(response_model, response)

    # ------------------------------------------------------------------
    # Sandboxes
    # ------------------------------------------------------------------

    async def add_sandbox(
        self, *, body: Any, query: Mapping[str, Any] | None = None
    ) -> SandboxResponse:
        """Create a new sandbox."""
        return await self._request_with_validation(
            method="POST",
            url="/workspaces/{workspace_domain}/sandboxes",
            body=await self._apply_scope_to_body(body),
            query=query,
            idempotent=False,
            body_model=AddSandboxBody,
            path_model=AddSandboxPath,
            query_model=AddSandboxQuery,
            response_model=AddSandboxResponse,
        )

    async def update_sandbox(self, *, path: Mapping[str, Any], body: Any) -> SandboxResponse:
        """Update an existing sandbox's configuration (timeout, apps, endpoints, etc.)."""
        return await self._request_with_validation(
            method="PATCH",
            url="/workspaces/{workspace_domain}/sandboxes/{id}",
            path=path,
            body=body,
            body_model=UpdateSandboxBody,
            path_model=UpdateSandboxPath,
            response_model=UpdateSandboxResponse,
        )

    async def get_sandbox_by_id(self, *, path: Mapping[str, Any]) -> SandboxResponse:
        """Get a specific sandbox by its ID."""
        return await self._request_with_validation(
            method="GET",
            url="/workspaces/{workspace_domain}/sandboxes/{id}",
            path=path,
            path_model=GetSandboxPath,
            response_model=GetSandboxResponse,
        )

    async def get_sandboxes(self, *, query: Mapping[str, Any] | None = None) -> SandboxesView:
        """Get all sandboxes in the workspace for the configured scope."""
        return await self._request_with_validation(
            method="GET",
            url="/workspaces/{workspace_domain}/sandboxes",
            query=query,
            path_model=GetSandboxesPath,
            query_model=GetSandboxesQuery,
            response_model=GetSandboxesResponse,
        )

    async def delete_sandbox_by_id(self, *, path: Mapping[str, Any]) -> None:
        """Delete a sandbox by its ID, ignoring an already-deleted one."""
        try:
            await self._request_with_validation(
                method="DELETE",
                url="/workspaces/{workspace_domain}/sandboxes/{id}",
                path=path,
                path_model=DeleteSandboxPath,
                response_model=DeleteSandboxResponse,
                skip_retry=True,
            )
        except HttpError as error:
            # Ignore 404 errors - sandbox already deleted.
            if error.status == 404:
                return
            raise

    async def get_identifiers(self, *, query: Mapping[str, Any] | None = None) -> IdsView:
        """Resolve human-readable identifiers to IDs."""
        return await self._request_with_validation(
            method="GET",
            url="/workspaces/{workspace_domain}/identifiers",
            query=query,
            path_model=GetIdentifiersPath,
            query_model=GetIdentifiersQuery,
            response_model=GetIdentifiersResponse,
            # This endpoint resolves the scope, so it cannot depend on it.
            skip_scope=True,
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start_sandbox(self, *, path: Mapping[str, Any]) -> SandboxResponse:
        """Start a sandbox."""
        return await self._request_with_validation(
            method="POST",
            url="/workspaces/{workspace_domain}/sandboxes/{sandbox_id}/start",
            path=path,
            path_model=StartSandboxPath,
            response_model=StartSandboxResponse,
        )

    async def stop_sandbox(self, *, path: Mapping[str, Any]) -> SandboxResponse:
        """Stop a sandbox."""
        return await self._request_with_validation(
            method="POST",
            url="/workspaces/{workspace_domain}/sandboxes/{sandbox_id}/stop",
            path=path,
            path_model=StopSandboxPath,
            response_model=StopSandboxResponse,
        )

    async def restart_sandbox(self, *, path: Mapping[str, Any]) -> SandboxResponse:
        """Restart a sandbox."""
        return await self._request_with_validation(
            method="POST",
            url="/workspaces/{workspace_domain}/sandboxes/{sandbox_id}/restart",
            path=path,
            idempotent=False,
            path_model=RestartSandboxPath,
            response_model=RestartSandboxResponse,
        )

    # ------------------------------------------------------------------
    # Apps
    # ------------------------------------------------------------------

    async def start_sandbox_app(self, *, path: Mapping[str, Any]) -> SandboxResponse:
        """Start a sandbox app."""
        return await self._request_with_validation(
            method="POST",
            url="/workspaces/{workspace_domain}/sandboxes/{sandbox_id}/apps/{app_id}/start",
            path=path,
            path_model=StartSandboxAppPath,
            response_model=StartSandboxAppResponse,
        )

    async def stop_sandbox_app(self, *, path: Mapping[str, Any]) -> SandboxResponse:
        """Stop a sandbox app."""
        return await self._request_with_validation(
            method="POST",
            url="/workspaces/{workspace_domain}/sandboxes/{sandbox_id}/apps/{app_id}/stop",
            path=path,
            path_model=StopSandboxAppPath,
            response_model=StopSandboxAppResponse,
        )

    async def get_sandbox_app_logs(
        self, *, path: Mapping[str, Any], query: Mapping[str, Any] | None = None
    ) -> SandboxAppLogsView:
        """Get logs for a specific sandbox app."""
        return await self._request_with_validation(
            method="GET",
            url="/workspaces/{workspace_domain}/sandboxes/{sandbox_id}/apps/{app_id}/logs",
            path=path,
            query=query,
            path_model=GetSandboxAppLogsByIdPath,
            query_model=GetSandboxAppLogsByIdQuery,
            response_model=GetSandboxAppLogsByIdResponse,
        )

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    async def execute_command(self, *, path: Mapping[str, Any], body: Any) -> SandboxCommandView:
        """Execute a command in a sandbox, returning once it has been accepted.

        The endpoint can also answer with a finished result, but only for the
        ``fast`` query param this method never sends - ``exec_command`` covers that.
        """
        return await self._request_with_validation(
            method="POST",
            url="/workspaces/{workspace_domain}/sandboxes/{sandbox_id}/commands",
            path=path,
            body=body,
            idempotent=False,
            body_model=ExecuteSandboxCommandBody,
            path_model=ExecuteSandboxCommandPath,
            response_model=GetSandboxCommandResponse,
        )

    async def exec_command(
        self, *, path: Mapping[str, Any], body: Any, timeout_ms: float | None = None
    ) -> SandboxCommandResultView:
        """Run a command in a sandbox and wait for its result.

        The request stays open for as long as the command runs, so it outlasts
        the API's own 60 second ceiling by default rather than the client-wide
        timeout.
        """
        return await self._request_with_validation(
            method="POST",
            url="/workspaces/{workspace_domain}/sandboxes/{sandbox_id}/exec",
            path=path,
            body=body,
            idempotent=False,
            body_model=ExecSandboxCommandBody,
            path_model=ExecSandboxCommandPath,
            response_model=ExecSandboxCommandResponse,
            timeout_ms=timeout_ms if timeout_ms is not None else EXEC_TIMEOUT_MS,
        )

    async def get_sandbox_commands(self, *, path: Mapping[str, Any]) -> SandboxCommandsView:
        """List all command executions in a sandbox (history)."""
        return await self._request_with_validation(
            method="GET",
            url="/workspaces/{workspace_domain}/sandboxes/{sandbox_id}/commands",
            path=path,
            path_model=GetSandboxCommandsPath,
            response_model=GetSandboxCommandsResponse,
        )

    async def get_command_details(self, *, path: Mapping[str, Any]) -> SandboxCommandView:
        """Get a specific command execution's details."""
        return await self._request_with_validation(
            method="GET",
            url="/workspaces/{workspace_domain}/sandboxes/{sandbox_id}/commands/{id}",
            path=path,
            path_model=GetSandboxCommandPath,
            response_model=GetSandboxCommandResponse,
        )

    async def terminate_command(self, *, path: Mapping[str, Any]) -> SandboxCommandView:
        """Terminate a running command in a sandbox."""
        return await self._request_with_validation(
            method="POST",
            url="/workspaces/{workspace_domain}/sandboxes/{sandbox_id}/commands/{command_id}/terminate",
            path=path,
            path_model=TerminateSandboxCommandPath,
            response_model=TerminateSandboxCommandResponse,
        )

    # ------------------------------------------------------------------
    # Snapshots
    # ------------------------------------------------------------------

    async def get_sandbox_snapshots(self, *, path: Mapping[str, Any]) -> SnapshotsView:
        """List snapshots for a sandbox."""
        return await self._request_with_validation(
            method="GET",
            url="/workspaces/{workspace_domain}/sandboxes/{sandbox_id}/snapshots",
            path=path,
            path_model=GetSandboxSnapshotsPath,
            response_model=GetSandboxSnapshotsResponse,
        )

    async def get_project_snapshots(
        self, *, query: Mapping[str, Any] | None = None
    ) -> SnapshotsView:
        """List all snapshots in the scope (across all sandboxes, including orphans)."""
        return await self._request_with_validation(
            method="GET",
            url="/workspaces/{workspace_domain}/sandboxes/snapshots",
            query=query,
            path_model=GetProjectSnapshotsPath,
            query_model=GetProjectSnapshotsQuery,
            response_model=GetProjectSnapshotsResponse,
        )

    async def add_sandbox_snapshot(self, *, path: Mapping[str, Any], body: Any) -> SnapshotView:
        """Create a snapshot of a sandbox."""
        return await self._request_with_validation(
            method="POST",
            url="/workspaces/{workspace_domain}/sandboxes/{sandbox_id}/snapshots",
            path=path,
            body=body,
            idempotent=False,
            body_model=AddSandboxSnapshotBody,
            path_model=AddSandboxSnapshotPath,
            response_model=AddSandboxSnapshotResponse,
        )

    async def get_sandbox_snapshot(self, *, path: Mapping[str, Any]) -> SnapshotView:
        """Get a single sandbox snapshot by ID."""
        return await self._request_with_validation(
            method="GET",
            url="/workspaces/{workspace_domain}/sandboxes/{sandbox_id}/snapshots/{id}",
            path=path,
            path_model=GetSandboxSnapshotPath,
            response_model=GetSandboxSnapshotResponse,
        )

    async def delete_sandbox_snapshot(self, *, path: Mapping[str, Any]) -> None:
        """Delete a sandbox snapshot by ID, ignoring an already-deleted one."""
        try:
            await self._request_with_validation(
                method="DELETE",
                url="/workspaces/{workspace_domain}/sandboxes/{sandbox_id}/snapshots/{id}",
                path=path,
                path_model=DeleteSandboxSnapshotPath,
                response_model=DeleteSandboxSnapshotResponse,
                skip_retry=True,
            )
        except HttpError as error:
            # Ignore 404 errors - snapshot already deleted.
            if error.status == 404:
                return
            raise

    async def delete_snapshot(self, *, path: Mapping[str, Any]) -> None:
        """Delete a snapshot by ID at the project level.

        Works for snapshots whose parent sandbox has been deleted.
        """
        try:
            await self._request_with_validation(
                method="DELETE",
                url="/workspaces/{workspace_domain}/sandboxes/snapshots/{id}",
                path=path,
                path_model=DeleteSnapshotPath,
                response_model=DeleteSnapshotResponse,
                skip_retry=True,
            )
        except HttpError as error:
            # Ignore 404 errors - snapshot already deleted.
            if error.status == 404:
                return
            raise

    # ------------------------------------------------------------------
    # Content
    # ------------------------------------------------------------------

    async def get_sandbox_content(self, *, path: Mapping[str, Any]) -> SandboxContentView:
        """Get sandbox content (list files and directories at a path)."""
        return await self._request_with_validation(
            method="GET",
            url="/workspaces/{workspace_domain}/sandboxes/{sandbox_id}/content/{path}",
            path=path,
            path_model=GetSandboxContentPath,
            response_model=GetSandboxContentResponse,
        )

    async def delete_sandbox_file(self, *, path: Mapping[str, Any]) -> None:
        """Delete a file or directory from a sandbox."""
        return await self._request_with_validation(
            method="DELETE",
            url="/workspaces/{workspace_domain}/sandboxes/{sandbox_id}/content/{path}",
            path=path,
            path_model=DeleteSandboxFilePath,
            response_model=DeleteSandboxFileResponse,
        )

    async def create_sandbox_directory(self, *, path: Mapping[str, Any]) -> SandboxContentItem:
        """Create a directory in a sandbox."""
        return await self._request_with_validation(
            method="POST",
            url="/workspaces/{workspace_domain}/sandboxes/{sandbox_id}/content/{path}",
            path=path,
            path_model=CreateSandboxDirectoryPath,
            response_model=CreateSandboxDirectoryResponse,
        )

    async def upload_sandbox_file(
        self, *, path: Mapping[str, Any], body: bytes
    ) -> SandboxContentItem:
        """Upload a file to a sandbox.

        Sent outside :meth:`_request_with_validation`: multipart needs its own
        headers, and an upload is neither retried nor cut off part-way, however
        long the payload takes.
        """
        url = self._absolute_url(
            "/workspaces/{workspace_domain}/sandboxes/{sandbox_id}/content/upload/{path}",
            UploadSandboxFilePath,
            path,
        )

        filename = str(path["path"]).split("/")[-1] or "file"

        # No Content-Type here - httpx sets it with the multipart boundary.
        headers = {"Authorization": f"Bearer {self._token}"}

        if self.debug_mode:
            logger.debug(
                "[HTTP REQUEST - Upload]",
                {"method": "POST", "url": str(url), "headers": {**headers, "Authorization": "***"}},
            )

        # Typed, not guessed from the extension.
        response = await self._http.post(
            url, headers=headers, files={"file": (filename, body, "application/octet-stream")}
        )

        if not response.is_success:
            raise HttpError(
                f"Failed to upload file: {response.reason_phrase}", response.status_code
            )

        try:
            return UploadSandboxFileResponse.model_validate(response.json()).root
        except ValidationError as error:
            raise HttpError(
                f"Response validation failed:\n{error}", response.status_code
            ) from error

    async def download_sandbox_content(self, *, path: Mapping[str, Any]) -> tuple[bytes, str]:
        """Download content from a sandbox (a file, or a directory as tar.gz).

        Like an upload, it is neither retried nor cut off part-way.
        """
        url = self._absolute_url(
            "/workspaces/{workspace_domain}/sandboxes/{sandbox_id}/download/{path}",
            DownloadSandboxContentPath,
            path,
        )

        headers = {"Accept": "application/octet-stream", "Authorization": f"Bearer {self._token}"}

        if self.debug_mode:
            logger.debug(
                "[HTTP REQUEST - Download]",
                {"method": "GET", "url": str(url), "headers": {**headers, "Authorization": "***"}},
            )

        response = await self._http.get(url, headers=headers)

        if not response.is_success:
            raise HttpError(
                f"Failed to download content: {response.reason_phrase}", response.status_code
            )

        filename = "download"
        content_disposition = response.headers.get("Content-Disposition")
        if content_disposition:
            match = re.search(r'filename="?([^";\n]+)"?', content_disposition)
            if match:
                filename = match.group(1)

        return response.content, filename

    # ------------------------------------------------------------------
    # Streaming
    # ------------------------------------------------------------------

    async def stream_command_logs(
        self, *, path: Mapping[str, Any], query: Mapping[str, Any] | None = None
    ) -> AsyncGenerator[SandboxCommandLog, None]:
        """Stream logs from a specific command execution."""
        url = self._absolute_url(
            "/workspaces/{workspace_domain}/sandboxes/{sandbox_id}/commands/{command_id}/logs",
            GetSandboxCommandLogsPath,
            path,
        )

        validated_query = GetSandboxCommandLogsQuery.model_validate(_omit_none(query))

        if validated_query.follow is not None:
            url = url.copy_set_param("follow", validated_query.follow)

        headers = {
            "Accept": "application/jsonl",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._token}",
        }

        async with self._http.stream("GET", url, headers=headers) as response:
            if self.debug_mode:
                logger.debug(
                    "[HTTP REQUEST - Streaming]",
                    {
                        "method": "GET",
                        "url": str(url),
                        "headers": {**headers, "Authorization": "***"},
                    },
                )

            if not response.is_success:
                await response.aread()
                raise HttpError(
                    f"Failed to stream logs: {response.reason_phrase}", response.status_code
                )

            content_type = response.headers.get("content-type")
            if not content_type or "application/jsonl" not in content_type:
                raise ValueError(
                    f"Expected application/jsonl content type, got: {content_type or 'none'}"
                )

            # Buffered by hand rather than with aiter_lines(), which also splits
            # on \r, \v, \f and U+2028 - the last of which is legal raw inside a
            # JSON string and would tear a log line in half.
            decoder = codecs.getincrementaldecoder("utf-8")()
            buffer = ""

            async for chunk in response.aiter_bytes():
                buffer += decoder.decode(chunk)
                *lines, buffer = buffer.split("\n")

                for line in lines:
                    if not line.strip():
                        continue

                    entry = self._parse_log_entry(line)

                    if self.debug_mode:
                        logger.debug(f"[STREAM] {entry.type}", {"content": entry.data})

                    yield entry

            buffer += decoder.decode(b"", final=True)

            if buffer.strip():
                yield self._parse_log_entry(buffer)

    def _parse_log_entry(self, line: str) -> SandboxCommandLog:
        """Parse a JSON line and validate it as a command log entry."""
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"Failed to parse log entry as JSON: {error}. Line: {line}") from error

        return SandboxCommandLog.model_validate(parsed)


def _env_token() -> str | None:
    """Read the fallback token lazily, so tests can change it between clients."""
    return environment.BUDDY_TOKEN
