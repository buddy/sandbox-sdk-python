"""Run a command and get its result in a single call."""

from __future__ import annotations

from examples.shared.logger import log
from examples.shared.run import run
from examples.shared.sandbox import get_or_create

IDENTIFIER = "exec-demo-sandbox"


async def main() -> None:
    log("Command Exec Example\n")
    async with get_or_create(IDENTIFIER, name="Exec Demo Sandbox", os="ubuntu:24.04") as sandbox:
        log("\n=== Example 1: Output and exit code in one call ===")

        hello = await sandbox.exec(command="echo 'Hello from the sandbox'")

        log(f"Exit code: {hello.exit_code}")
        log(f"Stdout: {(hello.stdout or '').strip()}")

        log("\n=== Example 2: A failing command still returns ===")
        log("Check exit_code to see how it went:\n")

        failed = await sandbox.exec(command="echo 'something broke' >&2 && exit 3")

        log(f"Exit code: {failed.exit_code}")
        log(f"Stderr: {(failed.stderr or '').strip()}")

        if failed.exit_code != 0:
            log("Handled the failure without a try/except.")

        log("\n=== Example 3: Another runtime ===")

        python = await sandbox.exec(command="print('computed in python:', 6 * 7)", runtime="PYTHON")

        log(f"Stdout: {(python.stdout or '').strip()}")

        log("\n=== Example 4: exec() leaves no trace ===")

        before = await sandbox.list_commands()
        await sandbox.exec(command="echo untracked")
        after = await sandbox.list_commands()

        log(f"Commands in history before: {len(before)}, after: {len(after)}")
        log("Use run_command() for history, streamed logs, or over 60 seconds.")
    log("Exec example completed!")


if __name__ == "__main__":
    run(main)
