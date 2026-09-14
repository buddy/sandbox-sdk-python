"""Walk a sandbox through its whole state machine."""

from __future__ import annotations

from examples.shared.logger import log
from examples.shared.run import run
from examples.shared.sandbox import fresh

IDENTIFIER = "lifecycle-demo-sandbox"


async def main() -> None:
    log("Sandbox Lifecycle Example\n")

    log(f"Creating sandbox with identifier: {IDENTIFIER}")

    async with fresh(IDENTIFIER, name="Lifecycle Demo Sandbox", os="ubuntu:24.04") as sandbox:
        log(f"Status: {sandbox.data.status}, Setup: {sandbox.data.setup_status}\n")

        log("Running command: uptime")
        await sandbox.run_command(command="uptime")

        log("\nStopping sandbox...")
        await sandbox.stop()
        log(f"Sandbox stopped. Status: {sandbox.data.status}\n")

        log("Starting sandbox...")
        await sandbox.start()
        log(f"Sandbox started. Status: {sandbox.data.status}\n")

        log("Running command: df -h")
        await sandbox.run_command(command="df -h")

        log("\nRestarting sandbox...")
        await sandbox.restart()
        log(f"Sandbox restarted. Status: {sandbox.data.status}\n")

        log("Running final command: hostname")
        await sandbox.run_command(command="hostname")


if __name__ == "__main__":
    run(main)
