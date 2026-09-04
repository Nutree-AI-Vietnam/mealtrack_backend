import pytest

from src.app.services.chat_next_meal_candidates import (
    ChatNextMealCandidates,
    SuggestionChatDiscoverAdapter,
    map_discover_meals,
)
from src.domain.model.chat import ChatUserContext
from src.domain.ports.chat_discover_port import ChatDiscoverBatch


class _FakeDiscover:
    def __init__(self, batch=None, error=None):
        self.batch = batch or ChatDiscoverBatch(session_id=None, meals=())
        self.error = error
        self.calls: list[dict] = []

    async def discover_meals(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.batch


def _meal(name: str = "Egg rice bowl", **extra) -> dict:
    card = {
        "id": f"d-{name}",
        "name": name,
        "english_name": extra.pop("english_name", name),
        "calories": extra.pop("calories", 420),
        "protein": 28,
        "carbs": 45,
        "fat": 12,
    }
    card.update(extra)
    return card


def _context(**overrides) -> ChatUserContext:
    values = {
        "context_version": "chat_context_v1",
        "as_of": "2026-09-01T00:00:00+00:00",
        "locale": "en",
        "timezone": "UTC",
        "allergies": [],
        "health_conditions": [],
        "dietary_preferences": [],
        "goal": "cutting",
        "tdee": 2200,
        "target_calories": 1800,
        "target_protein_g": 140,
        "target_carbs_g": 180,
        "target_fat_g": 60,
        "consumed_calories": 1150,
        "consumed_protein_g": 90,
        "consumed_carbs_g": 100,
        "consumed_fat_g": 40,
        "remaining_calories": 650,
        "remaining_protein_g": 50,
        "remaining_carbs_g": 80,
        "remaining_fat_g": 20,
        "remaining_days": 4,
        "local_hour": 8,
        "local_minute": 12,
        "suggested_meal_slot": "breakfast",
    }
    values.update(overrides)
    return ChatUserContext(**values)


@pytest.mark.asyncio
async def test_fetch_maps_discover_cards_and_keeps_session() -> None:
    discover = _FakeDiscover(
        ChatDiscoverBatch(
            session_id="sess-keep",
            meals=(
                _meal(),
                _meal("Yogurt cup"),
                _meal("Tofu scramble"),
            ),
        )
    )
    service = ChatNextMealCandidates(discover)
    result = await service.fetch(
        user_id="u1",
        context=_context(),
        user_text="What should I eat?",
        locale="en",
        session_id="sess-keep",
    )
    assert result.session_id == "sess-keep"
    assert result.meal_slot == "breakfast"
    assert len(result.suggestions) == 3
    assert result.suggestions[0]["protein_g"] == 28
    assert discover.calls[0]["session_id"] == "sess-keep"
    assert discover.calls[0]["meal_type"] == "breakfast"
    assert discover.calls[0]["meal_portion_type"] == "main"
    assert discover.calls[0]["calorie_target"] == 650


@pytest.mark.asyncio
async def test_fetch_drops_allergen_cards() -> None:
    discover = _FakeDiscover(
        ChatDiscoverBatch(
            session_id="sess-1",
            meals=(
                _meal("Thai satay", ingredients=[{"name": "peanut butter"}]),
                _meal("Rice bowl", ingredients=[{"name": "rice"}]),
                _meal("Unverified bowl"),
            ),
        )
    )
    service = ChatNextMealCandidates(discover)
    result = await service.fetch(
        user_id="u1",
        context=_context(allergies=["peanut"]),
        user_text="What should I eat?",
        locale="en",
        session_id=None,
    )
    assert [card["name"] for card in result.suggestions] == ["Rice bowl"]
    assert "ingredients" not in result.suggestions[0]


@pytest.mark.asyncio
async def test_fetch_hides_unverified_cards_when_user_has_allergies() -> None:
    discover = _FakeDiscover(
        ChatDiscoverBatch(
            session_id="sess-1",
            meals=(_meal("Egg rice bowl"), _meal("Yogurt cup")),
        )
    )
    service = ChatNextMealCandidates(discover)
    result = await service.fetch(
        user_id="u1",
        context=_context(allergies=["peanut"]),
        user_text="What should I eat?",
        locale="en",
        session_id=None,
    )
    assert result.suggestions == []


@pytest.mark.asyncio
async def test_rate_limit_skips_discover_call() -> None:
    discover = _FakeDiscover()
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


@pytest.mark.asyncio
async def test_discover_failure_returns_empty_cards() -> None:
    discover = _FakeDiscover(error=RuntimeError("discover down"))
    service = ChatNextMealCandidates(discover)
    result = await service.fetch(
        user_id="u1",
        context=_context(),
        user_text="What should I eat?",
        locale="en",
        session_id="sess-keep",
    )
    assert result.suggestions == []
    assert result.session_id is None


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
