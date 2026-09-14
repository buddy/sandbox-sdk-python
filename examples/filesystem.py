"""Every file system operation, end to end."""

from __future__ import annotations

import asyncio

from buddy_sandbox import FileSystem
from examples.shared.logger import log
from examples.shared.run import run
from examples.shared.sandbox import get_or_create

IDENTIFIER = "filesystem-demo-sandbox"


async def main() -> None:
    log("FileSystem Operations Example\n")
    async with get_or_create(
        IDENTIFIER, name="FileSystem Demo Sandbox", os="ubuntu:24.04"
    ) as sandbox:
        await sandbox.start()

        log("\n=== Direct FileSystem Usage ===")
        log("Creating a FileSystem straight from the sandbox ID...")

        direct = FileSystem.for_sandbox(sandbox.initialized_id)
        home = await direct.list_files("/home")
        log(f"Direct FileSystem found {len(home)} items in /home:")
        for entry in home:
            log(f"  {'[DIR]' if entry.type == 'DIR' else '[FILE]'} {entry.name}")

        log("\n=== Example 1: Listing Files ===")
        log("Listing the contents of /etc (first 10 items)...")

        entries = await sandbox.fs.list_files("/etc")
        log(f"Found {len(entries)} items:")
        for entry in entries[:10]:
            size = f" ({entry.size} bytes)" if entry.size else ""
            log(f"  {'[DIR]' if entry.type == 'DIR' else '[FILE]'} {entry.name}{size}")
        if len(entries) > 10:
            log(f"  ... and {len(entries) - 10} more")

        log("\n=== Example 2: Creating Directories ===")
        log("Creating the directory structure /buddy/demo/data")

        await sandbox.fs.create_folder("/buddy/demo")
        await sandbox.fs.create_folder("/buddy/demo/data")

        log("\n=== Example 3: Uploading Files ===")

        await sandbox.fs.upload_file(b"Hello from the SDK!\n", "/buddy/demo/hello.txt")
        log("Uploaded /buddy/demo/hello.txt")

        await asyncio.gather(
            sandbox.fs.upload_file(b'{"answer": 42}\n', "/buddy/demo/data/config.json"),
            sandbox.fs.upload_file(b"one,two\n1,2\n", "/buddy/demo/data/rows.csv"),
        )
        log("Uploaded two more files in parallel")

        log("\n=== Example 4: Downloading Files ===")

        content = await sandbox.fs.download_file("/buddy/demo/hello.txt")
        log(f"Downloaded /buddy/demo/hello.txt: {content.decode().strip()}")

        log("\n=== Example 5: Deleting Files ===")

        await sandbox.fs.delete_file("/buddy/demo/data/rows.csv")
        log("Deleted /buddy/demo/data/rows.csv")

        remaining = await sandbox.fs.list_files("/buddy/demo/data")
        log(f"Remaining in /buddy/demo/data: {', '.join(entry.name for entry in remaining)}")

        await sandbox.fs.delete_file("/buddy")
        log("Deleted /buddy and everything under it")
    log("FileSystem example completed!")


if __name__ == "__main__":
    run(main)
