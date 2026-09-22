from datetime import date
from decimal import Decimal

import pytest

from src.app.services.weekly_grocery_service import WeeklyGroceryService
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


class _Uow:
    catalog_recipes = _Catalog()
    weekly_meal_plans = _Plans()


@pytest.mark.asyncio
async def test_groceries_scale_people_and_subtract_pantry():
    categories = await WeeklyGroceryService().calculate(_Uow(), _plan())

    item = categories[0].items[0]
    assert item.category == "produce"
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


def test_quantity_normalization_only_uses_exact_conversions():
    assert normalize_ingredient_quantity(1, "kg").amount == Decimal("1000")
    assert normalize_ingredient_quantity(250, "g").unit == "g"
    assert normalize_ingredient_quantity(2, "pieces").dimension == "count:piece"
    count = normalize_ingredient_quantity(2, "tomatoes")
    assert count.dimension == "raw:tomatoes"
    assert count.confidence == "unknown"
