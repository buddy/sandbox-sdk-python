"""Long-running apps inside a sandbox."""

from __future__ import annotations

import asyncio

from examples.shared.logger import log
from examples.shared.run import run
from examples.shared.sandbox import fresh

IDENTIFIER = "apps-demo-sandbox"


async def main() -> None:
    log("Sandbox Apps Example\n")

    log("Creating sandbox with 2 apps...")
    async with fresh(
        IDENTIFIER,
        name="Apps Demo Sandbox",
        os="ubuntu:24.04",
        first_boot_commands="apt-get update && apt-get install -y curl",
        apps=[
            'while true; do echo "[app1] ping $(date)"; sleep 2; done',
            'while true; do echo "[app2] pong $(date)"; sleep 3; done',
        ],
    ) as sandbox:
        log(f"Status: {sandbox.data.status}, Setup: {sandbox.data.setup_status}\n")

        log("Apps:")
        for app in sandbox.data.apps or []:
            log(f'  {app.id}: "{app.command}" -> {app.app_status}')

        apps = sandbox.data.apps or []
        if len(apps) < 2 or not apps[0].id or not apps[1].id:
            raise RuntimeError("Expected 2 apps in sandbox")

        first, second = apps[0].id, apps[1].id

        log(f"\nStopping the first app ({first})...")
        await sandbox.stop_app(first)
        log("Apps after stopping the first one:")
        for app in sandbox.data.apps or []:
            log(f"  {app.id}: {app.app_status}")

        log(f"\nStarting the first app ({first})...")
        await sandbox.start_app(first)
        log("Apps after starting the first one:")
        for app in sandbox.data.apps or []:
            log(f"  {app.id}: {app.app_status}")

        log("\nWaiting a few seconds for log output...")
        await asyncio.sleep(5)

        log(f"Getting logs for the second app ({second})...")
        logs = await sandbox.get_app_logs(second)
        log(f"Logs ({len(logs.logs or [])} entries):")
        for entry in logs.logs or []:
            log(f"  {entry}")


if __name__ == "__main__":
    run(main)
