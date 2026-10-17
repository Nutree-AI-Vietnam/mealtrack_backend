import asyncio

import pytest

from src.app.services.chat_next_meal_candidates import (
    ChatNextMealCandidates,
    SuggestionChatDiscoverAdapter,
    _fallback_meals_for_slot,
    _filter_meals_for_diet,
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
    assert discover.calls[0]["calorie_target"] == 450
    assert discover.calls[0]["protein_target"] == 34.6
    assert discover.calls[0]["carbs_target"] == 55.4
    assert discover.calls[0]["fat_target"] == 13.8


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


@pytest.mark.asyncio
async def test_fetch_does_not_pass_remaining_day_as_lunch_target() -> None:
    discover = _FakeDiscover(
        ChatDiscoverBatch(session_id="sess-lunch", meals=(_meal("Chicken Rice Bowl"),))
    )
    service = ChatNextMealCandidates(discover)
    result = await service.fetch(
        user_id="u1",
        context=_context(
            suggested_meal_slot="lunch",
            target_calories=1932,
            remaining_calories=1932,
            remaining_protein_g=119,
            remaining_carbs_g=222,
            remaining_fat_g=63,
        ),
        user_text="Trưa nay tôi nên ăn gì",
        locale="vi",
        session_id=None,
    )
    assert result.meal_slot == "lunch"
    call = discover.calls[0]
    assert call["calorie_target"] == 724
    assert call["calorie_target"] != 1932
    assert call["protein_target"] == 44.6
    assert call["carbs_target"] == 83.2
    assert call["fat_target"] == 23.6


def test_map_drops_meals_without_calories() -> None:
    cards = map_discover_meals(
        [{"name": "Mystery", "protein": 10}, {"name": "Oats", "calories": 300}],
        "breakfast",
    )
    assert [card["name"] for card in cards] == ["Oats"]


def test_map_preserves_recipe_steps_and_ingredients() -> None:
    meal = {
        "id": "m1",
        "name": "Ức gà áp chảo",
        "calories": 450,
        "ingredients": [{"name": "ức gà", "amount": 200, "unit": "g"}],
        "recipe_steps": [
            {"step": 1, "instruction": "Ướp ức gà", "duration_minutes": 5},
            {"step": 2, "instruction": "Áp chảo ức gà", "duration_minutes": 10},
        ],
    }
    cards = map_discover_meals([meal], "lunch")
    assert len(cards) == 1
    assert cards[0]["ingredients"] == [{"name": "ức gà", "amount": 200, "unit": "g"}]
    assert len(cards[0]["recipe_steps"]) == 2
    assert cards[0]["recipe_steps"][0]["instruction"] == "Ướp ức gà"


@pytest.mark.asyncio
async def test_fetch_prioritizes_recipe_generator_and_returns_full_recipe() -> None:
    class _FakeRecipeGen:
        async def generate_next_meal_recipes(self, **kwargs):
            del kwargs
            return [
                {
                    "name": "Cơm cá hồi",
                    "english_name": "Salmon rice",
                    "emoji": "🐟",
                    "calories": 500,
                    "protein_g": 35.0,
                    "carbs_g": 45.0,
                    "fat_g": 15.0,
                    "prep_time_minutes": 20,
                    "ingredients": [{"name": "cá hồi", "amount": 150, "unit": "g"}],
                    "recipe_steps": [
                        {
                            "step": 1,
                            "instruction": "Áp chảo cá hồi",
                            "duration_minutes": 8,
                        }
                    ],
                }
            ]

    discover = _FakeDiscover()
    service = ChatNextMealCandidates(discover, recipe_generator=_FakeRecipeGen())
    result = await service.fetch(
        user_id="u1",
        context=_context(),
        user_text="Gợi ý bữa trưa",
        locale="vi",
        session_id="sess-prev",
    )
    assert len(discover.calls) == 0  # Generator was used first, discover not called
    assert result.session_id is None
    assert len(result.suggestions) == 1
    card = result.suggestions[0]
    assert card["name"] == "Cơm cá hồi"
    assert card["ingredients"] == [{"name": "cá hồi", "amount": 150, "unit": "g"}]
    assert card["recipe_steps"][0]["instruction"] == "Áp chảo cá hồi"


class _Lookup:
    def __init__(self, macros=None, error=None):
        self.macros = macros
        self.error = error
        self.calls: list[list] = []

    async def calculate_meal_macros(self, ingredients):
        self.calls.append(ingredients)
        if self.error is not None:
            raise self.error
        return self.macros


class _IngredientCal:
    def __init__(self, calories: float):
        self.calories = calories


class _MealMacros:
    def __init__(self, calories: float, ingredients: list[_IngredientCal]):
        self.calories = calories
        self.protein = 32.0
        self.carbs = 40.0
        self.fat = 11.0
        self.fiber = 6.0
        self.ingredients = ingredients


def _recipe(**overrides) -> dict:
    meal = {
        "name": "Salmon rice",
        "english_name": "Salmon rice",
        "emoji": "🐟",
        "calories": 999,
        "protein_g": 1,
        "carbs_g": 1,
        "fat_g": 1,
        "ingredients": [
            {"name": "salmon", "amount": 150, "unit": "g"},
            {"name": "brown rice", "amount": 120, "unit": "g"},
            {"name": "broccoli", "amount": 80, "unit": "g"},
        ],
        "recipe_steps": [
            {"step": 1, "instruction": "Sear the salmon.", "duration_minutes": 8},
            {"step": 2, "instruction": "Serve with rice.", "duration_minutes": 2},
        ],
    }
    meal.update(overrides)
    return meal


@pytest.mark.asyncio
async def test_lookup_replaces_model_calories_and_adds_ingredient_kcal() -> None:
    class _Gen:
        async def generate_next_meal_recipes(self, **kwargs):
            del kwargs
            return [_recipe()]

    lookup = _Lookup(
        _MealMacros(
            410,
            [_IngredientCal(180), _IngredientCal(140), _IngredientCal(90)],
        )
    )
    service = ChatNextMealCandidates(
        _FakeDiscover(),
        recipe_generator=_Gen(),
        nutrition_lookup=lookup,
    )
    result = await service.fetch(
        user_id="u1",
        context=_context(),
        user_text="What should I eat next?",
        locale="en",
        session_id=None,
    )
    card = result.suggestions[0]
    assert card["calories"] == 410
    assert card["protein_g"] == 32.0
    assert card["calories"] != 999
    assert [item["calories"] for item in card["ingredients"]] == [180, 140, 90]
    assert lookup.calls[0][0]["name"] == "salmon"


@pytest.mark.asyncio
async def test_failed_lookup_keeps_generated_meal() -> None:
    class _Gen:
        async def generate_next_meal_recipes(self, **kwargs):
            del kwargs
            return [_recipe()]

    discover = _FakeDiscover(
        ChatDiscoverBatch(session_id="sess-lookup", meals=(_meal("Rice bowl"),))
    )
    service = ChatNextMealCandidates(
        discover,
        recipe_generator=_Gen(),
        nutrition_lookup=_Lookup(error=RuntimeError("lookup down")),
    )
    result = await service.fetch(
        user_id="u1",
        context=_context(),
        user_text="What should I eat next?",
        locale="en",
        session_id=None,
    )
    assert result.suggestions[0]["name"] == "Salmon rice"
    assert result.suggestions[0]["calories"] == 999
    assert discover.calls == []


@pytest.mark.asyncio
async def test_meal_lookups_run_together_and_keep_surviving_order() -> None:
    class _SlowLookup:
        def __init__(self) -> None:
            self.in_flight = 0
            self.max_in_flight = 0

        async def calculate_meal_macros(self, ingredients):
            name = ingredients[0]["name"]
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
            await asyncio.sleep(0.05)
            self.in_flight -= 1
            if name == "chicken":
                raise RuntimeError("lookup down")
            calories = {"salmon": 410.0, "tofu": 280.0}[name]
            return _MealMacros(calories, [_IngredientCal(calories)])

    class _Gen:
        async def generate_next_meal_recipes(self, **kwargs):
            del kwargs
            return [
                _recipe(
                    name="Salmon rice",
                    ingredients=[{"name": "salmon", "amount": 150, "unit": "g"}],
                ),
                _recipe(
                    name="Chicken bowl",
                    ingredients=[{"name": "chicken", "amount": 150, "unit": "g"}],
                ),
                _recipe(
                    name="Tofu bowl",
                    ingredients=[{"name": "tofu", "amount": 200, "unit": "g"}],
                ),
            ]

    lookup = _SlowLookup()
    service = ChatNextMealCandidates(
        _FakeDiscover(),
        recipe_generator=_Gen(),
        nutrition_lookup=lookup,
    )
    result = await service.fetch(
        user_id="u1",
        context=_context(),
        user_text="What should I eat next?",
        locale="en",
        session_id=None,
    )
    assert lookup.max_in_flight == 3
    assert [card["name"] for card in result.suggestions] == [
        "Salmon rice",
        "Chicken bowl",
        "Tofu bowl",
    ]
    assert [card["calories"] for card in result.suggestions] == [410, 999, 280]


@pytest.mark.asyncio
async def test_fetch_can_defer_food_photos() -> None:
    class _Gen:
        async def generate_next_meal_recipes(self, **kwargs):
            del kwargs
            return [_recipe()]

    calls: list[str] = []

    async def search(name: str) -> None:
        calls.append(name)
        return None

    service = ChatNextMealCandidates(
        _FakeDiscover(),
        recipe_generator=_Gen(),
        image_search=search,
    )
    result = await service.fetch(
        user_id="u1",
        context=_context(),
        user_text="What should I eat next?",
        locale="en",
        session_id=None,
        include_images=False,
    )
    assert calls == []
    await service.attach_images(result.suggestions)
    assert calls == ["Salmon rice"]


@pytest.mark.asyncio
async def test_zero_calorie_lookup_keeps_generated_meal() -> None:
    class _Gen:
        async def generate_next_meal_recipes(self, **kwargs):
            del kwargs
            return [
                _recipe(
                    name="Green bean porridge",
                    english_name="Green bean porridge",
                    calories=320,
                    ingredients=[{"name": "mung beans", "amount": 80, "unit": "g"}],
                )
            ]

    discover = _FakeDiscover(
        ChatDiscoverBatch(
            session_id="sess-zero",
            meals=(_meal("Greek Yogurt with Almonds and Blueberries"),),
        )
    )
    service = ChatNextMealCandidates(
        discover,
        recipe_generator=_Gen(),
        nutrition_lookup=_Lookup(_MealMacros(0, [])),
    )
    result = await service.fetch(
        user_id="u1",
        context=_context(),
        user_text="A hot soup please",
        locale="en",
        session_id=None,
        include_images=False,
    )
    assert result.suggestions[0]["name"] == "Green bean porridge"
    assert result.suggestions[0]["calories"] == 320
    assert discover.calls == []


@pytest.mark.asyncio
async def test_vegan_snack_fallback_is_not_greek_yogurt() -> None:
    class _Gen:
        async def generate_next_meal_recipes(self, **kwargs):
            del kwargs
            return [
                _recipe(
                    name="Sữa chua Hy Lạp mix hạnh nhân và việt quất",
                    english_name="Greek Yogurt with Almonds and Blueberries",
                    ingredients=[
                        {"name": "plain greek yogurt", "amount": 150, "unit": "g"}
                    ],
                )
            ]

    service = ChatNextMealCandidates(None, recipe_generator=_Gen())
    result = await service.fetch(
        user_id="tiendat",
        context=_context(
            dietary_preferences=["vegan"],
            suggested_meal_slot="snack",
            locale="vi",
        ),
        user_text="Món nước nào mà nóng đi, dễ ăn cho ngon",
        locale="vi",
        session_id=None,
        include_images=False,
    )
    blob = " ".join(
        f"{card['name']} {card.get('english_name', '')}" for card in result.suggestions
    ).casefold()
    assert result.meal_slot == "snack"
    assert result.suggestions
    assert "yogurt" not in blob
    assert "sữa chua" not in blob


def test_vegan_filter_drops_plural_eggs_and_keeps_plant_fats() -> None:
    dropped = _filter_meals_for_diet(
        [
            {
                "name": "Sunny-side up Eggs",
                "english_name": "Sunny-side up Eggs",
                "ingredients": [{"name": "eggs"}],
            }
        ],
        ["vegan"],
    )
    assert dropped == []

    kept = _filter_meals_for_diet(
        [
            {
                "name": "Peanut butter banana",
                "english_name": "Peanut butter banana",
                "ingredients": [{"name": "peanut butter"}, {"name": "banana"}],
            },
            {
                "name": "Cơm sữa dừa",
                "english_name": "Rice with coconut cream",
                "ingredients": [{"name": "coconut cream"}, {"name": "rice milk"}],
            },
        ],
        ["vegan"],
    )
    assert len(kept) == 2


def test_dairy_free_fallback_drops_yogurt_and_keeps_eggs() -> None:
    snacks = _filter_meals_for_diet(
        _fallback_meals_for_slot("snack", "en", 200),
        ["dairy_free"],
    )
    snack_names = " ".join(meal["english_name"] for meal in snacks).casefold()
    assert snacks
    assert "yogurt" not in snack_names
    assert "oat" in snack_names

    breakfasts = _filter_meals_for_diet(
        _fallback_meals_for_slot("breakfast", "en", 350),
        ["dairy_free"],
    )
    breakfast_names = " ".join(meal["english_name"] for meal in breakfasts).casefold()
    assert "egg" in breakfast_names


@pytest.mark.asyncio
async def test_vegetarian_lunch_fallback_drops_meat() -> None:
    class _Gen:
        async def generate_next_meal_recipes(self, **kwargs):
            del kwargs
            return []

    service = ChatNextMealCandidates(None, recipe_generator=_Gen())
    result = await service.fetch(
        user_id="u1",
        context=_context(
            dietary_preferences=["vegetarian"],
            suggested_meal_slot="lunch",
        ),
        user_text="What should I eat?",
        locale="en",
        session_id=None,
        include_images=False,
    )
    names = [card["name"].casefold() for card in result.suggestions]
    assert names
    assert all("chicken" not in name and "salmon" not in name for name in names)
    assert any("tofu" in name for name in names)
