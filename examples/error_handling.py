"""Patterns for reacting to a command that fails."""

from __future__ import annotations

from buddy_sandbox import Command, Sandbox
from examples.shared.logger import log
from examples.shared.run import run
from examples.shared.sandbox import get_or_create

IDENTIFIER = "error-handling-dev-sandbox"


async def run_or_raise(sandbox: Sandbox, command: str) -> Command:
    """Turn a non-zero exit code into an exception."""
    result = await sandbox.run_command(command=command, stdout=None, stderr=None)

    if result.data.exit_code != 0:
        stderr = await result.stderr()
        raise RuntimeError(f'Command "{command}" failed: {stderr.strip()}')

    return result


async def main() -> None:
    log("Error Handling Example\n")
    async with get_or_create(
        IDENTIFIER, name="Error Handling Sandbox", os="ubuntu:24.04"
    ) as sandbox:
        log("Starting sandbox...")
        await sandbox.start()

        log("\n=== Example 1: Basic exit code check ===")
        log("Running: ls /nonexistent-directory\n")

        result = await sandbox.run_command(command="ls /nonexistent-directory")

        if result.data.exit_code != 0:
            log(f"Command failed with exit code {result.data.exit_code}")

        log("\n=== Example 2: Capture stderr ===")
        log("Running: cat /file/that/does/not/exist\n")

        result = await sandbox.run_command(
            command="cat /file/that/does/not/exist", stdout=None, stderr=None
        )

        if result.data.exit_code != 0:
            log(f"Command failed: {(await result.stderr()).strip()}")

        log("\n=== Example 3: Raise on failure ===")
        log("Running: false (always exits with 1)\n")

        try:
            await run_or_raise(sandbox, "false")
        except RuntimeError as error:
            log(f"Caught error: {error}")

        log("\n=== Example 4: Expected failures ===")
        log("Running: echo 'hello world' | grep 'nonexistent'\n")

        grep = await sandbox.run_command(
            command="echo 'hello world' | grep 'nonexistent'", stdout=None, stderr=None
        )

        # grep exits 1 when nothing matches, which is an answer rather than an error.
        if grep.data.exit_code == 1:
            log("No matches found (expected)")
        elif grep.data.exit_code == 0:
            log("Found matches")
        else:
            log(f"Unexpected grep error: exit code {grep.data.exit_code}")
    log("Error handling example completed!")


if __name__ == "__main__":
    run(main)
