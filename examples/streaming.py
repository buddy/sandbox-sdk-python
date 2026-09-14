"""Streaming command output, automatically and by hand."""

from __future__ import annotations

import sys

from examples.shared.logger import log
from examples.shared.run import run
from examples.shared.sandbox import get_or_create

IDENTIFIER = "streaming-demo-sandbox"


async def main() -> None:
    log("Command Streaming Example\n")
    async with get_or_create(
        IDENTIFIER, name="Streaming Demo Sandbox", os="ubuntu:24.04"
    ) as sandbox:
        log("\n=== Example 1: Auto-streaming ===")
        log("Output streams to the console as the command runs:\n")

        await sandbox.run_command(command='for i in 1 2 3; do echo "Line $i"; sleep 1; done')

        log("\n=== Example 2: Custom log formatting ===")
        log("Iterate over the logs yourself to add custom prefixes:\n")

        command = await sandbox.run_command(
            command='echo "stdout message" && echo "stderr message" >&2 && echo "another stdout"',
            detached=True,
            stdout=None,
            stderr=None,
        )

        async for entry in command.logs(follow=True):
            prefix = "[OUT]" if entry.type == "STDOUT" else "[ERR]"
            sys.stdout.write(f"{prefix} {entry.data}\n")

        await command.wait()
    log("Streaming example completed!")


if __name__ == "__main__":
    run(main)
