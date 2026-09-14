"""List every sandbox in the current scope."""

from __future__ import annotations

from buddy_sandbox import Sandbox
from examples.shared.logger import log
from examples.shared.run import run


async def main() -> None:
    log("List Sandboxes Example\n")
    log("Fetching the sandboxes in the configured scope...\n")

    sandboxes = await Sandbox.list()

    if not sandboxes:
        log("No sandboxes found in this scope.")
    else:
        log(f"Found {len(sandboxes)} sandbox(es):\n")

        for sandbox in sandboxes:
            log(f"  - {sandbox.name}")
            log(f"    ID: {sandbox.id}")
            log(f"    Identifier: {sandbox.identifier}")
            log(f"    Status: {sandbox.status}")
            log(f"    URL: {sandbox.html_url}\n")

    log("List example completed!")


if __name__ == "__main__":
    run(main)
