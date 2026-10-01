from decimal import Decimal

import pytest

from src.app.services.weekly_recipe_service import WeeklyRecipeService
from src.domain.model.ai.nutrition_contracts import AIRecipeMicronutrientEstimate
from src.domain.model.meal_recommendation import CatalogMeal, CatalogMealIngredient
from src.domain.model.nutrition.extra_nutrients import extra_nutrients_to_micros
from src.domain.model.nutrition.micros import Micros

MICRO_FIELDS = tuple(Micros.__dataclass_fields__)


def _complete_estimate(**overrides):
    return {**dict.fromkeys(MICRO_FIELDS, 1.0), **overrides}


def _meal(*, ingredient=None, micros=None, base_servings=4):
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
        base_servings=base_servings,
        nutrition_micros=micros,
    )


class _CatalogRepository:
    def __init__(self, meal, refreshed_meal=None):
        self.meal = meal
        self.refreshed_meal = refreshed_meal or meal
        self.cached = None
        self.claims = 0
        self.failed_claims = 0

    async def get_meal_detail(self, recipe_id):
        return self.meal

    async def get_meals(self, recipe_ids):
        return [self.meal] if self.meal.id in recipe_ids else []

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
        self.failed_claims += 1


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
async def test_plan_enrichment_estimates_missing_reference_micros_once_and_detail_reuses_cache():
    catalog = _CatalogRepository(_meal())
    food_references = _FoodReferenceRepository([], catalog)
    estimate_calls = []

    async def estimate(meal, missing_fields, known_micros):
        estimate_calls.append((missing_fields, known_micros))
        return _complete_estimate(iron=2.5, vitamin_c=8)

    uow_factory = _uow_factory(catalog, food_references)
    service = WeeklyRecipeService(uow_factory, micronutrient_estimator=estimate)

    ready = await service.micronutrient_enrichment.enrich_recipe_ids(["catalog-1"])
    first = await service.detail("catalog-1")
    second = await service.detail("catalog-1")

    assert ready is True
    assert first is not None and second is not None
    assert first.nutrition_micros.to_dict() == _complete_estimate(vitamin_c=8, iron=2.5)
    assert set(first.nutrition_micros_sources.values()) == {"ai_estimate"}
    assert first.nutrition_micros_estimated is True
    assert first.nutrition_micros_enrichment_loaded is True
    assert second.nutrition_micros.to_dict() == first.nutrition_micros.to_dict()
    assert len(estimate_calls) == 1
    assert estimate_calls[0][1] == {}
    assert len(estimate_calls[0][0]) == 21
    assert catalog.claims == 1


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
        return _complete_estimate(vitamin_c=999, iron=3)

    service = WeeklyRecipeService(
        _uow_factory(catalog, food_references),
        micronutrient_estimator=estimate,
        fdc_micronutrient_loader=load_fdc,
    )

    ready = await service.micronutrient_enrichment.enrich_recipe_ids(["catalog-1"])
    result = await service.detail("catalog-1")

    assert ready is True
    assert result is not None
    assert result.nutrition_micros.to_dict() == _complete_estimate(iron=3, vitamin_c=12)
    assert result.nutrition_micros_sources["vitamin_c"] == "usda_fdc"
    assert set(result.nutrition_micros_sources.values()) == {
        "ai_estimate",
        "usda_fdc",
    }
    assert result.nutrition_micros_estimated is True
    assert result.nutrition_micros_enrichment_loaded is True
    assert food_references.updated[0][:2] == (7, 12345)
    assert estimate_calls[0][1] == {"vitamin_c": 12}
    assert "vitamin_c" not in estimate_calls[0][0]


@pytest.mark.asyncio
async def test_incomplete_ai_estimate_does_not_mark_recipe_ready():
    catalog = _CatalogRepository(_meal())
    food_references = _FoodReferenceRepository([], catalog)

    async def estimate(meal, missing_fields, known_micros):
        return {}

    service = WeeklyRecipeService(
        _uow_factory(catalog, food_references), micronutrient_estimator=estimate
    )

    ready = await service.micronutrient_enrichment.enrich_recipe_ids(["catalog-1"])

    assert ready is False
    assert catalog.cached is None
    assert catalog.failed_claims == 1


def test_estimate_prompt_uses_whole_recipe_basis_and_requires_all_fields():
    from src.app.services.catalog_recipe_micronutrient_enrichment_service import (
        build_micronutrient_estimate_prompt,
    )

    prompt = build_micronutrient_estimate_prompt(_meal(), MICRO_FIELDS, {})

    assert "Ingredient quantities below are for the entire recipe" in prompt
    assert "do not divide by base servings" in prompt
    assert "never omit a field or return null" in prompt


def test_estimate_schema_requires_every_micronutrient_field():
    assert set(AIRecipeMicronutrientEstimate.model_json_schema()["required"]) == set(
        MICRO_FIELDS
    )


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
        "catalog-1"
    )

    assert result is not None
    assert result.nutrition_micros_sources == {"vitamin_c": "food_reference"}
