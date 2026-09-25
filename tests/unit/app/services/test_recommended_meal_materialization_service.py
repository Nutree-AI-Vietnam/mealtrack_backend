import dataclasses
from datetime import date
from decimal import Decimal

import pytest

from src.app.services.recommended_meal_materialization_service import (
    RecommendedMealMaterializationService,
)
from src.domain.exceptions.meal_recommendation_exceptions import (
    MealRecommendationNotFoundError,
)
from src.domain.model.meal_recommendation import (
    CatalogMeal,
    CatalogMealIngredient,
    PersistedMealRecommendationCandidate,
    PersistedMealRecommendationPlan,
    PersistedMealRecommendationSlot,
)
from src.domain.ports.food_reference_repository_port import (
    FoodReferenceNutritionProjection,
)


class _CatalogRepo:
    async def get_meal(self, catalog_meal_id):
        return CatalogMeal(
            id=catalog_meal_id,
            catalog_key="catalog-key",
            content_hash="a" * 64,
            name="Catalog Recipe",
            cuisine="vietnamese",
            description=None,
            image_url=None,
            protein_g=Decimal("30"),
            carbs_g=Decimal("50"),
            fat_g=Decimal("10"),
            fiber_g=Decimal("5"),
            meal_types=("breakfast",),
            ingredients=(
                CatalogMealIngredient(
                    food_reference_id=123,
                    display_name="Ingredient",
                    quantity=Decimal("100"),
                    unit="g",
                ),
            ),
        )


class _MissingCatalogRepo:
    async def get_meal(self, catalog_meal_id):
        return None


class _MealRepo:
    def __init__(self):
        self.saved = None

    async def save(self, meal):
        self.saved = meal
        return meal


class _FoodRefRepo:
    def __init__(self, projections: dict[int, FoodReferenceNutritionProjection]):
        self._projections = projections

    async def get_nutrition_projections(self, food_reference_ids, *, for_update=False):
        return {
            food_id: self._projections[food_id]
            for food_id in food_reference_ids
            if food_id in self._projections
        }


class _Uow:
    def __init__(self, catalog_recipes=None, food_references=None):
        self.catalog_recipes = catalog_recipes or _CatalogRepo()
        self.meals = _MealRepo()
        if food_references is not None:
            self.food_references = food_references


def _plan_and_slot():
    plan = PersistedMealRecommendationPlan(
        id="plan-1",
        user_id="00000000-0000-0000-0000-000000000001",
        status="active",
        timezone="UTC",
        start_date=date(2026, 7, 16),
        daily_calories=2000,
        operation="three_day",
        idempotency_key="key",
        request_fingerprint="f" * 64,
    )
    catalog_meal = CatalogMeal(
        id="catalog-1",
        catalog_key="catalog-key",
        content_hash="a" * 64,
        name="Catalog Recipe",
        cuisine="vietnamese",
        description=None,
        image_url=None,
        protein_g=Decimal("30"),
        carbs_g=Decimal("50"),
        fat_g=Decimal("10"),
        fiber_g=Decimal("5"),
        meal_types=("breakfast",),
        ingredients=(
            CatalogMealIngredient(
                food_reference_id=123,
                display_name="Ingredient",
                quantity=Decimal("100"),
                unit="g",
            ),
        ),
    )
    slot = PersistedMealRecommendationSlot(
        id="slot-1",
        slot_date=date(2026, 7, 16),
        day_index=0,
        meal_type="breakfast",
        catalog_meal_id="catalog-1",
        target_calories=500,
        score=1.0,
        position=0,
        selected=PersistedMealRecommendationCandidate(
            id="candidate-1",
            slot_id="slot-1",
            recommendation_date=date(2026, 7, 16),
            meal_type="breakfast",
            catalog_meal_id="catalog-1",
            candidate_rank=0,
            is_selected=True,
            score=Decimal("1.0"),
            selection_version=1,
            catalog_meal=catalog_meal,
        ),
    )
    return plan, slot


@pytest.mark.asyncio
async def test_materializer_preserves_food_reference_ids_and_macro_snapshot():
    plan, slot = _plan_and_slot()

    meal = await RecommendedMealMaterializationService().materialize(
        _Uow(),
        plan=plan,
        slot=slot,
    )

    assert meal.source == "meal_recommendation"
    assert meal.nutrition is not None
    assert meal.nutrition.macros.protein == 30
    assert meal.nutrition.food_items[0].food_reference_id == 123
    item = meal.nutrition.food_items[0]
    assert item.macros.protein == 30
    assert item.macros.carbs == 50
    assert item.macros.fat == 10
    assert item.macros.fiber == 5
    assert item.calories > 0
    # Placeholder image keeps inserts valid when meal.image_id is NOT NULL.
    assert meal.image is not None
    assert meal.image.url is None
    assert meal.image.size_bytes == 1


@pytest.mark.asyncio
async def test_materializer_scales_food_reference_macros_onto_items():
    plan, slot = _plan_and_slot()
    uow = _Uow(
        food_references=_FoodRefRepo(
            {
                123: FoodReferenceNutritionProjection(
                    id=123,
                    name="Ingredient",
                    source="catalog_seed",
                    is_verified=True,
                    protein_100g=31.0,
                    carbs_100g=0.0,
                    fat_100g=3.6,
                    fiber_100g=0.0,
                    sugar_100g=0.0,
                    density_g_ml=1.0,
                )
            }
        )
    )

    meal = await RecommendedMealMaterializationService().materialize(
        uow,
        plan=plan,
        slot=slot,
    )

    item = meal.nutrition.food_items[0]
    assert item.macros.protein == pytest.approx(31.0)
    assert item.macros.fat == pytest.approx(3.6)
    assert item.calories == pytest.approx(31.0 * 4 + 3.6 * 9)


@pytest.mark.asyncio
async def test_materializer_attaches_catalog_image_url_when_present():
    plan, slot = _plan_and_slot()
    catalog_meal = slot.selected.catalog_meal
    assert catalog_meal is not None
    slot = PersistedMealRecommendationSlot(
        **{
            **slot.__dict__,
            "selected": PersistedMealRecommendationCandidate(
                **{
                    **slot.selected.__dict__,
                    "catalog_meal": CatalogMeal(
                        **{
                            **catalog_meal.__dict__,
                            "image_url": "https://cdn.example.com/meals/tuna.jpg",
                        }
                    ),
                }
            ),
        }
    )

    meal = await RecommendedMealMaterializationService().materialize(
        _Uow(),
        plan=plan,
        slot=slot,
    )

    assert meal.image is not None
    assert meal.image.url == "https://cdn.example.com/meals/tuna.jpg"


@pytest.mark.asyncio
async def test_materializer_keeps_long_catalog_image_urls():
    plan, slot = _plan_and_slot()
    catalog_meal = slot.selected.catalog_meal
    assert catalog_meal is not None
    long_url = (
        "https://res.cloudinary.com/demo/image/upload/"
        "c_fill,w_1200,h_1200,q_auto:good,f_auto,fl_progressive/"
        "v1720000000/mealtrack/" + ("catalog-meal-" + "x" * 1200) + ".jpg"
    )
    assert len(long_url) > 1024
    slot = PersistedMealRecommendationSlot(
        **{
            **slot.__dict__,
            "selected": PersistedMealRecommendationCandidate(
                **{
                    **slot.selected.__dict__,
                    "catalog_meal": CatalogMeal(
                        **{
                            **catalog_meal.__dict__,
                            "image_url": long_url,
                        }
                    ),
                }
            ),
        }
    )

    meal = await RecommendedMealMaterializationService().materialize(
        _Uow(),
        plan=plan,
        slot=slot,
    )

    assert meal.image is not None
    assert meal.image.url == long_url


@pytest.mark.asyncio
async def test_materializer_fails_with_public_error_when_selected_meal_missing():
    plan, slot = _plan_and_slot()
    slot = PersistedMealRecommendationSlot(**{**slot.__dict__, "selected": None})

    with pytest.raises(MealRecommendationNotFoundError):
        await RecommendedMealMaterializationService().materialize(
            _Uow(),
            plan=plan,
            slot=slot,
        )


@pytest.mark.asyncio
async def test_materializer_scales_backend_macros_for_weekly_portions():
    plan, slot = _plan_and_slot()
    assert slot.selected is not None and slot.selected.catalog_meal is not None

    meal = await RecommendedMealMaterializationService().materialize_from_catalog(
        _Uow(),
        user_id=plan.user_id,
        catalog_meal=slot.selected.catalog_meal,
        meal_date=plan.start_date,
        meal_type="lunch",
        timezone="UTC",
        portion_multiplier=1.5,
        source="weekly_meal_planner",
    )

    assert meal.source == "weekly_meal_planner"
    assert meal.nutrition is not None
    assert meal.nutrition.macros.protein == 45
    assert meal.nutrition.macros.carbs == 75
    assert meal.nutrition.macros.fat == 15
    assert meal.recipe_snapshot is not None
    assert meal.recipe_snapshot["portion_multiplier"] == 1.5
    assert meal.recipe_snapshot["ingredients"][0]["quantity"] == 150


@pytest.mark.asyncio
async def test_materializer_scales_published_recipe_payload_without_mutating_catalog():
    plan, slot = _plan_and_slot()
    assert slot.selected is not None and slot.selected.catalog_meal is not None
    catalog_meal = dataclasses.replace(
        slot.selected.catalog_meal,
        recipe_payload={
            "recipe_name": slot.selected.catalog_meal.name,
            "ingredients": [
                {"name": "rice", "quantity": 200, "unit": "g"},
                {"name": "salt", "quantity": None, "unit": None},
            ],
        },
    )

    meal = await RecommendedMealMaterializationService().materialize_from_catalog(
        _Uow(),
        user_id=plan.user_id,
        catalog_meal=catalog_meal,
        meal_date=plan.start_date,
        meal_type="lunch",
        timezone="UTC",
        portion_multiplier=0.5,
    )

    assert meal.recipe_snapshot["ingredients"][0]["quantity"] == 100
    assert meal.recipe_snapshot["ingredients"][1]["quantity"] is None
    assert catalog_meal.recipe_payload["ingredients"][0]["quantity"] == 200
