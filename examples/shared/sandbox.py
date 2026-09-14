"""Getting hold of the sandbox an example works on."""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from typing import Any

from buddy_sandbox import Sandbox
from examples.shared.logger import log


@contextlib.asynccontextmanager
async def get_or_create(identifier: str, **config: Any) -> AsyncIterator[Sandbox]:
    """Reuse the example's sandbox if it is still around, else make one.

    Stops it again on the way out, so re-running the example is cheap.
    """
    log(f"Getting or creating sandbox: {identifier}")

    try:
        sandbox = await Sandbox.get_by_identifier(identifier)
        log(f"Found existing sandbox: {sandbox.data.identifier} ({sandbox.data.html_url})")
        await sandbox.start()
    except Exception:
        log("Creating new sandbox...")
        sandbox = await Sandbox.create(identifier=identifier, **config)
        log(f"Created sandbox: {sandbox.data.identifier} ({sandbox.data.html_url})")

    try:
        yield sandbox
    finally:
        log("\nStopping sandbox...")
        with contextlib.suppress(Exception):
            await sandbox.stop()


@contextlib.asynccontextmanager
async def fresh(identifier: str, **config: Any) -> AsyncIterator[Sandbox]:
    """Make a sandbox from scratch, replacing any leftover of the same name.

    Destroys it on the way out.
    """
    with contextlib.suppress(Exception):
        existing = await Sandbox.get_by_identifier(identifier)
        log(f"Found existing sandbox with identifier: {identifier}, deleting...")
        await existing.destroy()

    sandbox = await Sandbox.create(identifier=identifier, **config)
    log(f"Created sandbox: {sandbox.data.identifier} ({sandbox.data.html_url})")

    try:
        yield sandbox
    finally:
        log("\nCleaning up...")
        with contextlib.suppress(Exception):
            await sandbox.destroy()
        log("Sandbox deleted successfully")
