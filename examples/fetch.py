"""Cloning a public repository into a sandbox on first boot."""

from __future__ import annotations

from examples.shared.logger import log
from examples.shared.run import run
from examples.shared.sandbox import fresh

IDENTIFIER = "fetch-demo-sandbox"


async def main() -> None:
    log("Fetch Example\n")

    log("Creating sandbox that clones a public repository on first boot...")
    async with fresh(
        IDENTIFIER,
        name="Fetch Demo Sandbox",
        os="ubuntu:24.04",
        fetch=[
            {
                "type": "PUBLIC_REPO",
                "repository": "https://github.com/octocat/Hello-World",
                "ref": "master",
                "path": "/workspace/hello-world",
            }
        ],
    ) as sandbox:
        log("Listing the cloned directory:")
        command = await sandbox.run_command(
            command="ls -la /workspace/hello-world", stdout=None, stderr=None
        )
        log(await command.stdout())


if __name__ == "__main__":
    run(main)
