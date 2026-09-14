"""A clock that makes sleeping instantaneous while still moving time forward."""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field

import pytest


@dataclass
class FakeClock:
    """Records every sleep and advances a monotonic counter by the same amount."""

    delays_ms: list[float] = field(default_factory=list)
    now: float = 0.0

    @classmethod
    def install(cls, monkeypatch: pytest.MonkeyPatch, module: str) -> FakeClock:
        """Replace ``asyncio.sleep``, and ``monotonic`` where the module imports it.

        The module under test reaches sleep through the ``asyncio`` module
        object, so the patch lands on the stdlib and holds for the whole test.
        monkeypatch puts both back afterwards.
        """
        clock = cls()

        async def sleep(seconds: float) -> None:
            clock.delays_ms.append(seconds * 1000)
            clock.now += seconds

        monkeypatch.setattr(f"{module}.asyncio.sleep", sleep)

        if hasattr(importlib.import_module(module), "monotonic"):
            monkeypatch.setattr(f"{module}.monotonic", lambda: clock.now)

        return clock

    @property
    def rounded_delays_ms(self) -> list[int]:
        return [round(delay) for delay in self.delays_ms]
