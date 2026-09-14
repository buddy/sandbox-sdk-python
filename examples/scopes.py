"""Where a sandbox lands: a project, an environment, or the workspace itself.

A sandbox lives in a project, in an environment, or directly in the workspace,
and that follows from what the connection names. Each part below names its own,
so the labels hold whatever your environment contains.

Creating one outside a project needs workspace admin rights.
"""

from __future__ import annotations

import os
import sys
import time

from buddy_sandbox import ConnectionConfig, Sandbox
from examples.shared.logger import log
from examples.shared.run import run


async def main() -> None:
    workspace_name = os.environ.get("BUDDY_WORKSPACE")
    project_name = os.environ.get("BUDDY_PROJECT")

    # Passed as arguments rather than env vars: BUDDY_ENVIRONMENT is read by the
    # SDK itself and would move every other example into that environment.
    environment_identifier = sys.argv[1] if len(sys.argv) > 1 else None
    environment_project = sys.argv[2] if len(sys.argv) > 2 else None

    log("Sandbox Scopes Example\n")

    if project_name:
        log(f"Project scope ({project_name}):")
        sandboxes = await Sandbox.list(connection=ConnectionConfig(project=project_name))
        log(f"  {len(sandboxes)} sandbox(es) in the project\n")
    else:
        log("Project scope skipped - set BUDDY_PROJECT to try it.\n")

    if workspace_name:
        log("Workspace scope (no project, no environment):")
        sandboxes = await Sandbox.list(connection=ConnectionConfig(workspace=workspace_name))
        log(f"  {len(sandboxes)} workspace-level sandbox(es)\n")

    if environment_identifier:
        log(f"Environment scope ({environment_identifier}):")

        # A project-level environment is only found through its project.
        connection = (
            ConnectionConfig(project=environment_project, environment=environment_identifier)
            if environment_project
            else ConnectionConfig(environment=environment_identifier)
        )

        sandbox = await Sandbox.create(
            name="Scoped sandbox",
            identifier=f"scoped_sandbox_{int(time.time() * 1000)}",
            os="ubuntu:24.04",
            connection=connection,
        )

        environment = sandbox.data.environment

        log(f"  Scope: {sandbox.data.scope}")
        log(f"  Environment: {environment.identifier if environment else None}")

        await sandbox.destroy()
        log("  Cleaned up\n")
    else:
        log(
            "Environment scope skipped - run `python -m examples.scopes <environment> [project]`.\n"
        )

    log("Scopes example completed!")


if __name__ == "__main__":
    run(main)
