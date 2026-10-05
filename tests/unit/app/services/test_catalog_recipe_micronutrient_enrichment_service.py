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
    def __init__(self, meal, overlay=None):
        self.meal = meal
        self.overlay = overlay

    async def get_meal_detail(self, recipe_id):
        return self.meal

    async def get_overlay(self, catalog_meal_id):
        return self.overlay


class _FoodReferenceRepository:
    def __init__(self, references, catalog_repository):
        self.references = references

    async def get_by_ids(self, ids):
        return [row for row in self.references if row["id"] in ids]


class _UnitOfWork:
    def __init__(self, catalog_repository, food_reference_repository):
        self.catalog_recipes = catalog_repository
        self.catalog_preparation = catalog_repository
        self.food_references = food_reference_repository

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


def _uow_factory(catalog_repository, food_reference_repository):
    return lambda: _UnitOfWork(catalog_repository, food_reference_repository)


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
async def test_recipe_detail_overlays_prepared_micronutrients():
    catalog = _CatalogRepository(
        _meal(),
        overlay={
            "micros": _complete_estimate(iron=2.5),
            "sources": dict.fromkeys(MICRO_FIELDS, "ai_estimate"),
        },
    )

    result = await WeeklyRecipeService(
        _uow_factory(catalog, _FoodReferenceRepository([], catalog))
    ).detail("catalog-1")

    assert result is not None
    assert result.nutrition_micros_enrichment_loaded is True
    assert result.nutrition_micros.to_dict()["iron"] == 2.5


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
