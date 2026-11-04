import dataclasses
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from src.app.services.recommended_meal_materialization_service import (
    RecommendedMealMaterializationService,
)
from src.domain.model.meal_recommendation import (
    CatalogMeal,
    CatalogMealIngredient,
)
from src.domain.ports.food_reference_repository_port import (
    FoodReferenceNutritionProjection,
)
from src.domain.services.meal_nrf_fields import meal_nrf_fields
from src.domain.utils.timezone_utils import ensure_utc, utc_now


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
        self.preserve_nutrient_units = False

    async def get_nutrition_projections(
        self,
        food_reference_ids,
        *,
        for_update=False,
        preserve_nutrient_units=False,
    ):
        self.preserve_nutrient_units = preserve_nutrient_units
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


_USER_ID = "00000000-0000-0000-0000-000000000001"
_MEAL_DATE = date(2026, 7, 16)


def _catalog_meal() -> CatalogMeal:
    return CatalogMeal(
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


@pytest.mark.asyncio
async def test_materializer_preserves_food_reference_ids_and_macro_snapshot():
    catalog_meal = _catalog_meal()

    meal = await RecommendedMealMaterializationService().materialize_from_catalog(
        _Uow(),
        user_id=_USER_ID,
        catalog_meal=catalog_meal,
        meal_date=_MEAL_DATE,
        meal_type="breakfast",
        timezone="UTC",
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
    catalog_meal = _catalog_meal()
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

    meal = await RecommendedMealMaterializationService().materialize_from_catalog(
        uow,
        user_id=_USER_ID,
        catalog_meal=catalog_meal,
        meal_date=_MEAL_DATE,
        meal_type="breakfast",
        timezone="UTC",
    )

    item = meal.nutrition.food_items[0]
    assert item.macros.protein == pytest.approx(31.0)
    assert item.macros.fat == pytest.approx(3.6)
    assert item.calories == pytest.approx(31.0 * 4 + 3.6 * 9)


@pytest.mark.asyncio
async def test_materializer_preserves_complete_micros_for_nrf_score_parity():
    catalog_meal = _catalog_meal()
    source_micros = {
        "vitamin_a_mcg": 900,
        "vitamin_c_mg": 90,
        "vitamin_e_mg": 15,
        "calcium_mg": 1300,
        "iron_mg": 18,
        "magnesium_mg": 420,
        "potassium_mg": 4700,
        "saturated_fat_g": 20,
        "added_sugar_g": 50,
        "sodium_mg": {"amount": 0.2, "unit": "g"},
    }
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
                    extra_nutrients=source_micros,
                )
            }
        )
    )

    meal = await RecommendedMealMaterializationService().materialize_from_catalog(
        uow,
        user_id=_USER_ID,
        catalog_meal=catalog_meal,
        meal_date=_MEAL_DATE,
        meal_type="lunch",
        timezone="UTC",
        portion_multiplier=1.5,
        source="weekly_meal_planner",
    )

    assert meal.nutrition is not None
    assert meal.nutrition.micros is not None
    assert meal.nutrition.micros.iron == pytest.approx(27.0)
    assert meal.nutrition.micros.sodium == pytest.approx(300.0)
    assert uow.food_references.preserve_nutrient_units is True
    fields = meal_nrf_fields(meal)
    assert fields["nrf_coverage"] == 10
    assert fields["nrf_quality"] is not None


@pytest.mark.asyncio
async def test_materializer_attaches_catalog_image_url_when_present():
    catalog_meal = dataclasses.replace(
        _catalog_meal(), image_url="https://cdn.example.com/meals/tuna.jpg"
    )

    meal = await RecommendedMealMaterializationService().materialize_from_catalog(
        _Uow(),
        user_id=_USER_ID,
        catalog_meal=catalog_meal,
        meal_date=_MEAL_DATE,
        meal_type="breakfast",
        timezone="UTC",
    )

    assert meal.image is not None
    assert meal.image.url == "https://cdn.example.com/meals/tuna.jpg"


@pytest.mark.asyncio
async def test_materializer_keeps_long_catalog_image_urls():
    long_url = (
        "https://res.cloudinary.com/demo/image/upload/"
        "c_fill,w_1200,h_1200,q_auto:good,f_auto,fl_progressive/"
        "v1720000000/mealtrack/" + ("catalog-meal-" + "x" * 1200) + ".jpg"
    )
    assert len(long_url) > 1024
    catalog_meal = dataclasses.replace(_catalog_meal(), image_url=long_url)

    meal = await RecommendedMealMaterializationService().materialize_from_catalog(
        _Uow(),
        user_id=_USER_ID,
        catalog_meal=catalog_meal,
        meal_date=_MEAL_DATE,
        meal_type="breakfast",
        timezone="UTC",
    )

    assert meal.image is not None
    assert meal.image.url == long_url


@pytest.mark.asyncio
async def test_materializer_scales_backend_macros_for_weekly_portions():
    catalog_meal = _catalog_meal()

    meal = await RecommendedMealMaterializationService().materialize_from_catalog(
        _Uow(),
        user_id=_USER_ID,
        catalog_meal=catalog_meal,
        meal_date=_MEAL_DATE,
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
async def test_materializer_skips_projection_lookup_for_unlinked_ingredients():
    catalog_meal = _catalog_meal()
    unlinked = CatalogMealIngredient(
        food_reference_id=None,
        display_name="Ingredient",
        quantity=Decimal("100"),
        unit="g",
    )
    catalog_meal = dataclasses.replace(catalog_meal, ingredients=(unlinked, unlinked))
    food_references = _FoodRefRepo({})
    food_references.get_nutrition_projections = _fail_if_called

    meal = await RecommendedMealMaterializationService().materialize_from_catalog(
        _Uow(food_references=food_references),
        user_id=_USER_ID,
        catalog_meal=catalog_meal,
        meal_date=_MEAL_DATE,
        meal_type="lunch",
        timezone="UTC",
        source="weekly_meal_planner",
    )

    assert meal.nutrition is not None
    assert meal.nutrition.macros.protein == 30


async def _fail_if_called(*args, **kwargs):
    raise AssertionError("projection lookup must not receive unlinked ingredients")


@pytest.mark.asyncio
async def test_materializer_scales_published_recipe_payload_without_mutating_catalog():
    catalog_meal = _catalog_meal()
    catalog_meal = dataclasses.replace(
        catalog_meal,
        recipe_payload={
            "recipe_name": catalog_meal.name,
            "ingredients": [
                {"name": "rice", "quantity": 200, "unit": "g"},
                {"name": "salt", "quantity": None, "unit": None},
            ],
        },
    )

    meal = await RecommendedMealMaterializationService().materialize_from_catalog(
        _Uow(),
        user_id=_USER_ID,
        catalog_meal=catalog_meal,
        meal_date=_MEAL_DATE,
        meal_type="lunch",
        timezone="UTC",
        portion_multiplier=0.5,
    )

    assert meal.recipe_snapshot["ingredients"][0]["quantity"] == 100
    assert meal.recipe_snapshot["ingredients"][1]["quantity"] is None
    assert catalog_meal.recipe_payload["ingredients"][0]["quantity"] == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("for_today", [True, False])
async def test_materializer_stamps_today_now_and_other_days_at_local_noon(for_today):
    catalog_meal = _catalog_meal()
    before = utc_now()
    meal_date = before.date() if for_today else date(2026, 7, 16)

    meal = await RecommendedMealMaterializationService().materialize_from_catalog(
        _Uow(),
        user_id=_USER_ID,
        catalog_meal=catalog_meal,
        meal_date=meal_date,
        meal_type="lunch",
        timezone="UTC",
    )

    created_at = ensure_utc(meal.created_at)
    if for_today:
        assert before <= created_at <= utc_now()
    else:
        assert created_at == datetime(2026, 7, 16, 12, tzinfo=UTC)


@pytest.mark.asyncio
@pytest.mark.parametrize("for_today", [True, False])
async def test_materializer_marks_only_a_log_for_today_as_logged_on_its_day(
    for_today,
):
    catalog_meal = _catalog_meal()
    meal_date = utc_now().date() if for_today else date(2026, 7, 16)

    meal = await RecommendedMealMaterializationService().materialize_from_catalog(
        _Uow(),
        user_id=_USER_ID,
        catalog_meal=catalog_meal,
        meal_date=meal_date,
        meal_type="lunch",
        timezone="UTC",
    )

    assert meal.logged_on_day is for_today


def _saved_pho(**overrides):
    from src.domain.model.weekly_meal_planner import SavedRecipeSnapshot

    data = {
        "meal_name": "Chicken pho",
        "description": "Clear broth",
        "emoji": "🍜",
        "macros": {"protein": 30.0, "carbs": 50.0, "fat": 10.0, "fiber": 4.0},
        "servings": 4,
        "image_url": "https://images.example/pho.jpg",
        "prep_time_minutes": 45,
        "cuisine_type": "Vietnamese",
        "ingredients": [
            {
                "name": "Chicken",
                "amount": 400,
                "unit": "g",
                "food_reference_id": 123,
                "protein": 100.0,
                "carbs": 0.0,
                "fat": 20.0,
            },
            # Reference that no longer exists: kept as a custom line.
            {
                "name": "Noodles",
                "amount": 2,
                "unit": "",
                "food_reference_id": 999,
                "protein": 20.0,
                "carbs": 200.0,
                "fat": 20.0,
            },
            {"name": "Salt", "amount": 0, "unit": ""},
        ],
        "recipe_steps": [{"step": 1, "instruction": "Simmer.", "duration_minutes": 30}],
        "recipe_import": {"servings_estimated": False},
    }
    data.update(overrides)
    return SavedRecipeSnapshot.from_saved_suggestion("recipe_import_imp-1", data)


def _projection(food_id):
    return FoodReferenceNutritionProjection(
        id=food_id,
        name="Chicken",
        source="catalog_seed",
        is_verified=True,
        protein_100g=25.0,
        carbs_100g=0.0,
        fat_100g=5.0,
        fiber_100g=0.0,
        sugar_100g=0.0,
    )


@pytest.mark.asyncio
async def test_saved_recipe_becomes_a_meal_with_its_per_serving_macros():
    uow = _Uow(food_references=_FoodRefRepo({123: _projection(123)}))

    meal = await RecommendedMealMaterializationService().materialize_from_saved_recipe(
        uow,
        user_id="00000000-0000-0000-0000-000000000001",
        recipe=_saved_pho(),
        meal_date=date(2026, 9, 22),
        meal_type="dinner",
        timezone="UTC",
        portion_multiplier=2.0,
        source="weekly_meal_planner",
    )

    assert uow.meals.saved is meal
    assert (meal.dish_name, meal.meal_type, meal.source) == (
        "Chicken pho",
        "dinner",
        "weekly_meal_planner",
    )
    macros = meal.nutrition.macros
    assert (macros.protein, macros.carbs, macros.fat, macros.fiber) == (
        60.0,
        100.0,
        20.0,
        8.0,
    )
    # Backend formula over the doubled per-serving macros.
    assert meal.nutrition.calories == pytest.approx(60 * 4 + 92 * 4 + 8 * 2 + 20 * 9)
    assert meal.catalog_meal_id is None
    assert meal.recipe_snapshot["saved_suggestion_id"] == "recipe_import_imp-1"
    assert meal.nutrition_snapshot["source"] == "saved_recipe"
    assert meal.image.url == "https://images.example/pho.jpg"
    assert meal.instructions == [{"instruction": "Simmer.", "duration_minutes": 30}]
    assert (meal.cook_time_min, meal.cuisine_type, meal.emoji) == (
        45,
        "Vietnamese",
        "🍜",
    )
    chicken, noodles = meal.nutrition.food_items
    # 400 g for 4 servings, eaten twice over: 200 g.
    assert (chicken.quantity, chicken.unit, chicken.food_reference_id) == (
        200.0,
        "g",
        123,
    )
    assert chicken.macros.protein == pytest.approx(50.0)
    assert (noodles.quantity, noodles.unit, noodles.food_reference_id) == (
        1.0,
        "piece",
        None,
    )
    assert noodles.is_custom and not chicken.is_custom


@pytest.mark.asyncio
async def test_saved_recipe_without_line_macros_spreads_the_meal_macros():
    recipe = _saved_pho(
        servings=1,
        ingredients=[
            {"name": "Tofu", "amount": 300, "unit": "g"},
            {"name": "Rice", "amount": 100, "unit": "g"},
        ],
    )

    meal = await RecommendedMealMaterializationService().materialize_from_saved_recipe(
        _Uow(),
        user_id="00000000-0000-0000-0000-000000000001",
        recipe=recipe,
        meal_date=date(2026, 9, 22),
        meal_type="lunch",
        timezone="UTC",
    )

    tofu, rice = meal.nutrition.food_items
    assert tofu.macros.protein == pytest.approx(22.5)
    assert rice.macros.protein == pytest.approx(7.5)


@pytest.mark.asyncio
async def test_saved_recipe_rejects_unsupported_portions():
    with pytest.raises(ValueError):
        await RecommendedMealMaterializationService().materialize_from_saved_recipe(
            _Uow(),
            user_id="00000000-0000-0000-0000-000000000001",
            recipe=_saved_pho(),
            meal_date=date(2026, 9, 22),
            meal_type="lunch",
            timezone="UTC",
            portion_multiplier=3.0,
        )
