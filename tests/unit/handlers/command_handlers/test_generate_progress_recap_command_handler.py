from datetime import date
from unittest.mock import AsyncMock

import pytest

from src.app.commands.progress.generate_progress_recap_command import (
    GenerateProgressRecapCommand,
)
from src.app.handlers.command_handlers.generate_progress_recap_command_handler import (
    GenerateProgressRecapCommandHandler,
)
from src.app.handlers.query_handlers.get_progress_recap_query_handler import (
    GetProgressRecapQueryHandler,
)
from src.app.queries.progress.get_progress_recap_query import GetProgressRecapQuery


def _summary() -> dict:
    return {
        "effective_start": "2026-09-07",
        "effective_end": "2026-09-13",
        "cap_days": 400,
        "days": [
            {
                "date": "2026-09-07",
                "calories": 2100,
                "target_calories": 2000,
                "protein_g": 90,
                "protein_target_g": 130,
                "hydration_ml": 1200,
                "hydration_goal_ml": 2000,
                "burned_calories": 0,
                "meal_count": 3,
                "logged_status": "full",
            }
        ],
    }


@pytest.mark.asyncio
async def test_generate_uses_ai_and_caches_by_horizon() -> None:
    cache = AsyncMock()
    cache.get.return_value = None
    summary = AsyncMock()
    summary.handle.return_value = _summary()

    async def generate(_prompt: str, _system: str) -> dict:
        return {
            "headline": "Protein is the lever this week.",
            "body": "Calories are only 100 over.",
            "next_move": "Add 40 g protein tonight.",
            "highlights": [
                {
                    "kind": "protein_hits",
                    "polarity": "watch",
                    "title": "Protein",
                    "detail": "90 g vs 130 g",
                },
                {
                    "kind": "pace",
                    "polarity": "watch",
                    "title": "Pace",
                    "detail": "+100 kcal",
                },
                {
                    "kind": "next_move",
                    "polarity": "next",
                    "title": "Next",
                    "detail": "Add 40 g protein",
                },
            ],
        }

    handler = GenerateProgressRecapCommandHandler(
        cache_service=cache,
        summary_handler=summary,
        generate=generate,
    )
    result = await handler.handle(
        GenerateProgressRecapCommand(
            user_id="user-1",
            horizon="week",
            start_date=date(2026, 9, 7),
            end_date=date(2026, 9, 13),
            locale="en-US",
        )
    )
    assert result["status"] == "ready"
    assert result["horizon"] == "week"
    assert result["headline"].startswith("Protein")
    cache.set.assert_awaited()


@pytest.mark.asyncio
async def test_generate_reuses_cached_empty_window() -> None:
    cache = AsyncMock()
    cache.get.return_value = {
        "status": "empty",
        "horizon": "week",
        "headline": "",
        "highlights": [],
    }
    summary = AsyncMock()
    summary.handle.return_value = {
        "effective_start": "2026-09-07",
        "effective_end": "2026-09-13",
        "cap_days": 400,
        "days": [],
    }

    async def generate(_prompt: str, _system: str) -> dict:
        raise AssertionError("cached empty windows must not call the model")

    result = await GenerateProgressRecapCommandHandler(
        cache_service=cache,
        summary_handler=summary,
        generate=generate,
    ).handle(
        GenerateProgressRecapCommand(
            user_id="user-1",
            horizon="week",
            start_date=date(2026, 9, 7),
            end_date=date(2026, 9, 13),
        )
    )
    assert result["status"] == "empty"
    cache.set.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_returns_missing_when_cache_empty() -> None:
    cache = AsyncMock()
    cache.get.return_value = None
    summary = AsyncMock()
    summary.handle.return_value = _summary()
    handler = GetProgressRecapQueryHandler(
        cache_service=cache,
        summary_handler=summary,
    )
    result = await handler.handle(
        GetProgressRecapQuery(
            user_id="user-1",
            horizon="month",
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 13),
        )
    )
    assert result["status"] == "missing"
    assert result["horizon"] == "month"


@pytest.mark.asyncio
async def test_generate_falls_back_when_ai_is_not_wired() -> None:
    cache = AsyncMock()
    cache.get.return_value = None
    summary = AsyncMock()
    summary.handle.return_value = _summary()

    result = await GenerateProgressRecapCommandHandler(
        cache_service=cache,
        summary_handler=summary,
    ).handle(
        GenerateProgressRecapCommand(
            user_id="user-1",
            horizon="day",
            start_date=date(2026, 9, 7),
            end_date=date(2026, 9, 7),
        )
    )
    assert result["status"] == "ready"
    assert result["highlights"]


@pytest.mark.asyncio
async def test_generate_falls_back_when_ai_fails() -> None:
    cache = AsyncMock()
    cache.get.return_value = None
    summary = AsyncMock()
    summary.handle.return_value = _summary()

    async def generate(_prompt: str, _system: str) -> dict:
        raise RuntimeError("model down")

    result = await GenerateProgressRecapCommandHandler(
        cache_service=cache,
        summary_handler=summary,
        generate=generate,
    ).handle(
        GenerateProgressRecapCommand(
            user_id="user-1",
            horizon="day",
            start_date=date(2026, 9, 7),
            end_date=date(2026, 9, 7),
        )
    )
    assert result["status"] == "ready"
    assert result["highlights"]


@pytest.mark.asyncio
async def test_generate_asks_ai_for_user_language() -> None:
    cache = AsyncMock()
    cache.get.return_value = None
    summary = AsyncMock()
    summary.handle.return_value = _summary()
    captured: dict[str, str] = {}

    async def generate(prompt: str, system: str) -> dict:
        captured["prompt"] = prompt
        captured["system"] = system
        raise RuntimeError("model down")

    result = await GenerateProgressRecapCommandHandler(
        cache_service=cache,
        summary_handler=summary,
        generate=generate,
    ).handle(
        GenerateProgressRecapCommand(
            user_id="user-1",
            horizon="week",
            start_date=date(2026, 9, 7),
            end_date=date(2026, 9, 13),
            locale="vi-VN,vi;q=0.9",
        )
    )
    assert "Vietnamese" in captured["system"]
    assert "Vietnamese" in captured["prompt"]
    assert "Đạm" in result["headline"]
