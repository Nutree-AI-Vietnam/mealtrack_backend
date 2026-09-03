import asyncio

import pytest

from src.app.services.chat_next_meal_candidates import (
    ChatNextMealCandidates,
    map_discover_meals,
)
from src.domain.model.chat import ChatUserContext
from src.domain.ports.chat_discover_port import ChatDiscoverBatch


class _FakeDiscover:
    def __init__(self, batch=None, delay=0.0, error=None):
        self.batch = batch or ChatDiscoverBatch(session_id="sess-1", meals=())
        self.delay = delay
        self.error = error
        self.calls: list[dict] = []

    async def discover_meals(self, **kwargs):
        self.calls.append(kwargs)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return self.batch


def _context() -> ChatUserContext:
    return ChatUserContext(
        context_version="chat_context_v1",
        as_of="2026-09-01T00:00:00+00:00",
        locale="en",
        timezone="UTC",
        allergies=[],
        health_conditions=[],
        dietary_preferences=[],
        goal="cutting",
        tdee=2200,
        target_calories=1800,
        target_protein_g=140,
        target_carbs_g=180,
        target_fat_g=60,
        consumed_calories=1150,
        consumed_protein_g=90,
        consumed_carbs_g=100,
        consumed_fat_g=40,
        remaining_calories=650,
        remaining_protein_g=50,
        remaining_carbs_g=80,
        remaining_fat_g=20,
        remaining_days=4,
        local_hour=8,
        local_minute=12,
        suggested_meal_slot="breakfast",
    )


@pytest.mark.asyncio
async def test_fetch_maps_three_cards_and_portion() -> None:
    discover = _FakeDiscover(
        ChatDiscoverBatch(
            session_id="sess-9",
            meals=(
                {
                    "id": "d1",
                    "name": "Egg rice bowl",
                    "calories": 420,
                    "protein": 28,
                    "carbs": 45,
                    "fat": 12,
                    "emoji": "🍳",
                },
                {
                    "id": "d2",
                    "name": "Yogurt cup",
                    "calories": 220,
                    "protein": 18,
                    "carbs": 20,
                    "fat": 6,
                },
                {
                    "id": "d3",
                    "name": "Tofu scramble",
                    "calories": 380,
                    "protein": 24,
                    "carbs": 18,
                    "fat": 22,
                },
            ),
        )
    )
    service = ChatNextMealCandidates(discover)
    result = await service.fetch(
        user_id="u1",
        context=_context(),
        user_text="What should I eat?",
        locale="en",
        session_id=None,
    )
    assert result.session_id == "sess-9"
    assert result.meal_slot == "breakfast"
    assert len(result.suggestions) == 3
    assert result.suggestions[0]["protein_g"] == 28
    assert result.suggestions[0]["emoji"] == "🍳"
    assert discover.calls[0]["meal_portion_type"] == "main"
    assert discover.calls[0]["calorie_target"] == 650
    assert discover.calls[0]["count"] == 3


@pytest.mark.asyncio
async def test_fetch_reuses_session_id() -> None:
    discover = _FakeDiscover(ChatDiscoverBatch(session_id="sess-2", meals=()))
    service = ChatNextMealCandidates(discover)
    await service.fetch(
        user_id="u1",
        context=_context(),
        user_text="More breakfast ideas",
        locale="en",
        session_id="sess-1",
    )
    assert discover.calls[0]["session_id"] == "sess-1"


@pytest.mark.asyncio
async def test_slow_discover_still_returns_cards() -> None:
    discover = _FakeDiscover(
        ChatDiscoverBatch(
            session_id="sess-slow",
            meals=({"name": "Oats", "calories": 300},),
        ),
        delay=0.05,
    )
    service = ChatNextMealCandidates(discover)
    result = await service.fetch(
        user_id="u1",
        context=_context(),
        user_text="What should I eat?",
        locale="en",
        session_id="sess-keep",
    )
    assert result.suggestions[0]["name"] == "Oats"
    assert result.session_id == "sess-slow"


@pytest.mark.asyncio
async def test_rate_limit_skips_discover() -> None:
    discover = _FakeDiscover(ChatDiscoverBatch(session_id="n", meals=()))
    service = ChatNextMealCandidates(discover, max_per_minute=1)
    await service.fetch(
        user_id="u1",
        context=_context(),
        user_text="next",
        locale="en",
        session_id=None,
    )
    second = await service.fetch(
        user_id="u1",
        context=_context(),
        user_text="next",
        locale="en",
        session_id="sess-keep",
    )
    assert len(discover.calls) == 1
    assert second.suggestions == []


def test_map_drops_meals_without_calories() -> None:
    cards = map_discover_meals(
        [{"name": "Mystery", "protein": 10}, {"name": "Oats", "calories": 300}],
        "breakfast",
    )
    assert [card["name"] for card in cards] == ["Oats"]
