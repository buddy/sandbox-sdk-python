"""Extra scopes the integration suite exercises when they are configured."""

from __future__ import annotations

import os

from buddy_sandbox import ConnectionConfig

_workspace = os.environ.get("BUDDY_TEST_WORKSPACE")
_project = os.environ.get("BUDDY_TEST_PROJECT")

workspace_environment = os.environ.get("BUDDY_TEST_ENVIRONMENT")
project_environment = os.environ.get("BUDDY_TEST_PROJECT_ENVIRONMENT")

workspace_connection = ConnectionConfig(workspace=_workspace) if _workspace else None

workspace_environment_connection = (
    ConnectionConfig(workspace=_workspace, environment=workspace_environment)
    if _workspace and workspace_environment
    else None
)

project_environment_connection = (
    ConnectionConfig(workspace=_workspace, project=_project, environment=project_environment)
    if _workspace and _project and project_environment
    else None
)
