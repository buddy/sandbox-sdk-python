"""Changing a sandbox's configuration in place."""

from __future__ import annotations

from examples.shared.logger import log
from examples.shared.run import run
from examples.shared.sandbox import fresh

IDENTIFIER = "update-demo-sandbox"


async def main() -> None:
    log("Sandbox Update Example\n")

    log("Creating sandbox with initial config...")
    async with fresh(
        IDENTIFIER,
        name="Update Demo Sandbox",
        os="ubuntu:24.04",
        timeout=300,
        tags=["initial"],
        apps=["echo initial-app"],
    ) as sandbox:
        log(f"Initial timeout: {sandbox.data.timeout}")
        log(f"Initial tags: {', '.join(sandbox.data.tags or [])}")
        log(f"Initial apps: {', '.join(app.command or '' for app in sandbox.data.apps or [])}\n")

        log("Updating timeout (300 -> 1200), tags and apps in place...")
        await sandbox.update(
            timeout=1200, tags=["updated", "demo"], apps=[{"command": "echo updated-app"}]
        )

        log(f"Updated timeout: {sandbox.data.timeout}")
        log(f"Updated tags: {', '.join(sandbox.data.tags or [])}")
        log(f"Updated apps: {', '.join(app.command or '' for app in sandbox.data.apps or [])}")
        log(f"Sandbox status: {sandbox.data.status} (setup: {sandbox.data.setup_status})\n")


if __name__ == "__main__":
    run(main)
