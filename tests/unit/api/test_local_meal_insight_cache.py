"""Meal insight generation in the API process: in-process events and the local writer."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.api.services.local_meal_insight_cache import (
    MEAL_INSIGHT_CACHE_TTL_SECONDS,
    MEAL_INSIGHT_FENCE_TTL_SECONDS,
    LocalMealInsightWriter,
    MealInsightEventHandler,
    _build_prompt,
    event_revision,
    redis_url_is_local,
)
from src.domain.services.meal_value_insight_service import MealValueInsightService


def test_redis_url_is_local_for_docker_host() -> None:
    assert redis_url_is_local("redis://localhost:6379/0")
    assert redis_url_is_local("redis://127.0.0.1:6379/0")
    assert not redis_url_is_local(
        "rediss://default:secret@legible-kingfish-128226.upstash.io:6379"
    )


@pytest.mark.asyncio
async def test_local_writer_skips_hosted_redis() -> None:
    cache = SimpleNamespace(
        enabled=True,
        redis=SimpleNamespace(_redis_url="rediss://upstash.example:6379"),
        set_json=AsyncMock(),
    )
    ai = SimpleNamespace(generate=AsyncMock())
    writer = LocalMealInsightWriter(lambda: cache, lambda: ai)

    await writer._write(
        "00000000-0000-4000-8000-000000000001",
        {"dish_name": "Pho", "language": "vi"},
        datetime(2026, 9, 4, tzinfo=UTC),
    )

    ai.generate.assert_not_awaited()
    cache.set_json.assert_not_awaited()


@pytest.mark.asyncio
async def test_local_writer_caches_validated_insights() -> None:
    cache = SimpleNamespace(
        enabled=True,
        redis=SimpleNamespace(_redis_url="redis://localhost:6379/0"),
        set_json=AsyncMock(),
    )
    ai = SimpleNamespace(
        generate=AsyncMock(
            return_value={
                "meal_bullets": [
                    {
                        "text": "High protein supports fullness",
                        "category": "benefit",
                        "highlights": ["protein"],
                    }
                ],
                "ingredient_insights": [],
            }
        )
    )
    writer = LocalMealInsightWriter(lambda: cache, lambda: ai)
    meal_id = "00000000-0000-4000-8000-000000000002"

    await writer._write(
        meal_id,
        {"dish_name": "Chicken", "language": "en", "nutrition": {}, "ingredients": []},
        datetime(2026, 9, 4, tzinfo=UTC),
    )

    ai.generate.assert_awaited_once()
    cache.set_json.assert_awaited_once()
    key, payload, ttl = cache.set_json.await_args.args
    assert key == MealValueInsightService.cache_key_for_meal(meal_id)
    assert payload["meal_bullets"][0]["highlights"] == ["protein"]
    assert ttl == 7 * 24 * 60 * 60
    prompt = ai.generate.await_args.kwargs["prompt"]
    assert "Micros are absent" in prompt
    assert "English" in ai.generate.await_args.kwargs["system_message"]


def test_build_prompt_asks_for_macro_and_micro_analysis() -> None:
    prompt = _build_prompt(
        {
            "dish_name": "Cơm tấm",
            "language": "vi",
            "nutrition": {
                "calories": 650.0,
                "protein_g": 32.0,
                "carbs_g": 70.0,
                "fat_g": 22.0,
                "fiber_g": 4.0,
                "sugar_g": 6.0,
                "micros": {"iron": 3.4, "sodium": 980.0, "potassium": 420.0},
            },
            "ingredients": [
                {
                    "name": "Sườn",
                    "protein_g": 18.0,
                    "micros": {"iron": 1.8, "sodium": 410.0},
                }
            ],
            "user_context": {"fitness_goal": "lose_weight"},
        }
    )

    assert (
        "Write ALL meal_bullet.text and ingredient_insights.text in Vietnamese"
        in prompt
    )
    assert "Do not write English sentences" in prompt
    assert "'output_language': 'Vietnamese'" in prompt
    assert "Analyze both macros and micros" in prompt
    assert "logged micronutrient" in prompt
    assert "'iron': 3.4" in prompt
    assert "'protein_g': 32.0" in prompt
    assert "Sườn" in prompt
    assert "vitamin_c/e mg" in prompt


VALID_AI_INSIGHT = {
    "meal_bullets": [
        {
            "text": "High protein supports fullness",
            "category": "benefit",
            "highlights": ["protein"],
        }
    ],
    "ingredient_insights": [],
}
MEAL_ID = "00000000-0000-4000-8000-000000000003"
OCCURRED_AT = "2026-11-05T03:00:00+00:00"
REVISION = event_revision(OCCURRED_AT)


def _insight_cache(*, owns_key: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        enabled=True,
        invalidate_with_revision=AsyncMock(return_value=owns_key),
        set_json_with_revision=AsyncMock(return_value=True),
    )


def _meal_event(event_type: str = "meal.created.v1", **data) -> dict:
    return {
        "event_type": event_type,
        "aggregate_id": MEAL_ID,
        "occurred_at": OCCURRED_AT,
        "data": {
            "meal_id": MEAL_ID,
            "insight": {
                "dish_name": "Phở bò",
                "language": "vi",
                "nutrition": {},
                "ingredients": [],
            },
            **data,
        },
    }


@pytest.mark.asyncio
async def test_event_handler_stores_insights_under_the_events_revision() -> None:
    cache = _insight_cache()
    ai = SimpleNamespace(generate=AsyncMock(return_value=VALID_AI_INSIGHT))
    key = MealValueInsightService.cache_key_for_meal(MEAL_ID)

    await MealInsightEventHandler(lambda: cache, lambda: ai)(_meal_event())

    cache.invalidate_with_revision.assert_awaited_once_with(
        key, REVISION, MEAL_INSIGHT_FENCE_TTL_SECONDS
    )
    ai.generate.assert_awaited_once()
    assert "Vietnamese" in ai.generate.await_args.kwargs["system_message"]
    stored_key, payload, revision, ttl = cache.set_json_with_revision.await_args.args
    assert (stored_key, revision, ttl) == (
        key,
        REVISION,
        MEAL_INSIGHT_CACHE_TTL_SECONDS,
    )
    assert payload["meal_bullets"][0]["highlights"] == ["protein"]


@pytest.mark.asyncio
async def test_event_handler_leaves_the_key_to_a_newer_edit() -> None:
    cache = _insight_cache(owns_key=False)
    ai = SimpleNamespace(generate=AsyncMock(return_value=VALID_AI_INSIGHT))

    await MealInsightEventHandler(lambda: cache, lambda: ai)(
        _meal_event("meal.updated.v1")
    )

    ai.generate.assert_not_awaited()
    cache.set_json_with_revision.assert_not_awaited()


@pytest.mark.asyncio
async def test_meal_deletion_clears_the_insight_without_generating() -> None:
    cache = _insight_cache()
    ai = SimpleNamespace(generate=AsyncMock())

    await MealInsightEventHandler(lambda: cache, lambda: ai)(
        _meal_event("meal.deleted.v1", insight=None)
    )

    cache.invalidate_with_revision.assert_awaited_once()
    ai.generate.assert_not_awaited()


@pytest.mark.asyncio
async def test_event_handler_keeps_nothing_when_the_ai_answer_is_invalid() -> None:
    cache = _insight_cache()
    ai = SimpleNamespace(generate=AsyncMock(return_value={"meal_bullets": "nope"}))

    await MealInsightEventHandler(lambda: cache, lambda: ai)(_meal_event())

    cache.set_json_with_revision.assert_not_awaited()


@pytest.mark.asyncio
async def test_event_handler_does_nothing_without_a_cache_or_timestamp() -> None:
    ai = SimpleNamespace(generate=AsyncMock())
    disabled = SimpleNamespace(enabled=False, invalidate_with_revision=AsyncMock())

    await MealInsightEventHandler(lambda: None, lambda: ai)(_meal_event())
    await MealInsightEventHandler(lambda: disabled, lambda: ai)(_meal_event())
    cache = _insight_cache()
    event = _meal_event()
    event["occurred_at"] = "yesterday"
    await MealInsightEventHandler(lambda: cache, lambda: ai)(event)

    disabled.invalidate_with_revision.assert_not_awaited()
    cache.invalidate_with_revision.assert_not_awaited()
    ai.generate.assert_not_awaited()


def test_event_revision_reads_iso_timestamps_as_epoch_milliseconds() -> None:
    assert event_revision("2026-11-05T03:00:00Z") == event_revision(
        datetime(2026, 11, 5, 3, tzinfo=UTC)
    )
    assert event_revision("2026-11-05T03:00:00") == 1793847600000
    assert event_revision("not a time") is None
    assert event_revision(None) is None
