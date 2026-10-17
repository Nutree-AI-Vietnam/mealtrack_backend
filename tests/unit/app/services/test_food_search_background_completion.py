"""Background food search work: budgets, joining, failures and draining."""

import asyncio
import logging

import pytest

from src.app.services.food_search_background_completion import (
    CompletionStatus,
    FoodSearchBackgroundCompletions,
    completion_budget_seconds,
)

_LOGGER = "src.app.services.food_search_background_completion"


@pytest.mark.parametrize(
    ("autocomplete", "has_local_results", "expected"),
    [
        (True, True, 0.5),
        (True, False, 3.0),
        (False, True, 0.8),
        (False, False, 8.0),
    ],
)
def test_budget_is_short_only_when_local_rows_can_be_shown(
    autocomplete, has_local_results, expected
):
    assert (
        completion_budget_seconds(
            autocomplete=autocomplete, has_local_results=has_local_results
        )
        == expected
    )


@pytest.mark.asyncio
async def test_each_waiter_gets_its_own_copy_of_the_published_page():
    completions = FoodSearchBackgroundCompletions()
    rows = [{"description": "Beef pho"}]

    async def work(publish):
        publish(rows)
        # Later stages (adoption, cache write) keep mutating their rows.
        rows[0]["food_reference_id"] = 7

    ready = completions.start("key", work, name="search")
    first = await completions.wait(ready, budget=1.0)
    first.results[0]["description"] = "changed by the first caller"
    second = await completions.wait(ready, budget=1.0)

    assert first.status is CompletionStatus.READY
    assert first.partial is False
    assert second.results == [{"description": "Beef pho"}]


@pytest.mark.asyncio
async def test_partial_flag_reaches_the_waiter():
    completions = FoodSearchBackgroundCompletions()

    async def work(publish):
        publish([{"description": "Rice"}], partial=True)

    outcome = await completions.wait(
        completions.start("key", work, name="search"), budget=1.0
    )

    assert outcome.status is CompletionStatus.READY
    assert outcome.partial is True


@pytest.mark.asyncio
async def test_only_the_first_publish_counts():
    completions = FoodSearchBackgroundCompletions()

    async def work(publish):
        publish([{"description": "First"}])
        publish([{"description": "Second"}], partial=True)

    outcome = await completions.wait(
        completions.start("key", work, name="search"), budget=1.0
    )

    assert outcome.results == [{"description": "First"}]
    assert outcome.partial is False


@pytest.mark.asyncio
async def test_work_that_ends_without_publishing_fails_the_wait():
    completions = FoodSearchBackgroundCompletions()

    async def work(publish):
        return None

    outcome = await completions.wait(
        completions.start("key", work, name="search"), budget=1.0
    )

    assert outcome.status is CompletionStatus.FAILED
    assert outcome.results == []


@pytest.mark.asyncio
async def test_raising_work_fails_the_wait_and_is_logged(caplog):
    completions = FoodSearchBackgroundCompletions()

    async def work(publish):
        raise RuntimeError("provider exploded")

    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        outcome = await completions.wait(
            completions.start("key", work, name="search"), budget=1.0
        )
        await completions.drain(timeout=1.0)

    records = [record for record in caplog.records if record.name == _LOGGER]
    assert outcome.status is CompletionStatus.FAILED
    assert [record.getMessage() for record in records] == [
        "food search background work failed"
    ]
    assert records[0].exc_info[0] is RuntimeError


@pytest.mark.asyncio
async def test_timed_out_wait_leaves_the_work_running():
    completions = FoodSearchBackgroundCompletions()
    gate = asyncio.Event()

    async def work(publish):
        await gate.wait()
        publish([{"description": "Late page"}])

    ready = completions.start("key", work, name="search")
    timed_out = await completions.wait(ready, budget=0.01)
    gate.set()
    late = await completions.wait(ready, budget=1.0)

    assert timed_out.status is CompletionStatus.TIMEOUT
    assert timed_out.results == []
    assert late.status is CompletionStatus.READY
    assert late.results == [{"description": "Late page"}]


@pytest.mark.asyncio
async def test_same_key_joins_running_work_and_starts_fresh_after_it_ends():
    completions = FoodSearchBackgroundCompletions()
    gate = asyncio.Event()
    runs = []

    async def work(publish):
        runs.append("run")
        await gate.wait()
        publish([])

    first = completions.start("key", work, name="search")
    joined = completions.start("key", work, name="search")
    gate.set()
    await completions.drain(timeout=1.0)
    again = completions.start("key", work, name="search")
    await completions.drain(timeout=1.0)

    assert joined is first
    assert again is not first
    assert runs == ["run", "run"]


@pytest.mark.asyncio
async def test_repeat_start_joins_work_that_published_but_is_still_writing():
    completions = FoodSearchBackgroundCompletions()
    published = asyncio.Event()
    cache_write = asyncio.Event()
    runs = []

    async def work(publish):
        runs.append("run")
        publish([{"description": "Beef pho"}])
        published.set()
        await cache_write.wait()

    first = completions.start("key", work, name="search")
    await published.wait()
    repeat = completions.start("key", work, name="search")
    outcome = await completions.wait(repeat, budget=1.0)
    cache_write.set()
    await completions.drain(timeout=1.0)

    assert repeat is first
    assert outcome.results == [{"description": "Beef pho"}]
    assert runs == ["run"]


@pytest.mark.asyncio
async def test_work_past_the_in_flight_cap_runs_but_cannot_be_joined():
    completions = FoodSearchBackgroundCompletions(max_in_flight=1)
    gate = asyncio.Event()
    runs = []

    async def work(publish):
        runs.append("run")
        await gate.wait()
        publish([])

    first = completions.start("first", work, name="search")
    second = completions.start("second", work, name="search")
    second_again = completions.start("second", work, name="search")
    first_again = completions.start("first", work, name="search")
    gate.set()
    await completions.drain(timeout=1.0)

    assert first_again is first
    assert second_again is not second
    assert runs == ["run", "run", "run"]


@pytest.mark.asyncio
async def test_drain_cancels_work_that_outlives_its_timeout(caplog):
    completions = FoodSearchBackgroundCompletions()
    started = asyncio.Event()
    cancelled = []

    async def work(publish):
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    ready = completions.start("key", work, name="search")
    await started.wait()
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        await completions.drain(timeout=0.01)
    outcome = await completions.wait(ready, budget=1.0)

    assert cancelled == [True]
    assert outcome.status is CompletionStatus.FAILED
    assert not [record for record in caplog.records if record.name == _LOGGER]


@pytest.mark.asyncio
async def test_drain_without_work_returns_immediately():
    await FoodSearchBackgroundCompletions().drain(timeout=0.01)


def test_work_left_on_another_event_loop_is_not_joined():
    completions = FoodSearchBackgroundCompletions()
    old_loop = asyncio.new_event_loop()
    try:
        completions._in_flight["key"] = old_loop.create_future()
    finally:
        old_loop.close()

    async def search():
        async def work(publish):
            publish([{"description": "Fresh page"}])

        ready = completions.start("key", work, name="search")
        return await completions.wait(ready, budget=1.0)

    outcome = asyncio.run(search())

    assert outcome.status is CompletionStatus.READY
    assert outcome.results == [{"description": "Fresh page"}]
