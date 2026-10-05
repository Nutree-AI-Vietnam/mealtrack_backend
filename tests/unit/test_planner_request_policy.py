import asyncio
from time import monotonic

import pytest

from src.planner_request_policy import timeout_until


class _OffsetClockLoop(asyncio.SelectorEventLoop):
    """Event loop whose clock does not share time.monotonic()'s epoch."""

    def time(self) -> float:
        return super().time() + 1_000_000.0


def _run_on_offset_loop(coro):
    loop = _OffsetClockLoop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_monotonic_deadline_does_not_expire_early_on_offset_loop_clock():
    async def scenario():
        async with timeout_until(monotonic() + 5):
            await asyncio.sleep(0.01)
        return "completed"

    assert _run_on_offset_loop(scenario()) == "completed"


def test_monotonic_deadline_still_times_out_on_offset_loop_clock():
    async def scenario():
        async with timeout_until(monotonic() + 0.05):
            await asyncio.sleep(5)

    with pytest.raises(TimeoutError):
        _run_on_offset_loop(scenario())


def test_expired_monotonic_deadline_times_out_immediately():
    async def scenario():
        async with timeout_until(monotonic() - 1):
            await asyncio.sleep(5)

    with pytest.raises(TimeoutError):
        _run_on_offset_loop(scenario())
