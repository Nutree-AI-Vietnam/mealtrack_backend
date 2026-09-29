from decimal import Decimal

import pytest

from src.app.services.weekly_recipe_service import WeeklyRecipeService
from src.domain.model.meal_recommendation import CatalogMeal, CatalogMealIngredient
from src.domain.model.nutrition.extra_nutrients import extra_nutrients_to_micros


def _meal(*, ingredient=None, micros=None):
    return CatalogMeal(
        id="catalog-1",
        catalog_key="sample-recipe",
        content_hash="a" * 64,
        name="Sample recipe",
        cuisine="vietnamese",
        description=None,
        image_url=None,
        protein_g=Decimal("10"),
        carbs_g=Decimal("20"),
        fat_g=Decimal("3"),
        fiber_g=Decimal("2"),
        ingredients=(ingredient,) if ingredient else (),
        nutrition_micros=micros,
    )


class _CatalogRepository:
    def __init__(self, meal, refreshed_meal=None):
        self.meal = meal
        self.refreshed_meal = refreshed_meal or meal
        self.cached = None
        self.claims = 0

    async def get_meal_detail(self, recipe_id):
        return self.meal

    async def get_micronutrient_enrichment(self, *, catalog_meal_id, content_hash):
        return self.cached

    async def claim_micronutrient_enrichment(
        self, *, catalog_meal_id, content_hash, lease_seconds
    ):
        self.claims += 1
        return "claimed", f"claim-{self.claims}"

    async def save_micronutrient_enrichment(
        self, *, catalog_meal_id, content_hash, claim_token, micros, sources
    ):
        self.cached = {"micros": micros, "sources": sources}
        return True

    async def fail_micronutrient_enrichment(
        self, *, catalog_meal_id, content_hash, claim_token, retry_seconds
    ):
        raise AssertionError("successful enrichment must not release its claim")


class _FoodReferenceRepository:
    def __init__(self, references, catalog_repository):
        self.references = references
        self.catalog_repository = catalog_repository
        self.updated = []

    async def get_by_ids(self, ids):
        return [row for row in self.references if row["id"] in ids]

    async def update_usda_micronutrients(
        self, food_reference_id, fdc_id, extra_nutrients
    ):
        self.updated.append((food_reference_id, fdc_id, extra_nutrients))
        for row in self.references:
            if row["fdc_id"] == fdc_id:
                row["extra_nutrients"] = {
                    key: {**value, "source": "usda_fdc"}
                    if isinstance(value, dict)
                    else value
                    for key, value in extra_nutrients.items()
                }
        self.catalog_repository.meal = self.catalog_repository.refreshed_meal
        return True


class _UnitOfWork:
    def __init__(self, catalog_repository, food_reference_repository):
        self.catalog_recipes = catalog_repository
        self.food_references = food_reference_repository

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


def _uow_factory(catalog_repository, food_reference_repository):
    return lambda: _UnitOfWork(catalog_repository, food_reference_repository)


@pytest.mark.asyncio
async def test_recipe_detail_estimates_missing_reference_micros_once_and_reuses_cache():
    catalog = _CatalogRepository(_meal())
    food_references = _FoodReferenceRepository([], catalog)
    estimate_calls = []

    async def estimate(meal, missing_fields, known_micros):
        estimate_calls.append((missing_fields, known_micros))
        return {"iron": 2.5, "vitamin_c": 8}

    service = WeeklyRecipeService(
        _uow_factory(catalog, food_references), micronutrient_estimator=estimate
    )

    first = await service.detail("catalog-1", enrich_micronutrients=True)
    second = await service.detail("catalog-1", enrich_micronutrients=True)

    assert first is not None and second is not None
    assert first.nutrition_micros.to_dict() == {"vitamin_c": 8, "iron": 2.5}
    assert first.nutrition_micros_sources == {
        "vitamin_c": "ai_estimate",
        "iron": "ai_estimate",
    }
    assert first.nutrition_micros_estimated is True
    assert first.nutrition_micros_enrichment_loaded is True
    assert second.nutrition_micros.to_dict() == first.nutrition_micros.to_dict()
    assert len(estimate_calls) == 1
    assert estimate_calls[0][1] == {}
    assert len(estimate_calls[0][0]) == 21


@pytest.mark.asyncio
async def test_linked_usda_micros_are_saved_and_take_precedence_over_ai_estimates():
    ingredient = CatalogMealIngredient(
        food_reference_id=7,
        display_name="Orange",
        quantity=Decimal("100"),
        unit="g",
    )
    catalog = _CatalogRepository(
        _meal(ingredient=ingredient),
        refreshed_meal=_meal(
            ingredient=ingredient,
            micros=extra_nutrients_to_micros(
                {"vitamin_c": {"amount": 12, "unit": "mg"}}, validate_units=True
            ),
        ),
    )
    food_references = _FoodReferenceRepository(
        [{"id": 7, "fdc_id": 12345, "extra_nutrients": {}}], catalog
    )
    estimate_calls = []

    async def load_fdc(fdc_ids):
        assert fdc_ids == [12345]
        return {
            12345: {"vitamin_c": {"amount": 12, "unit": "mg"}},
        }

    async def estimate(meal, missing_fields, known_micros):
        estimate_calls.append((missing_fields, known_micros))
        return {"vitamin_c": 999, "iron": 3}

    service = WeeklyRecipeService(
        _uow_factory(catalog, food_references),
        micronutrient_estimator=estimate,
        fdc_micronutrient_loader=load_fdc,
    )

    result = await service.detail("catalog-1", enrich_micronutrients=True)

    assert result is not None
    assert result.nutrition_micros.to_dict() == {"iron": 3, "vitamin_c": 12}
    assert result.nutrition_micros_sources == {
        "iron": "ai_estimate",
        "vitamin_c": "usda_fdc",
    }
    assert result.nutrition_micros_estimated is True
    assert result.nutrition_micros_enrichment_loaded is True
    assert food_references.updated[0][:2] == (7, 12345)
    assert estimate_calls[0][1] == {"vitamin_c": 12}
    assert "vitamin_c" not in estimate_calls[0][0]


@pytest.mark.asyncio
async def test_recipe_detail_preload_does_not_call_fdc_or_ai():
    catalog = _CatalogRepository(_meal())
    food_references = _FoodReferenceRepository([], catalog)
    calls = []

    async def estimate(*args):
        calls.append("ai")
        return {"iron": 2.5}

    async def load_fdc(*args):
        calls.append("fdc")
        return {}

    service = WeeklyRecipeService(
        _uow_factory(catalog, food_references),
        micronutrient_estimator=estimate,
        fdc_micronutrient_loader=load_fdc,
    )

    result = await service.detail("catalog-1")

    assert result is not None
    assert result.nutrition_micros is None
    assert result.nutrition_micros_enrichment_loaded is False
    assert calls == []


@pytest.mark.asyncio
async def test_fdc_id_alone_does_not_claim_existing_micros_are_usda_sourced():
    ingredient = CatalogMealIngredient(
        food_reference_id=7,
        display_name="Orange",
        quantity=Decimal("100"),
        unit="g",
    )
    catalog = _CatalogRepository(
        _meal(
            ingredient=ingredient,
            micros=extra_nutrients_to_micros(
                {"vitamin_c": {"amount": 12, "unit": "mg"}}, validate_units=True
            ),
        )
    )
    food_references = _FoodReferenceRepository(
        [
            {
                "id": 7,
                "fdc_id": 12345,
                "extra_nutrients": {
                    "vitamin_c": {"amount": 12, "unit": "mg"},
                },
            }
        ],
        catalog,
    )

    result = await WeeklyRecipeService(_uow_factory(catalog, food_references)).detail(
        "catalog-1", enrich_micronutrients=True
    )

    assert result is not None
    assert result.nutrition_micros_sources == {"vitamin_c": "food_reference"}
