"""Three ways to run commands: streaming, in parallel, and fire-and-forget."""

from __future__ import annotations

import asyncio
import time

from buddy_sandbox import Command
from examples.shared.logger import log
from examples.shared.run import run
from examples.shared.sandbox import get_or_create

IDENTIFIER = "ping-dev-sandbox"


async def main() -> None:
    log("Ping Sandbox Example\n")
    async with get_or_create(IDENTIFIER, name="My Ping Sandbox", os="ubuntu:24.04") as sandbox:
        await sandbox.start()

        log("\n=== Mode 1: Real-time streaming ===")
        log("Output appears as the command runs:\n")

        await sandbox.run_command(command="ping -c 4 buddy.works", runtime="BASH")

        log("\n=== Mode 2: Parallel execution ===")
        log("Run multiple commands simultaneously, results print as they complete.")

        slow, fast = await asyncio.gather(
            sandbox.run_command(
                command="echo 'Starting slow task...' && sleep 3 && echo 'Slow task done!'",
                runtime="BASH",
                detached=True,
                stdout=None,
                stderr=None,
            ),
            sandbox.run_command(
                command="echo 'Starting fast task...' && sleep 1 && echo 'Fast task done!'",
                runtime="BASH",
                detached=True,
                stdout=None,
                stderr=None,
            ),
        )

        log("Both commands started in background! Waiting for results...\n")

        async def report(label: str, command: Command) -> None:
            finished = await command.wait()
            log(f"[{label}] Exit code: {finished.data.exit_code}")
            log(f"[{label}] Output:\n{await finished.output()}")

        await asyncio.gather(report("Slow", slow), report("Fast", fast))

        log("\n=== Mode 3: Fire and forget ===")
        log("Commands run on the server independently - you can check back anytime.")
        log("Launching a 2-second task, then doing other work for 5 seconds...\n")

        detached = await sandbox.run_command(
            command="echo 'Quick task' && sleep 2 && echo 'Done!'",
            runtime="BASH",
            detached=True,
            stdout=None,
            stderr=None,
        )

        log("Command running on server. Doing other work locally...")
        await asyncio.sleep(5)

        log("Done with local work. Checking if server command finished...")
        started = time.monotonic()
        finished = await detached.wait()
        elapsed = int((time.monotonic() - started) * 1000)
        log(f"Status check took {elapsed}ms (instant - command already done!)")
        log(f"Output:\n{await finished.output()}")
    log("Ping example completed!")


if __name__ == "__main__":
    run(main)
