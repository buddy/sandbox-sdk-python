"""Bump the version, commit it and tag the result.

The equivalent of `pnpm version <part>`: `uv version --bump` handles the number
and the lockfile, this adds the release commit and the `v`-prefixed tag the
release pipeline triggers on.

    uv run python scripts/bump.py patch
"""

from __future__ import annotations

import subprocess
import sys

PARTS = ("major", "minor", "patch", "stable", "alpha", "beta", "rc", "post", "dev")


def run(*command: str) -> str:
    return subprocess.run(command, check=True, capture_output=True, text=True).stdout.strip()


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in PARTS:
        raise SystemExit(f"Usage: bump.py <{' | '.join(PARTS)}>")

    if run("git", "status", "--porcelain"):
        raise SystemExit("Working tree is not clean - commit or stash first.")

    run("uv", "version", "--bump", sys.argv[1])
    version = run("uv", "version", "--short")

    run("git", "commit", "-am", f"chore(release): bump version to {version}")
    run("git", "tag", "-a", f"v{version}", "-m", f"v{version}")

    print(f"Bumped to {version} and tagged v{version}. Push with:")
    print(f"  git push && git push origin v{version}")


if __name__ == "__main__":
    main()
