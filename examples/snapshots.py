"""Snapshotting a sandbox and restoring a new one from it."""

from __future__ import annotations

import contextlib
import time
from datetime import UTC, datetime

from buddy_sandbox import Sandbox, Snapshot
from examples.shared.logger import log
from examples.shared.run import run

BASE_IDENTIFIER = "snapshot-demo-base"
RESTORED_IDENTIFIER = "snapshot-demo-restored"


async def main() -> None:
    log("Snapshots Example\n")

    for identifier in (BASE_IDENTIFIER, RESTORED_IDENTIFIER):
        with contextlib.suppress(Exception):
            existing = await Sandbox.get_by_identifier(identifier)
            log(f"Found existing sandbox '{identifier}', deleting...")
            await existing.destroy()

    base: Sandbox | None = None
    snapshot: Snapshot | None = None
    restored: Sandbox | None = None

    try:
        log("Creating base sandbox...")
        base = await Sandbox.create(
            identifier=BASE_IDENTIFIER, name="Snapshot Demo Base", os="ubuntu:24.04"
        )
        log(f"Created base sandbox: {base.data.identifier} ({base.data.html_url})\n")

        log("Uploading a marker file to the base sandbox...")
        marker_name = "snapshot-marker.txt"
        marker_content = f"created at {datetime.now(UTC).isoformat()}".encode()
        await base.fs.upload_file(marker_content, marker_name)
        log(f"Wrote '{marker_name}' with content: {marker_content.decode()}\n")

        log("Creating a snapshot (returns immediately with status CREATING)...")
        snapshot = await base.create_snapshot(name="demo-snapshot")
        log(f"Snapshot id={snapshot.id} initial status={snapshot.data.status}\n")

        log("Waiting until the snapshot is CREATED...")
        started = time.monotonic()
        await snapshot.wait_until_ready()
        elapsed = int((time.monotonic() - started) * 1000)
        log(f"Snapshot ready in {elapsed}ms (status={snapshot.data.status})\n")

        log("Listing snapshots for the base sandbox:")
        for entry in await base.list_snapshots():
            log(f"  {entry.id}  {entry.data.name}  {entry.data.status}")

        log("\nCreating a new sandbox from the snapshot...")
        restored = await Sandbox.create_from_snapshot(
            snapshot.id, identifier=RESTORED_IDENTIFIER, name="Snapshot Demo Restored"
        )
        log(f"Restored sandbox: {restored.data.identifier} ({restored.data.html_url})")
        log(f"Status: {restored.data.status}\n")

        log("Verifying the marker file is present in the restored sandbox...")
        downloaded = await restored.fs.download_file(marker_name)
        log(f"Marker content matches: {downloaded == marker_content}\n")
    finally:
        log("Cleaning up...")
        for resource in (restored, snapshot, base):
            if resource is not None:
                with contextlib.suppress(Exception):
                    await (
                        resource.destroy() if isinstance(resource, Sandbox) else resource.delete()
                    )
        log("All resources deleted successfully")


if __name__ == "__main__":
    run(main)
