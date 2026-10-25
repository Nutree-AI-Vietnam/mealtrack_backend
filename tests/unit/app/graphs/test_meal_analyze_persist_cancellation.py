"""persist_meal: a meal saved while the graph is cancelled keeps its created event."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.commands.meal.scan_by_url_command import ScanByUrlCommand
from src.app.graphs.meal_analyze import nodes
from src.app.graphs.meal_analyze.runtime import MealAnalyzeRuntime


@pytest.mark.asyncio
async def test_persist_meal_cancelled_mid_save_still_publishes_created():
    commit_started = asyncio.Event()
    release = asyncio.Event()
    published = asyncio.Event()

    async def slow_commit():
        commit_started.set()
        await release.wait()

    async def record_publish(*args, **kwargs):
        published.set()

    uow = MagicMock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=False)
    uow.users.get_user_timezone = AsyncMock(return_value="UTC")
    uow.meals.save = AsyncMock(side_effect=lambda meal: meal)
    uow.commit = AsyncMock(side_effect=slow_commit)
    meal = MagicMock(meal_id="meal-1")
    runtime = MealAnalyzeRuntime(
        command=ScanByUrlCommand(
            user_id="user-1",
            image_url="https://example.com/a.jpg",
            public_id="mealtrack/a",
            scan_mode="food_label",
        ),
        acquired_image=MagicMock(),
        nutrition=MagicMock(),
        gpt_parser=MagicMock(),
        uow=uow,
        event_publisher=MagicMock(),
        event_bus=MagicMock(),
    )

    with (
        patch.object(nodes, "_build_ready_meal", return_value=meal),
        patch.object(
            nodes, "publish_meal_event", new=AsyncMock(side_effect=record_publish)
        ) as publish,
    ):
        persist = asyncio.create_task(nodes.persist_meal({}, runtime))
        await asyncio.wait_for(commit_started.wait(), timeout=5)
        persist.cancel()
        with pytest.raises(asyncio.CancelledError):
            await persist
        publish.assert_not_called()

        release.set()
        await asyncio.wait_for(published.wait(), timeout=5)

    publish.assert_awaited_once()
    assert publish.await_args.args[1] is meal
    assert publish.await_args.kwargs["event_type"] == "created"
    assert publish.await_args.kwargs["source"] == "meal_analyze_graph"
