from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from src.api.schemas.response.weekly_meal_planner_responses import GroceryItemResponse
from src.app.services.weekly_grocery_service import (
    WeeklyGroceryService,
    _resolve_grocery_category,
    _slot_from_meal,
)
from src.domain.model.meal_recommendation import CatalogMeal, CatalogMealIngredient
from src.domain.model.weekly_meal_planner import (
    WeeklyMealPlan,
    WeeklyMealPlanPreferences,
    WeeklyMealPlanSlot,
    WeeklyMealPlanStatus,
)
from src.domain.services.meal_recommendation.ingredient_quantity_normalization import (
    normalize_ingredient_quantity,
)
from src.domain.services.weekly_meal_planner.grocery_projection import (
    SYNTHETIC_INGREDIENT_ID_OFFSET,
    GroceryIngredient,
    GroceryInteraction,
    GrocerySlot,
    PantryAvailability,
    aggregate_grocery,
    deterministic_ingredient_id,
)


def _plan():
    return WeeklyMealPlan(
        id="plan-1",
        user_id="user-1",
        week_start_date=date(2026, 9, 21),
        status=WeeklyMealPlanStatus.DRAFT,
        people=2,
        preferences=WeeklyMealPlanPreferences(people=2),
        timezone="UTC",
        slots=tuple(
            WeeklyMealPlanSlot(
                id=f"slot-{day}-{slot}",
                day_index=day,
                slot_index=slot,
                recipe_id="recipe-1" if day == 0 and slot == 0 else None,
            )
            for day in range(7)
            for slot in range(2)
        ),
    )


class _Catalog:
    async def get_meal(self, recipe_id):
        return CatalogMeal(
            id=recipe_id,
            catalog_key=recipe_id,
            content_hash="a" * 64,
            name="Tomato",
            cuisine="vietnamese",
            description=None,
            image_url=None,
            protein_g=Decimal("10"),
            carbs_g=Decimal("20"),
            fat_g=Decimal("3"),
            fiber_g=Decimal("2"),
            base_servings=1,
            serving_confidence="verified",
            meal_types=("lunch",),
            ingredients=(
                CatalogMealIngredient(
                    food_reference_id=7,
                    display_name="Tomato",
                    quantity=Decimal("150"),
                    unit="g",
                    category="produce",
                ),
            ),
        )


class _Plans:
    async def list_pantry(self, **kwargs):
        return [
            {
                "food_reference_id": 7,
                "available_amount": Decimal("100"),
                "available_unit": "g",
            }
        ]

    async def list_grocery_interactions(self, **kwargs):
        return []


class _Uow:
    catalog_recipes = _Catalog()
    weekly_meal_plans = _Plans()


@pytest.mark.asyncio
async def test_groceries_scale_people_and_subtract_pantry():
    categories = await WeeklyGroceryService().calculate(_Uow(), _plan())

    item = categories[0].items[0]
    assert item.category == "fresh_produce"
    assert item.total_needed == 300
    assert item.stock_amount == 100
    assert item.status == "need_more"
    assert item.quantity_confidence == "verified"


@pytest.mark.asyncio
async def test_groceries_do_not_silently_scale_unknown_servings():
    plan = _plan()

    class CatalogWithoutServing(_Catalog):
        async def get_meal(self, recipe_id):
            meal = await super().get_meal(recipe_id)
            return meal.__class__(
                **{
                    **meal.__dict__,
                    "base_servings": 1,
                    "serving_confidence": "unknown",
                }
            )

    class UowWithoutServing:
        catalog_recipes = CatalogWithoutServing()
        weekly_meal_plans = _Plans()

    categories = await WeeklyGroceryService().calculate(UowWithoutServing(), plan)

    item = categories[0].items[0]
    assert item.total_needed == 150
    assert item.quantity_confidence == "unscaled"


@pytest.mark.asyncio
async def test_groceries_allocate_partial_stock_once_across_days_and_keep_kind():
    plan = _plan()
    plan = WeeklyMealPlan(
        **{
            **plan.__dict__,
            "people": 1,
            "preferences": WeeklyMealPlanPreferences(people=1),
            "slots": tuple(
                WeeklyMealPlanSlot(
                    id=slot.id,
                    day_index=slot.day_index,
                    slot_index=slot.slot_index,
                    recipe_id=(
                        "recipe-1"
                        if slot.day_index in (0, 1) and slot.slot_index == 0
                        else None
                    ),
                )
                for slot in plan.slots
            ),
        }
    )

    class PlansWithBoughtStock(_Plans):
        async def list_pantry(self, **kwargs):
            return [
                {
                    "food_reference_id": 7,
                    "available_amount": Decimal("50"),
                    "available_unit": "g",
                }
            ]

        async def list_grocery_interactions(self, **kwargs):
            return [{"food_reference_id": 7, "checked": True}]

    class UowWithBoughtStock:
        catalog_recipes = _Catalog()
        weekly_meal_plans = PlansWithBoughtStock()

    item = (await WeeklyGroceryService().calculate(UowWithBoughtStock(), plan))[
        0
    ].items[0]

    assert item.stock_amount == 50
    assert item.stock_kind == "bought"
    assert item.status == "need_more"
    assert item.remaining == 250
    assert item.daily_amounts == (
        {"day_index": 0, "total_needed": 150, "remaining": 100},
        {"day_index": 1, "total_needed": 150, "remaining": 150},
    )
    response = GroceryItemResponse(**item.__dict__)
    assert response.remaining == 250
    assert response.stock_kind == "bought"
    assert [day.model_dump() for day in response.daily_amounts] == list(
        item.daily_amounts
    )

    class PlansWithOwnedStock(PlansWithBoughtStock):
        async def list_grocery_interactions(self, **kwargs):
            return [{"food_reference_id": 7, "manually_owned": True}]

    class UowWithOwnedStock:
        catalog_recipes = _Catalog()
        weekly_meal_plans = PlansWithOwnedStock()

    owned_item = (await WeeklyGroceryService().calculate(UowWithOwnedStock(), plan))[
        0
    ].items[0]
    assert owned_item.stock_kind == "owned"


def test_quantity_normalization_only_uses_exact_conversions():
    assert normalize_ingredient_quantity(1, "kg").amount == Decimal("1000")
    assert normalize_ingredient_quantity(250, "g").unit == "g"
    assert normalize_ingredient_quantity(2, "pieces").dimension == "count:piece"
    count = normalize_ingredient_quantity(2, "tomatoes")
    assert count.dimension == "raw:tomatoes"
    assert count.confidence == "unknown"


def test_eggplant_is_produce_while_eggs_are_protein():
    assert _resolve_grocery_category("Eggplant", None) == "fresh_produce"
    assert _resolve_grocery_category("2 eggs", None) == "protein"


@pytest.mark.asyncio
async def test_groceries_exclude_vietnamese_equipment_from_catalog_and_payload():
    class CatalogWithEquipment(_Catalog):
        async def get_meal(self, recipe_id):
            meal = await super().get_meal(recipe_id)
            return replace(
                meal,
                ingredients=(
                    *meal.ingredients,
                    CatalogMealIngredient(
                        food_reference_id=8,
                        display_name="Tô",
                        quantity=Decimal("1"),
                        unit="piece",
                    ),
                    CatalogMealIngredient(
                        food_reference_id=9,
                        display_name="Dụng cụ",
                        quantity=Decimal("20"),
                        unit="g",
                    ),
                ),
                recipe_payload={
                    "ingredients": [
                        {"name": "Dụng cụ sơ chế", "quantity": 15, "unit": "g"},
                        {
                            "name": "Olive oil",
                            "quantity": 10,
                            "unit": "g",
                        },
                        {
                            "name": "Whisk",
                            "quantity": 1,
                            "unit": "piece",
                            "is_equipment": True,
                        },
                    ]
                },
            )

    meal = await CatalogWithEquipment().get_meal("recipe-1")
    grocery_service = WeeklyGroceryService()

    class UowWithEquipment:
        catalog_recipes = CatalogWithEquipment()
        weekly_meal_plans = _Plans()

    categories = await grocery_service.calculate(UowWithEquipment(), _plan())
    calculated_names = {item.name for category in categories for item in category.items}
    proposal_items = grocery_service.project(_plan(), [meal])
    slot_names = {
        ingredient.name
        for ingredient in _slot_from_meal(
            meal, plan_people=2, override=None
        ).ingredients
    }

    assert calculated_names == {"Tomato", "Olive oil"}
    assert {item.name for item in proposal_items} == {"Tomato", "Olive oil"}
    assert {item.ingredient_id for item in proposal_items} == {
        7,
        deterministic_ingredient_id("Olive oil"),
    }
    assert slot_names == {"Tomato", "Olive oil"}


@pytest.mark.asyncio
async def test_unmapped_ingredients_are_not_doubled_in_groceries():
    meal = CatalogMeal(
        id="recipe-1",
        catalog_key="recipe-1",
        content_hash="f" * 64,
        name="Phi lê cá điêu hồng",
        cuisine="vietnamese",
        description=None,
        image_url=None,
        protein_g=Decimal("60"),
        carbs_g=Decimal("10"),
        fat_g=Decimal("15"),
        fiber_g=Decimal("1"),
        base_servings=1,
        serving_confidence="verified",
        meal_types=("lunch", "dinner"),
        ingredients=(
            CatalogMealIngredient(
                food_reference_id=None,
                display_name="Phi lê cá điêu hồng",
                quantity=Decimal("300"),
                unit="g",
                category="protein",
            ),
        ),
        recipe_payload={
            "ingredients": [
                {
                    "name": "Phi lê cá điêu hồng",
                    "quantity": 300,
                    "unit": "g",
                    "food_reference_id": None,
                }
            ]
        },
    )

    slot = _slot_from_meal(meal, plan_people=1, override=None)
    assert len(slot.ingredients) == 1
    assert slot.ingredients[0].name == "Phi lê cá điêu hồng"
    assert slot.ingredients[0].quantity == Decimal("300")

    grocery_service = WeeklyGroceryService()
    items = grocery_service.project(_plan(), [meal])
    fish_item = next(item for item in items if item.name == "Phi lê cá điêu hồng")
    # For 2 people in _plan() with base_servings=1, total needed should be 2 * 300 = 600g (NOT 1200g doubled)
    assert fish_item.total_needed == 600.0


def test_deterministic_ingredient_id_is_namespaced_to_prevent_collision():
    names = [
        "Water",
        "Salt",
        "Phi lê cá điêu hồng",
        "Rau muống",
        "Olive oil",
    ]
    for name in names:
        val = deterministic_ingredient_id(name)
        assert val >= SYNTHETIC_INGREDIENT_ID_OFFSET
        assert val <= 2_147_483_647


def test_unmapped_ingredient_does_not_collide_with_low_food_reference_id_pantry():
    unmapped_line = GroceryIngredient(
        position=0,
        food_reference_id=None,
        name="Special Herb",
        quantity=Decimal("50"),
        unit="g",
        category="produce",
    )
    slot = GrocerySlot(
        recipe_id="r1",
        publication_status="published",
        nutrition_status="ready",
        is_active=True,
        base_servings=1,
        serving_confidence="verified",
        people=1,
        ingredients=(unmapped_line,),
    )
    pantry = [
        PantryAvailability(
            food_reference_id=7,
            available_amount=Decimal("100"),
            available_unit="g",
        )
    ]
    interactions = [
        GroceryInteraction(
            food_reference_id=7,
            checked=True,
            do_not_buy=True,
        )
    ]
    items = aggregate_grocery([slot], pantry, interactions)
    assert len(items) == 1
    herb_item = items[0]
    assert herb_item.name == "Special Herb"
    assert herb_item.ingredient_id >= SYNTHETIC_INGREDIENT_ID_OFFSET
    assert herb_item.available_amount is None
    assert herb_item.checked is False
    assert herb_item.do_not_buy is False
    assert herb_item.status == "needed"

