"""Starting an example from the command line."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any

from buddy_sandbox import BuddySDKError
from examples.shared.logger import log


def run(main: Callable[[], Coroutine[Any, Any, None]]) -> None:
    """Run an example, reporting an API error without dumping a stack."""
    try:
        asyncio.run(main())
    except BuddySDKError as error:
        log(f"\nFailed: {error}")
        raise SystemExit(1) from None
    except KeyboardInterrupt:
        raise SystemExit(130) from None
