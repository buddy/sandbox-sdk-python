"""Turning a connection config into a configured API client."""

from __future__ import annotations

from dataclasses import dataclass

from buddy_sandbox.core.buddy_api_client import BuddyApiClient
from buddy_sandbox.utils.environment import environment
from buddy_sandbox.utils.regions import API_URLS, Region, get_api_url_from_region, parse_region

#: Setting any of these decides the placement, so the env vars step aside.
PLACEMENT_FIELDS = ("workspace", "project", "environment", "environment_id")


@dataclass(slots=True)
class ConnectionConfig:
    """Connection configuration for workspace and API authentication."""

    workspace: str | None = None
    """Workspace name/slug (falls back to the BUDDY_WORKSPACE env var)."""

    project: str | None = None
    """Project name/slug (falls back to BUDDY_PROJECT).

    Combined with ``environment`` it only says where to look that identifier up.
    """

    environment: str | None = None
    """Environment identifier (falls back to the BUDDY_ENVIRONMENT env var)."""

    environment_id: str | None = None
    """Environment ID - same as ``environment``, but skips the identifier lookup."""

    token: str | None = None
    """API authentication token (falls back to the BUDDY_TOKEN env var)."""

    region: Region | None = None
    """API region: US, EU or AS (falls back to the BUDDY_REGION env var)."""

    api_url: str | None = None
    """Custom API URL for testing (falls back to the BUDDY_API_URL env var)."""


@dataclass(slots=True)
class _ScopeSource:
    project: str | None
    environment: str | None
    environment_id: str | None


def _resolve_scope_source(connection: ConnectionConfig | None) -> _ScopeSource:
    """Resolve where sandboxes are placed.

    A connection that sets a workspace, a project or an environment decides the
    placement on its own, so ``ConnectionConfig(workspace=...)`` alone means that
    workspace and nothing below it. The env vars apply only when the connection
    sets none of the three - ``token`` and friends do not count, overriding the
    credentials should not move sandboxes.
    """
    if connection is not None and any(
        getattr(connection, name) is not None for name in PLACEMENT_FIELDS
    ):
        return _ScopeSource(
            project=connection.project,
            environment=connection.environment,
            environment_id=connection.environment_id,
        )

    return _ScopeSource(
        project=environment.BUDDY_PROJECT,
        environment=environment.BUDDY_ENVIRONMENT,
        environment_id=None,
    )


def _resolve_api_url(connection: ConnectionConfig | None) -> str:
    if connection is not None and connection.api_url:
        return connection.api_url
    if environment.BUDDY_API_URL:
        return environment.BUDDY_API_URL
    if connection is not None and connection.region:
        return get_api_url_from_region(parse_region(connection.region))
    if environment.BUDDY_REGION:
        return get_api_url_from_region(parse_region(environment.BUDDY_REGION))
    return API_URLS["US"]


def create_client(connection: ConnectionConfig | None = None) -> BuddyApiClient:
    """Create a :class:`BuddyApiClient` from a connection config."""
    if connection is not None:
        for name in PLACEMENT_FIELDS:
            if getattr(connection, name) == "":
                raise ValueError(
                    f"connection.{name} is empty. Leave it out to fall back to the env vars."
                )

    workspace = (connection.workspace if connection else None) or environment.BUDDY_WORKSPACE

    if not workspace:
        raise ValueError(
            "Workspace not found. Set workspace in config.connection or BUDDY_WORKSPACE env var."
        )

    scope = _resolve_scope_source(connection)

    return BuddyApiClient(
        workspace=workspace,
        api_url=_resolve_api_url(connection),
        project_name=scope.project or None,
        environment=scope.environment or None,
        environment_id=scope.environment_id or None,
        token=(connection.token if connection else None) or None,
    )
