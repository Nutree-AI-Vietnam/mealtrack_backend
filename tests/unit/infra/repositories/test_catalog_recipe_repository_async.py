from unittest.mock import MagicMock

import pytest

from src.infra.repositories.catalog_recipe_repository_async import (
    AsyncCatalogMealRepository,
)


class _Scalars:
    def __init__(self, rows):
        self._rows = rows

    def unique(self):
        return self

    def all(self):
        return self._rows


class _Result:
    def __init__(self, rows=None, one=None):
        self._rows = rows or []
        self._one = one

    def one(self):
        return self._one

    def scalars(self):
        return _Scalars(self._rows)

    def scalar_one_or_none(self):
        return self._one


class _AsyncSession:
    def __init__(self, results):
        self._results = list(results)
        self.statement = None

    async def execute(self, statement):
        self.statement = statement
        return self._results.pop(0)


def _meal_row():
    food_reference = MagicMock()
    food_reference.id = 7
    food_reference.name = "Rice"
    food_reference.source = "catalog_seed"
    food_reference.is_verified = True
    food_reference.protein_100g = 2.7
    food_reference.carbs_100g = 28.0
    food_reference.fat_100g = 0.3
    food_reference.fiber_100g = 0.4
    food_reference.sugar_100g = 0.1
    food_reference.density = 1.0
    food_reference.serving_size_rows = []
    food_reference.nutrient_rows = []
    food_reference.extra_nutrients = None

    ingredient = MagicMock()
    ingredient.food_reference_id = 7
    ingredient.display_name = "Rice"
    ingredient.quantity = 100
    ingredient.unit = "g"
    ingredient.food_reference = food_reference

    row = MagicMock()
    row.id = "catalog-1"
    row.catalog_key = "vn-rice"
    row.content_hash = "a" * 64
    row.name = "Rice Bowl"
    row.cuisine = "vietnamese"
    row.description = None
    row.image_url = None
    row.breakfast_eligible = True
    row.lunch_eligible = False
    row.dinner_eligible = False
    row.snack_eligible = False
    row.is_active = True
    row.popularity_rank = 1
    row.ingredients = [ingredient]
    return row


@pytest.mark.asyncio
async def test_get_meal_detail_scales_canonical_micros_by_resolved_ingredient_weight():
    row = _meal_row()
    row.ingredients[0].food_reference.extra_nutrients = {
        "iron_mg": {"amount": 2.0, "unit": "mg"},
        "vitamin_a_mcg": {"amount": 300.0, "unit": "mcg"},
        "sodium_mg": {"amount": 0.2, "unit": "g"},
    }
    row.ingredients[0].quantity = 50
    row.steps = []
    session = _AsyncSession([_Result(one=row)])

    meal = await AsyncCatalogMealRepository(session).get_meal_detail("catalog-1")

    assert meal is not None
    assert meal.nutrition_micros is not None
    assert meal.nutrition_micros.iron == pytest.approx(1.0)
    assert meal.nutrition_micros.vitamin_a == pytest.approx(150.0)
    assert meal.nutrition_micros.sodium == pytest.approx(100.0)


@pytest.mark.asyncio
async def test_get_meal_detail_keeps_micros_unknown_when_an_ingredient_is_missing_data():
    row = _meal_row()
    row.ingredients[0].food_reference.extra_nutrients = {"iron_mg": 2.0}
    second_reference = MagicMock()
    second_reference.id = 8
    second_reference.name = "Chicken"
    second_reference.source = "catalog_seed"
    second_reference.is_verified = True
    second_reference.protein_100g = 23.0
    second_reference.carbs_100g = 0.0
    second_reference.fat_100g = 2.5
    second_reference.fiber_100g = 0.0
    second_reference.sugar_100g = 0.0
    second_reference.density = 1.0
    second_reference.serving_size_rows = []
    second_reference.nutrient_rows = []
    second_reference.extra_nutrients = None
    ingredient = MagicMock()
    ingredient.food_reference_id = 8
    ingredient.display_name = "Chicken"
    ingredient.quantity = 100
    ingredient.unit = "g"
    ingredient.food_reference = second_reference
    row.ingredients.append(ingredient)
    row.steps = []
    session = _AsyncSession([_Result(one=row)])

    meal = await AsyncCatalogMealRepository(session).get_meal_detail("catalog-1")

    assert meal is not None
    assert meal.nutrition_micros is None


@pytest.mark.asyncio
async def test_get_meal_detail_prefers_normalized_nutrient_row_over_legacy_alias():
    row = _meal_row()
    reference = row.ingredients[0].food_reference
    reference.extra_nutrients = {
        "iron_mg": {
            "amount": 8.0,
            "unit": "mg",
            "_normalized_row": True,
        }
    }
    reference.nutrient_rows = [MagicMock(nutrient_key="iron", amount=2.0, unit="mg")]
    row.steps = []
    session = _AsyncSession([_Result(one=row)])

    meal = await AsyncCatalogMealRepository(session).get_meal_detail("catalog-1")

    assert meal is not None
    assert meal.nutrition_micros is not None
    assert meal.nutrition_micros.iron == pytest.approx(2.0)


@pytest.mark.asyncio
async def test_get_meal_detail_omits_normalized_micro_without_a_unit():
    row = _meal_row()
    row.ingredients[0].food_reference.nutrient_rows = [
        MagicMock(nutrient_key="iron_mg", amount=2.0, unit=None)
    ]
    row.steps = []
    session = _AsyncSession([_Result(one=row)])

    meal = await AsyncCatalogMealRepository(session).get_meal_detail("catalog-1")

    assert meal is not None
    assert meal.nutrition_micros is None


@pytest.mark.asyncio
async def test_get_meal_detail_keeps_legacy_scalar_when_normalized_row_has_no_unit():
    row = _meal_row()
    reference = row.ingredients[0].food_reference
    reference.extra_nutrients = {"iron_mg": 2.0}
    reference.nutrient_rows = [
        MagicMock(nutrient_key="iron_mg", amount=99.0, unit=None)
    ]
    row.steps = []
    session = _AsyncSession([_Result(one=row)])

    meal = await AsyncCatalogMealRepository(session).get_meal_detail("catalog-1")

    assert meal is not None
    assert meal.nutrition_micros is not None
    assert meal.nutrition_micros.iron == pytest.approx(2.0)


@pytest.mark.asyncio
async def test_list_active_meals_filters_by_cuisine_and_meal_type():
    session = _AsyncSession([_Result(rows=[_meal_row()])])
    repo = AsyncCatalogMealRepository(session)

    result = await repo.list_active_meals(
        cuisine="vietnamese",
        meal_type="breakfast",
    )

    assert len(result) == 1
    assert result[0].catalog_key == "vn-rice"
    assert result[0].meal_types == ("breakfast",)
    assert result[0].ingredients[0].food_reference_id == 7
    statement = str(session.statement)
    assert "meal_catalog.is_active" in statement
    assert "meal_catalog.cuisine" in statement
    assert "meal_catalog.breakfast_eligible" in statement


@pytest.mark.asyncio
async def test_list_popular_page_orders_by_rank_and_skips_snapshot_scan():
    session = _AsyncSession([_Result(one=(True, 1, 0)), _Result(rows=[_meal_row()])])
    repo = AsyncCatalogMealRepository(session)

    page = await repo.list_popular_page(
        limit=20,
        offset=0,
        query="rice",
        cuisine="vietnamese",
        meal_type="breakfast",
    )

    assert page.total == 1
    assert page.any_ranked is True
    assert page.unranked_count == 0
    assert page.items[0].id == "catalog-1"
    statement = str(session.statement)
    assert "meal_catalog.popularity_rank" in statement
    assert "LIMIT" in statement.upper() or "limit" in statement


@pytest.mark.asyncio
async def test_list_popular_page_orders_by_shuffle_seed_when_provided():
    session = _AsyncSession([_Result(one=(True, 1, 0)), _Result(rows=[_meal_row()])])
    repo = AsyncCatalogMealRepository(session)

    page = await repo.list_popular_page(
        limit=20,
        offset=0,
        shuffle_seed="refresh-a",
    )

    assert page.items[0].id == "catalog-1"
    statement = str(session.statement)
    assert "md5" in statement.lower()


@pytest.mark.asyncio
async def test_get_meal_scopes_to_active_catalog_row():
    session = _AsyncSession([_Result(one=_meal_row())])
    repo = AsyncCatalogMealRepository(session)

    result = await repo.get_meal("catalog-1")

    assert result is not None
    assert result.id == "catalog-1"
    assert result.calories > 0
    statement = str(session.statement)
    assert "meal_catalog.id" in statement
    assert "meal_catalog.is_active" in statement


@pytest.mark.asyncio
async def test_get_meal_mapped_zero_macro_ingredients_do_not_use_payload_fallback():
    row = _meal_row()
    ref = row.ingredients[0].food_reference
    ref.protein_100g = 0.0
    ref.carbs_100g = 0.0
    ref.fat_100g = 0.0
    ref.fiber_100g = 0.0
    ref.sugar_100g = 0.0
    row.recipe_payload = {
        "nutrition": {
            "calories": 250.0,
            "protein": 15.0,
            "carbs": 30.0,
            "fat": 5.0,
        }
    }
    session = _AsyncSession([_Result(one=row)])
    repo = AsyncCatalogMealRepository(session)

    result = await repo.get_meal("catalog-1")

    assert result is not None
    assert result.protein_g == 0
    assert result.carbs_g == 0
    assert result.fat_g == 0
    assert result.calories == 0


@pytest.mark.asyncio
async def test_get_meal_unmapped_ingredients_uses_payload_fallback():
    row = _meal_row()
    row.ingredients[0].food_reference_id = None
    row.ingredients[0].food_reference = None
    row.recipe_payload = {
        "nutrition": {
            "calories": 250.0,
            "protein": 15.0,
            "carbs": 30.0,
            "fat": 5.0,
            "fiber": 2.0,
            "sugar": 1.0,
        }
    }
    session = _AsyncSession([_Result(one=row)])
    repo = AsyncCatalogMealRepository(session)

    result = await repo.get_meal("catalog-1")

    assert result is not None
    assert result.protein_g == 15
    assert result.carbs_g == 30
    assert result.fat_g == 5
    assert result.fiber_g == 2
    assert result.sugar_g == 1
    assert result.calories == 221


@pytest.mark.asyncio
async def test_get_meal_mixed_mapped_and_unmapped_ingredients_uses_payload_fallback():
    row = _meal_row()
    # row already has mapped ingredient at index 0 (Rice, 100g, 2.7g P, 28g C, 0.3g F)
    unmapped_ingredient = MagicMock()
    unmapped_ingredient.food_reference_id = None
    unmapped_ingredient.display_name = "Special Sauce"
    unmapped_ingredient.quantity = 50
    unmapped_ingredient.unit = "g"
    unmapped_ingredient.food_reference = None
    row.ingredients.append(unmapped_ingredient)

    row.recipe_payload = {
        "nutrition": {
            "calories": 300.0,
            "protein": 15.0,
            "carbs": 35.0,
            "fat": 8.0,
            "fiber": 3.0,
            "sugar": 2.0,
        }
    }
    session = _AsyncSession([_Result(one=row)])
    repo = AsyncCatalogMealRepository(session)

    result = await repo.get_meal("catalog-1")

    assert result is not None
    assert result.protein_g == 15
    assert result.carbs_g == 35
    assert result.fat_g == 8
    assert result.fiber_g == 3
    assert result.sugar_g == 2
    assert result.calories == 266


@pytest.mark.asyncio
async def test_get_meal_mixed_ingredients_without_payload_nutrition_falls_back_to_mapped_totals():
    from decimal import Decimal

    row = _meal_row()
    unmapped_ingredient = MagicMock()
    unmapped_ingredient.food_reference_id = None
    unmapped_ingredient.display_name = "Special Sauce"
    unmapped_ingredient.quantity = 50
    unmapped_ingredient.unit = "g"
    unmapped_ingredient.food_reference = None
    row.ingredients.append(unmapped_ingredient)
    row.recipe_payload = None

    session = _AsyncSession([_Result(one=row)])
    repo = AsyncCatalogMealRepository(session)

    result = await repo.get_meal("catalog-1")

    assert result is not None
    assert result.protein_g == Decimal("2.7")
    assert result.carbs_g == Decimal("28.0")
    assert result.fat_g == Decimal("0.3")
