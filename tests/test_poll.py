"""The backing-off polling loop."""

from __future__ import annotations

import pytest

from buddy_sandbox.utils.poll import poll_until, resolve_poll_interval
from tests.shared.clock import FakeClock


class TestResolvePollInterval:
    def test_backs_off_from_100ms_to_1000ms_when_no_interval_is_given(self) -> None:
        assert resolve_poll_interval(None) == {
            "initial_interval_ms": 100,
            "max_interval_ms": 1000,
        }

    def test_honours_a_custom_ceiling_for_the_backoff_case(self) -> None:
        assert resolve_poll_interval(None, 2000) == {
            "initial_interval_ms": 100,
            "max_interval_ms": 2000,
        }

    def test_pins_to_a_fixed_interval_when_one_is_given_explicitly(self) -> None:
        assert resolve_poll_interval(500) == {
            "initial_interval_ms": 500,
            "max_interval_ms": 500,
        }

    def test_lets_an_explicit_interval_exceed_the_default_ceiling(self) -> None:
        assert resolve_poll_interval(5000) == {
            "initial_interval_ms": 5000,
            "max_interval_ms": 5000,
        }


class TestPollUntil:
    async def test_does_not_sleep_when_the_first_check_already_succeeds(
        self, clock: FakeClock
    ) -> None:
        async def check() -> bool:
            return True

        await poll_until(check)

        assert clock.delays_ms == []

    async def test_grows_the_delay_by_1_5x_up_to_the_1000ms_ceiling(self, clock: FakeClock) -> None:
        calls = 0

        async def check() -> bool:
            nonlocal calls
            calls += 1
            return calls > 9

        await poll_until(check)

        assert clock.rounded_delays_ms == [100, 150, 225, 338, 506, 759, 1000, 1000, 1000]

    async def test_keeps_a_constant_delay_when_an_explicit_interval_is_used(
        self, clock: FakeClock
    ) -> None:
        calls = 0

        async def check() -> bool:
            nonlocal calls
            calls += 1
            return calls > 4

        await poll_until(check, **resolve_poll_interval(500))

        assert clock.delays_ms == [500, 500, 500, 500]

    async def test_clamps_the_first_delay_to_max_interval_ms(self, clock: FakeClock) -> None:
        calls = 0

        async def check() -> bool:
            nonlocal calls
            calls += 1
            return calls > 2

        await poll_until(check, initial_interval_ms=5000, max_interval_ms=200)

        assert clock.delays_ms == [200, 200]

    async def test_raises_the_on_timeout_error_once_max_wait_ms_is_exceeded(
        self, clock: FakeClock
    ) -> None:
        async def check() -> bool:
            return False

        with pytest.raises(RuntimeError, match="gave up waiting"):
            await poll_until(
                check,
                initial_interval_ms=100,
                max_interval_ms=100,
                max_wait_ms=250,
                on_timeout=lambda: RuntimeError("gave up waiting"),
            )

    async def test_falls_back_to_a_generic_timeout_error_without_on_timeout(
        self, clock: FakeClock
    ) -> None:
        async def check() -> bool:
            return False

        with pytest.raises(TimeoutError, match="Timeout after 150ms"):
            await poll_until(check, initial_interval_ms=100, max_interval_ms=100, max_wait_ms=150)

    async def test_never_times_out_when_max_wait_ms_is_omitted(self, clock: FakeClock) -> None:
        calls = 0

        async def check() -> bool:
            nonlocal calls
            calls += 1
            return calls > 50

        await poll_until(check)

        assert calls == 51

    async def test_propagates_a_raise_from_check_without_sleeping_again(
        self, clock: FakeClock
    ) -> None:
        calls = 0

        async def check() -> bool:
            nonlocal calls
            calls += 1
            if calls == 3:
                raise RuntimeError("terminal state")
            return False

        with pytest.raises(RuntimeError, match="terminal state"):
            await poll_until(check)

        assert calls == 3
        assert clock.delays_ms == [100, 150]
