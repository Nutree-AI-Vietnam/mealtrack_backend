import asyncio

import pytest

from src.infra.services.scan_concurrency import (
    get_scan_semaphore,
    reset_scan_concurrency_for_tests,
)


@pytest.fixture(autouse=True)
def _reset_scan_semaphore():
    reset_scan_concurrency_for_tests()
    yield
    reset_scan_concurrency_for_tests()


@pytest.mark.asyncio
async def test_scan_semaphore_limits_concurrent_holders():
    semaphore = get_scan_semaphore(1)
    started = 0
    concurrent = 0
    max_concurrent = 0
    holding = asyncio.Event()
    released = asyncio.Event()

    async def holder():
        nonlocal started, concurrent, max_concurrent
        async with semaphore:
            started += 1
            concurrent += 1
            max_concurrent = max(max_concurrent, concurrent)
            holding.set()
            await released.wait()
            concurrent -= 1

    first = asyncio.create_task(holder())
    await holding.wait()
    holding.clear()
    second = asyncio.create_task(holder())
    await asyncio.sleep(0)
    assert started == 1
    released.set()
    await asyncio.gather(first, second)
    assert started == 2
    assert max_concurrent == 1
