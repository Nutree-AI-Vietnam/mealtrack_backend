import asyncio
from types import SimpleNamespace

import pytest

from src.app.services.chat_next_meal_candidates import (
    ChatNextMealCandidates,
    SuggestionChatDiscoverAdapter,
    map_chat_recipe_meals,
    map_discover_meals,
)
from src.domain.model.chat import ChatUserContext


class _FakeRecipes:
    def __init__(self, meals=None, delay=0.0, error=None):
        self.meals = meals or []
        self.delay = delay
        self.error = error
        self.calls: list[dict] = []

    async def generate_next_meal_recipes(self, **kwargs):
        self.calls.append(kwargs)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return self.meals


class _FakeLookup:
    def __init__(self, error=None):
        self.error = error
        self.calls: list[list] = []

    async def calculate_meal_macros(self, ingredients):
        self.calls.append(ingredients)
        if self.error is not None:
            raise self.error
        breakdown = [
            SimpleNamespace(name=ing["name"], calories=120 + index * 10)
            for index, ing in enumerate(ingredients)
        ]
        return SimpleNamespace(
            calories=420,
            protein=28,
            carbs=45,
            fat=12,
            ingredients=breakdown,
        )


def _recipe(name: str = "Egg rice bowl") -> dict:
    return {
        "name": name,
        "english_name": name,
        "emoji": "🍳",
        "prep_time_minutes": 18,
        "ingredients": [
            {"name": "eggs", "amount": 2, "unit": "pcs"},
            {"name": "rice", "amount": 150, "unit": "g"},
            {"name": "spinach", "amount": 40, "unit": "g"},
        ],
        "recipe_steps": [
            {"step": 1, "instruction": "Cook rice.", "duration_minutes": 12},
            {"step": 2, "instruction": "Scramble eggs.", "duration_minutes": 6},
        ],
    }


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
async def test_fetch_maps_three_recipe_cards() -> None:
    recipes = _FakeRecipes([_recipe(), _recipe("Yogurt cup"), _recipe("Tofu scramble")])
    lookup = _FakeLookup()
    service = ChatNextMealCandidates(recipes, lookup, model="gpt-test")
    result = await service.fetch(
        user_id="u1",
        context=_context(),
        user_text="What should I eat?",
        locale="en",
        session_id="sess-ignored",
    )
    assert result.session_id is None
    assert result.meal_slot == "breakfast"
    assert len(result.suggestions) == 3
    assert result.suggestions[0]["protein_g"] == 28
    assert result.suggestions[0]["emoji"] == "🍳"
    assert result.suggestions[0]["ingredients"][0]["name"] == "eggs"
    assert result.suggestions[0]["ingredients"][0]["calories"] == 120
    assert result.suggestions[0]["recipe_steps"][0]["instruction"] == "Cook rice."
    assert result.suggestions[0]["id"].startswith("chat_")
    assert recipes.calls[0]["slot"] == "breakfast"
    assert recipes.calls[0]["remaining_calories"] == 650
    assert recipes.calls[0]["model"] == "gpt-test"
    assert len(lookup.calls) == 3


@pytest.mark.asyncio
async def test_slow_recipe_call_still_returns_cards() -> None:
    recipes = _FakeRecipes([_recipe("Oats")], delay=0.05)
    service = ChatNextMealCandidates(recipes, _FakeLookup(), model="gpt-test")
    result = await service.fetch(
        user_id="u1",
        context=_context(),
        user_text="What should I eat?",
        locale="en",
        session_id="sess-keep",
    )
    assert result.suggestions[0]["name"] == "Oats"
    assert result.session_id is None


@pytest.mark.asyncio
async def test_rate_limit_skips_recipe_call() -> None:
    recipes = _FakeRecipes([])
    service = ChatNextMealCandidates(
        recipes, _FakeLookup(), model="gpt-test", max_per_minute=1
    )
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
    assert len(recipes.calls) == 1
    assert second.suggestions == []


@pytest.mark.asyncio
async def test_lookup_failure_drops_card() -> None:
    cards = await map_chat_recipe_meals(
        [_recipe()],
        "lunch",
        _FakeLookup(error=RuntimeError("lookup down")),
    )
    assert cards == []


@pytest.mark.asyncio
async def test_zero_calories_drops_card() -> None:
    class _ZeroLookup:
        async def calculate_meal_macros(self, ingredients):
            return SimpleNamespace(calories=0, protein=0, carbs=0, fat=0)

    cards = await map_chat_recipe_meals([_recipe()], "lunch", _ZeroLookup())
    assert cards == []


def test_map_keeps_photos_and_english_name() -> None:
    cards = map_discover_meals(
        [
            {
                "id": "d1",
                "name": "Cơm gà",
                "english_name": "Chicken rice",
                "calories": 420,
                "protein": 28,
                "carbs": 45,
                "fat": 12,
                "emoji": "🍚",
                "thumbnail_url": "https://cdn.example/thumb.jpg",
                "image_url": "https://cdn.example/full.jpg",
                "image_source": "pexels",
                "image_confidence": 0.91,
                "photographer": "Ann",
            }
        ],
        "lunch",
    )
    assert cards[0]["english_name"] == "Chicken rice"
    assert cards[0]["thumbnail_url"] == "https://cdn.example/thumb.jpg"
    assert cards[0]["image_confidence"] == 0.91
    assert cards[0]["photographer"] == "Ann"


@pytest.mark.asyncio
async def test_adapter_attaches_food_photos() -> None:
    class _Session:
        id = "sess-img"

    class _Service:
        async def generate_discovery(self, **kwargs):
            del kwargs
            return _Session(), [
                {
                    "id": "d1",
                    "name": "Pho",
                    "english_name": "Pho",
                    "calories": 400,
                    "protein": 20,
                    "carbs": 50,
                    "fat": 10,
                }
            ]

    class _Image:
        url = "https://cdn.example/full.jpg"
        thumbnail_url = "https://cdn.example/thumb.jpg"
        source = "pexels"
        photographer = "Ann"
        photographer_url = "https://pexels.example/ann"
        download_location = None
        confidence = 0.88

    async def search(name: str):
        assert name == "Pho"
        return _Image()

    adapter = SuggestionChatDiscoverAdapter(_Service(), image_search=search)
    batch = await adapter.discover_meals(
        user_id="u1",
        meal_type="lunch",
        meal_portion_type="main",
        language="en",
        calorie_target=650,
        protein_target=50,
        carbs_target=80,
        fat_target=20,
        session_id=None,
        count=3,
    )
    meal = batch.meals[0]
    assert meal["thumbnail_url"] == "https://cdn.example/thumb.jpg"
    assert meal["image_confidence"] == 0.88


@pytest.mark.asyncio
async def test_adapter_keeps_meals_when_image_search_fails() -> None:
    class _Session:
        id = "sess-img"

    class _Service:
        async def generate_discovery(self, **kwargs):
            del kwargs
            return _Session(), [
                {
                    "id": "d1",
                    "name": "Pho",
                    "calories": 400,
                    "protein": 20,
                    "carbs": 50,
                    "fat": 10,
                }
            ]

    async def search(name: str):
        raise RuntimeError(f"search failed for {name}")

    adapter = SuggestionChatDiscoverAdapter(_Service(), image_search=search)
    batch = await adapter.discover_meals(
        user_id="u1",
        meal_type="lunch",
        meal_portion_type="main",
        language="en",
        calorie_target=650,
        protein_target=50,
        carbs_target=80,
        fat_target=20,
        session_id=None,
        count=3,
    )
    assert batch.meals[0]["name"] == "Pho"
    assert "thumbnail_url" not in batch.meals[0]


def test_map_drops_meals_without_calories() -> None:
    cards = map_discover_meals(
        [{"name": "Mystery", "protein": 10}, {"name": "Oats", "calories": 300}],
        "breakfast",
    )
    assert [card["name"] for card in cards] == ["Oats"]
