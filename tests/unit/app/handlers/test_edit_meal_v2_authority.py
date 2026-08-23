import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.app.commands.meal import FoodItemChange
from src.app.commands.meal.edit_meal_command import EditMealCommand
from src.app.handlers.command_handlers.edit_meal_command_handler import (
    EditMealCommandHandler,
)
from src.domain.model.meal.food_item_change import (
    CustomNutritionData,
    NutritionOverride,
)
from src.domain.model.meal.meal import MealStatus
from src.domain.model.nutrition import FoodItem, Macros


class _References:
    async def get_nutrition_projection(self, food_reference_id):
        return SimpleNamespace(
            id=food_reference_id,
            name="Canonical rice",
            source="catalog",
            is_verified=True,
            protein_100g=2.7,
            carbs_100g=28.0,
            fat_100g=0.3,
            fiber_100g=0.4,
            sugar_100g=0.1,
            servings=[],
        )


class _PassthroughResolver:
    async def resolve_items(self, items, food_references, *, contract_version):
        return items

    async def revalidate_local_items(self, items, food_references):
        return None


@pytest.mark.asyncio
async def test_v2_add_allows_client_generated_id_not_owned_by_meal():
    current = FoodItem(
        id="item-1",
        name="Rice",
        quantity=100,
        unit="g",
        macros=Macros(protein=2.7, carbs=28.0, fat=0.3),
    )
    change = FoodItemChange(
        action="add",
        id="client-generated-id",
        name="Rau Xao",
        quantity=100,
        unit="g",
        origin="custom",
        custom_nutrition=CustomNutritionData(
            calories_per_100g=51,
            protein_per_100g=2,
            carbs_per_100g=8,
            fat_per_100g=1,
            fiber_per_100g=2,
            sugar_per_100g=1,
        ),
    )
    handler = EditMealCommandHandler(
        uow=None,
        nutrition_resolver=_PassthroughResolver(),
    )

    prepared = await handler._prepare_v2_changes(
        [current], [change], SimpleNamespace(food_references=object())
    )
    updated = await handler._apply_food_item_changes([current], prepared)

    assert prepared[0].id == "client-generated-id"
    assert len(updated) == 2
    added = next(item for item in updated if item.name == "Rau Xao")
    assert added.id != change.id
    uuid.UUID(added.id)
    assert added.source_kind == "custom"
    assert added.macros.protein == pytest.approx(2)


@pytest.mark.asyncio
async def test_v2_add_without_origin_is_rejected_before_id_lookup():
    current = FoodItem(
        id="item-1",
        name="Rice",
        quantity=100,
        unit="g",
        macros=Macros(protein=2.7, carbs=28.0, fat=0.3),
    )
    change = FoodItemChange(
        action="add",
        id="client-generated-id",
        name="Rau Xao",
        quantity=100,
        unit="g",
    )
    handler = EditMealCommandHandler(
        uow=None,
        nutrition_resolver=_PassthroughResolver(),
    )

    with pytest.raises(ValueError, match="v2 add requires origin"):
        await handler._prepare_v2_changes(
            [current], [change], SimpleNamespace(food_references=object())
        )


@pytest.mark.asyncio
async def test_v2_source_replacement_is_resolved_before_edit_strategy():
    current = FoodItem(
        id="item-1",
        name="Old rice",
        quantity=100,
        unit="g",
        macros=Macros(protein=1, carbs=1, fat=1),
    )
    change = FoodItemChange(
        action="update",
        id="item-1",
        origin="local",
        food_reference_id=42,
        quantity=100,
        unit="g",
    )
    handler = EditMealCommandHandler(uow=None)

    prepared = await handler._prepare_v2_changes(
        [current], [change], SimpleNamespace(food_references=_References())
    )

    assert prepared[0].name == "Canonical rice"
    assert prepared[0].custom_nutrition.protein_per_100g == pytest.approx(2.7)
    assert prepared[0].source_snapshot["basis"] == "100g"


@pytest.mark.asyncio
async def test_v2_source_replacement_preserves_client_unit_text():
    current = FoodItem(
        id="item-1",
        name="Old rice",
        quantity=100,
        unit="g",
        macros=Macros(protein=1, carbs=1, fat=1),
    )
    change = FoodItemChange(
        action="update",
        id="item-1",
        origin="local",
        food_reference_id=42,
        quantity=100,
        unit="g private-text",
    )
    handler = EditMealCommandHandler(uow=None)

    prepared = await handler._prepare_v2_changes(
        [current], [change], SimpleNamespace(food_references=_References())
    )
    updated = await handler._apply_food_item_changes([current], prepared)

    assert prepared[0].quantity == pytest.approx(100)
    assert prepared[0].unit == "g private-text"
    assert updated[0].quantity == pytest.approx(100)
    assert updated[0].unit == "g private-text"
    assert updated[0].macros.protein == pytest.approx(2.7)


@pytest.mark.asyncio
async def test_v2_quantity_update_preserves_client_unit_text():
    current = FoodItem(
        id="item-1",
        name="Canonical rice",
        quantity=1,
        unit="cup",
        macros=Macros(protein=4.266, carbs=44.24, fat=0.474),
        nutrition_contract_version="2",
        source_snapshot={
            "basis": "100g",
            "protein_per_100g": 2.7,
            "carbs_per_100g": 28.0,
            "fat_per_100g": 0.3,
            "fiber_per_100g": 0.4,
            "sugar_per_100g": 0.1,
            "serving_options": [
                {"unit": "g", "gram_weight": 1.0, "description": "1 g"},
                {"unit": "cup", "gram_weight": 158.0, "description": "cup"},
            ],
        },
    )
    change = FoodItemChange(
        action="update",
        id="item-1",
        quantity=100,
        unit="cup private-text",
    )
    handler = EditMealCommandHandler(uow=None)

    prepared = await handler._prepare_v2_changes(
        [current], [change], SimpleNamespace(food_references=_References())
    )
    updated = await handler._apply_food_item_changes([current], prepared)

    assert prepared[0].quantity == pytest.approx(100)
    assert prepared[0].unit == "cup private-text"
    assert updated[0].quantity == pytest.approx(100)
    assert updated[0].unit == "cup private-text"
    assert updated[0].macros.protein == pytest.approx(648.0)


@pytest.mark.asyncio
async def test_v2_quantity_update_preserves_translated_unit_text():
    current = FoodItem(
        id="item-1",
        name="Thịt",
        quantity=1,
        unit="serving",
        macros=Macros(protein=20, carbs=0, fat=16),
        nutrition_contract_version="2",
    )
    change = FoodItemChange(
        action="update",
        id="item-1",
        quantity=2,
        unit="phần",
    )
    handler = EditMealCommandHandler(uow=None)

    prepared = await handler._prepare_v2_changes(
        [current], [change], SimpleNamespace(food_references=object())
    )

    assert prepared[0].quantity == pytest.approx(2)
    assert prepared[0].unit == "phần"


@pytest.mark.asyncio
async def test_v2_quantity_update_preserves_unknown_unit_when_snapshot_missing():
    current = FoodItem(
        id="item-1",
        name="Pork rib",
        quantity=1,
        unit="slice",
        macros=Macros(protein=27, carbs=0, fat=15),
        nutrition_contract_version="2",
        serving_options=[
            {"unit": "g", "gram_weight": 1.0, "description": "1 g"},
            {"unit": "slice", "gram_weight": 80.0, "description": "1 slice"},
        ],
    )
    change = FoodItemChange(
        action="update",
        id="item-1",
        quantity=100,
        unit="unknown-unit",
    )
    handler = EditMealCommandHandler(uow=None)

    prepared = await handler._prepare_v2_changes(
        [current], [change], SimpleNamespace(food_references=object())
    )

    assert prepared[0].quantity == pytest.approx(100)
    assert prepared[0].unit == "unknown-unit"


@pytest.mark.asyncio
async def test_v2_item_override_does_not_require_intent_in_handler():
    current = FoodItem(
        id="item-1",
        name="Rice",
        quantity=100,
        unit="g",
        macros=Macros(protein=2.7, carbs=28.0, fat=0.3),
    )
    change = FoodItemChange(
        action="update",
        id="item-1",
        nutrition_override=NutritionOverride(
            calories=500,
            protein=20,
            carbs=30,
            fat=15,
        ),
    )
    handler = EditMealCommandHandler(uow=None)

    prepared = await handler._prepare_v2_changes(
        [current], [change], SimpleNamespace(food_references=object())
    )

    assert prepared[0].nutrition_override.calories == 500


@pytest.mark.asyncio
@pytest.mark.parametrize("clear_override", [False, True])
async def test_v2_item_override_allows_add_action_in_handler(clear_override):
    current = FoodItem(
        id="item-1",
        name="Rice",
        quantity=100,
        unit="g",
        macros=Macros(protein=2.7, carbs=28.0, fat=0.3),
    )
    change = FoodItemChange(
        action="add",
        id="client-generated-id",
        name="Rau xao",
        quantity=100,
        unit="g",
        origin="custom",
        custom_nutrition=CustomNutritionData(
            calories_per_100g=51,
            protein_per_100g=2,
            carbs_per_100g=8,
            fat_per_100g=1,
        ),
        clear_nutrition_override=clear_override,
        nutrition_override=(
            None
            if clear_override
            else NutritionOverride(calories=500, protein=20, carbs=30, fat=15)
        ),
    )
    handler = EditMealCommandHandler(
        uow=None,
        nutrition_resolver=_PassthroughResolver(),
    )

    prepared = await handler._prepare_v2_changes(
        [current], [change], SimpleNamespace(food_references=object())
    )
    updated = await handler._apply_food_item_changes([current], prepared)
    added = next(item for item in updated if item.name == "Rau xao")

    if clear_override:
        assert added.nutrition_override is None
    else:
        assert added.nutrition_override.calories == 500


@pytest.mark.asyncio
@pytest.mark.parametrize("clear_override", [False, True])
async def test_v2_item_override_allows_remove_action_in_handler(clear_override):
    change = FoodItemChange(
        action="remove",
        id="item-1",
        clear_nutrition_override=clear_override,
        nutrition_override=(
            None
            if clear_override
            else NutritionOverride(calories=500, protein=20, carbs=30, fat=15)
        ),
    )
    assert change.action == "remove"


@pytest.mark.asyncio
async def test_v2_remove_ignores_extra_override_fields_when_applying():
    current = FoodItem(
        id="item-1",
        name="Rice",
        quantity=100,
        unit="g",
        macros=Macros(protein=2.7, carbs=28.0, fat=0.3),
    )
    change = FoodItemChange(
        action="remove",
        id="item-1",
        quantity=200,
        nutrition_override=NutritionOverride(
            calories=500,
            protein=20,
            carbs=30,
            fat=15,
        ),
    )
    handler = EditMealCommandHandler(
        uow=None,
        nutrition_resolver=_PassthroughResolver(),
    )

    prepared = await handler._prepare_v2_changes(
        [current], [change], SimpleNamespace(food_references=object())
    )
    updated = await handler._apply_food_item_changes([current], prepared)

    assert prepared == [change]
    assert updated == []


class _FoodReferencesAdopt:
    """Fake ``uow.food_references`` exposing only ``adopt_provider_food``."""

    def __init__(self, response):
        self.response = response
        self.calls = []

    async def adopt_provider_food(
        self, namespace, food_id, english_name, per_100g, servings, locale, locale_name
    ):
        self.calls.append((namespace, food_id))
        return self.response

    async def get_nutrition_projection(self, food_reference_id):
        return None


class _AdoptUow:
    def __init__(self, food_references):
        self.food_references = food_references

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.mark.asyncio
async def test_v2_same_food_reference_replace_rebuilds_snapshot_from_catalog():
    """Re-selecting the same catalog row refreshes per-100g density without mutating food_reference."""
    current = FoodItem(
        id="item-1",
        name="Canonical rice",
        quantity=100,
        unit="g",
        macros=Macros(protein=1.0, carbs=28.0, fat=0.3),
        food_reference_id=42,
        nutrition_contract_version="2",
        source_snapshot={
            "basis": "100g",
            "protein_per_100g": 1.0,
            "carbs_per_100g": 28.0,
            "fat_per_100g": 0.3,
            "fiber_per_100g": 0.4,
            "sugar_per_100g": 0.1,
            "serving_options": [
                {"unit": "g", "gram_weight": 1.0, "description": "1 g"},
            ],
        },
    )
    change = FoodItemChange(
        action="update",
        id="item-1",
        origin="local",
        food_reference_id=42,
        quantity=100,
        unit="g",
    )
    handler = EditMealCommandHandler(uow=None)

    prepared = await handler._prepare_v2_changes(
        [current], [change], SimpleNamespace(food_references=_References())
    )

    assert prepared[0].source_snapshot["protein_per_100g"] == pytest.approx(2.7)
    assert prepared[0].custom_nutrition.protein_per_100g == pytest.approx(2.7)


@pytest.mark.asyncio
async def test_v2_add_by_fatsecret_id_adopts_once_not_search():
    """Edit-add with only provider identity must adopt, never search by name."""
    current = FoodItem(
        id="item-1",
        name="Rice",
        quantity=100,
        unit="g",
        macros=Macros(protein=2.7, carbs=28.0, fat=0.3),
    )
    change = FoodItemChange(
        action="add",
        id="client-generated-id",
        name="Thịt bò",
        quantity=60,
        unit="g",
        origin="provider",
        source_namespace="fatsecret",
        source_food_id="fs-beef",
    )
    provider = SimpleNamespace(
        get_food_details=AsyncMock(
            return_value={
                "food_id": "fs-beef",
                "food_name": "Beef",
                "protein_100g": 15.8,
                "carbs_100g": 0,
                "fat_100g": 11.7,
                "calories_100g": 172.0,
            }
        )
    )
    adopt_repo = _FoodReferencesAdopt(
        response={
            "id": 501,
            "name": "Beef",
            "protein_100g": 15.8,
            "carbs_100g": 0,
            "fat_100g": 11.7,
            "fiber_100g": 0,
            "sugar_100g": 0,
            "serving_options": [{"unit": "g", "gram_weight": 1.0, "description": "1 g"}],
        }
    )

    class _Budget:
        async def acquire(self, namespace, limit):
            return True

    handler = EditMealCommandHandler(
        uow=None,
        provider=provider,
        provider_budget=_Budget(),
        provider_rpm=10,
        uow_factory=lambda: _AdoptUow(adopt_repo),
    )

    prepared = await handler._prepare_v2_changes(
        [current], [change], SimpleNamespace(food_references=adopt_repo)
    )

    assert prepared[0].food_reference_id == 501
    assert len(adopt_repo.calls) == 1
    assert adopt_repo.calls[0] == ("fatsecret", "fs-beef")
    provider.get_food_details.assert_awaited_once_with("fs-beef")
    assert not hasattr(provider, "search_foods")


@pytest.mark.asyncio
async def test_v2_add_by_food_reference_id_never_calls_provider():
    """Edit-add with an owned catalog id must not touch the provider at all."""
    current = FoodItem(
        id="item-1",
        name="Old rice",
        quantity=100,
        unit="g",
        macros=Macros(protein=1, carbs=1, fat=1),
    )
    change = FoodItemChange(
        action="add",
        id="client-generated-id",
        origin="local",
        food_reference_id=42,
        quantity=100,
        unit="g",
    )
    provider = SimpleNamespace(get_food_details=AsyncMock())
    handler = EditMealCommandHandler(
        uow=None,
        provider=provider,
    )

    prepared = await handler._prepare_v2_changes(
        [current], [change], SimpleNamespace(food_references=_References())
    )

    assert prepared[0].food_reference_id == 42
    provider.get_food_details.assert_not_awaited()


@pytest.mark.asyncio
async def test_v2_meal_override_does_not_require_intent_in_preflight():
    class _Meals:
        async def find_by_id(self, meal_id, projection=None):
            return SimpleNamespace(
                meal_id=meal_id,
                user_id="user-1",
                status=MealStatus.READY,
                nutrition=SimpleNamespace(),
            )

    class _UowContext:
        async def __aenter__(self):
            return SimpleNamespace(meals=_Meals())

        async def __aexit__(self, exc_type, exc, tb):
            return False

    handler = EditMealCommandHandler(
        uow=None,
        uow_factory=lambda: _UowContext(),
    )
    command = EditMealCommand(
        meal_id="meal-1",
        user_id="user-1",
        nutrition_contract_version=2,
        nutrition_override=NutritionOverride(
            calories=500,
            protein=20,
            carbs=30,
            fat=15,
        ),
    )

    meal = await handler._preflight_v2_meal(command)

    assert meal.meal_id == "meal-1"
