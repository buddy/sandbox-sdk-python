"""Run the examples.

    python -m examples              # all of them, at once
    python -m examples ping list    # just these
    python -m examples --jobs 2     # throttled, for an API with tight rate limits

Each one runs in its own process so a crash cannot take the others down, and
their output is streamed as it arrives, labelled with the example it came from.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

HERE = Path(__file__).parent

EXCLUDED = {"__init__", "__main__", "shared"}


def available() -> list[str]:
    return sorted(p.stem for p in HERE.glob("*.py") if p.stem not in EXCLUDED)


HEARTBEAT_SECONDS = 30


async def report_pending(pending: set[str]) -> None:
    """Say what is still going, so a stuck example is never a mystery."""
    while pending:
        await asyncio.sleep(HEARTBEAT_SECONDS)
        if pending:
            print(f"\n... still running: {', '.join(sorted(pending))}\n", flush=True)


async def run(name: str, width: int, pending: set[str]) -> tuple[str, int]:
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        # Unbuffered, or the child would hold its output back until it exits.
        "-u",
        "-m",
        f"examples.{name}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )

    assert process.stdout is not None

    async for line in process.stdout:
        print(f"{name:<{width}} | {line.decode(errors='replace').rstrip()}", flush=True)

    code = await process.wait()
    pending.discard(name)

    return name, code


def parse_jobs(argv: list[str]) -> tuple[int, list[str]]:
    """Pull ``--jobs N`` out of the arguments, leaving the example names.

    Zero means no limit. A sandbox is polled hard while it boots, so throttling
    is worth it against an API with tight rate limits.
    """
    if "--jobs" not in argv:
        return 0, argv

    index = argv.index("--jobs")

    if index + 1 >= len(argv):
        raise SystemExit("--jobs needs a number")

    jobs = int(argv[index + 1])

    return jobs, argv[:index] + argv[index + 2 :]


async def main() -> int:
    jobs, names = parse_jobs(sys.argv[1:])
    requested = names or available()

    unknown = sorted(set(requested) - set(available()))
    if unknown:
        print(f"Unknown example(s): {', '.join(unknown)}", file=sys.stderr)
        print(f"Available: {', '.join(available())}", file=sys.stderr)
        return 2

    width = max(len(name) for name in requested)

    throttle = f" ({jobs} at a time)" if jobs else ""
    print(f"Running {len(requested)}{throttle}: {', '.join(requested)}\n", flush=True)

    pending = set(requested)
    heartbeat = asyncio.create_task(report_pending(pending))
    limit = asyncio.Semaphore(jobs or len(requested))

    async def guarded(name: str) -> tuple[str, int]:
        async with limit:
            return await run(name, width, pending)

    try:
        results = await asyncio.gather(*(guarded(name) for name in requested))
    finally:
        heartbeat.cancel()

    failed = [name for name, code in results if code != 0]

    print(f"\n{len(requested) - len(failed)}/{len(requested)} examples passed")
    if failed:
        print(f"failed: {', '.join(failed)}", file=sys.stderr)

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
