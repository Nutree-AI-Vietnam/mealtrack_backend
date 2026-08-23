from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.app.commands.meal.create_manual_meal_command import (
    CustomNutrition,
    ManualMealItem,
)
from src.app.services.manual_meal_nutrition_resolver import (
    ManualMealNutritionResolver,
)
from src.domain.services.nutrition_calculation_service import (
    NutritionCalculationService,
)
from src.domain.services.nutrition_integrity_policy import NutritionIntegrityError


def _reference(**overrides):
    values = {
        "id": 42,
        "name": "Rice",
        "source": "food_reference",
        "is_verified": True,
        "protein_100g": 2.7,
        "carbs_100g": 28.0,
        "fat_100g": 0.3,
        "fiber_100g": 0.4,
        "sugar_100g": 0.1,
        "servings": [SimpleNamespace(name="cup", grams=158, milliliters=None)],
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class _References:
    async def get_nutrition_projection(self, food_reference_id):
        assert food_reference_id == 42
        return _reference()


@pytest.mark.asyncio
async def test_local_reference_ignores_client_nutrition_and_units():
    item = ManualMealItem(
        name="Rice",
        quantity=100,
        unit="g",
        origin="local",
        food_reference_id=42,
        custom_nutrition=CustomNutrition(
            calories_per_100g=900,
            protein_per_100g=100,
            carbs_per_100g=0,
            fat_per_100g=0,
        ),
        serving_options=[{"unit": "evil", "gram_weight": 99999}],
    )

    resolved = await ManualMealNutritionResolver().resolve_items(
        [item], _References(), contract_version=2
    )

    assert resolved[0].custom_nutrition.protein_per_100g == pytest.approx(2.7)
    assert resolved[0].serving_options[0]["unit"] == "g"
    assert resolved[0].source_kind == "local"
    assert resolved[0].source_snapshot["calories_per_100g"] == pytest.approx(124.7)


@pytest.mark.asyncio
async def test_local_reference_preserves_unknown_unit_text():
    resolved = await ManualMealNutritionResolver().resolve_items(
        [
            ManualMealItem(
                name="Rice",
                quantity=100,
                unit="bowl",
                origin="local",
                food_reference_id=42,
            )
        ],
        _References(),
        contract_version=2,
    )

    assert resolved[0].quantity == pytest.approx(100.0)
    assert resolved[0].unit == "bowl"
    nutrition, _ = NutritionCalculationService().aggregate_from_command_items(resolved)
    assert nutrition.macros.protein == pytest.approx(426.6)


@pytest.mark.asyncio
async def test_local_reference_preserves_known_global_unit_text():
    resolved = await ManualMealNutritionResolver().resolve_items(
        [
            ManualMealItem(
                name="Rice",
                quantity=2,
                unit="oz",
                origin="local",
                food_reference_id=42,
            )
        ],
        _References(),
        contract_version=2,
    )

    assert resolved[0].quantity == pytest.approx(2)
    assert resolved[0].unit == "oz"


@pytest.mark.asyncio
async def test_unverified_reference_fails_closed():
    class _Unverified(_References):
        async def get_nutrition_projection(self, food_reference_id):
            return _reference(is_verified=False)

    with pytest.raises(NutritionIntegrityError, match="unverified_reference"):
        await ManualMealNutritionResolver().resolve_items(
            [
                ManualMealItem(
                    name="Rice",
                    quantity=100,
                    unit="g",
                    origin="local",
                    food_reference_id=42,
                )
            ],
            _Unverified(),
            contract_version=2,
        )


@pytest.mark.asyncio
async def test_provider_resolution_requires_shared_budget():
    provider = SimpleNamespace(
        get_food_details=AsyncMock(
            return_value={
                "food_id": "provider-1",
                "food_name": "Rice",
                "protein_100g": 2.7,
                "carbs_100g": 28.0,
                "fat_100g": 0.3,
                "calories_100g": 124.7,
            }
        )
    )
    with pytest.raises(NutritionIntegrityError, match="provider_budget_unavailable"):
        await ManualMealNutritionResolver(provider=provider).resolve_items(
            [
                ManualMealItem(
                    name="Rice",
                    quantity=100,
                    unit="g",
                    origin="provider",
                    source_namespace="fatsecret",
                    source_food_id="provider-1",
                )
            ],
            _References(),
            contract_version=2,
        )
    provider.get_food_details.assert_not_awaited()


@pytest.mark.asyncio
async def test_provider_resolution_uses_shared_budget_and_deadline():
    provider = SimpleNamespace(
        get_food_details=AsyncMock(
            return_value={
                "food_id": "provider-1",
                "food_name": "Rice",
                "protein_100g": 2.7,
                "carbs_100g": 28.0,
                "fat_100g": 0.3,
                "calories_100g": 124.7,
            }
        )
    )

    class _Budget:
        async def acquire(self, namespace, limit):
            assert namespace == "fatsecret"
            assert limit == 10
            return True

    resolved = await ManualMealNutritionResolver(
        provider=provider,
        provider_budget=_Budget(),
        provider_rpm=10,
        provider_timeout_seconds=1,
    ).resolve_items(
        [
            ManualMealItem(
                name="Rice",
                quantity=100,
                unit="g",
                origin="provider",
                source_namespace="fatsecret",
                source_food_id="provider-1",
            )
        ],
        _References(),
        contract_version=2,
    )

    assert resolved[0].source_snapshot["source_food_id"] == "provider-1"
    provider.get_food_details.assert_awaited_once_with("provider-1")


@pytest.mark.asyncio
async def test_provider_unit_text_is_preserved(caplog):
    raw_unit = "medium private-text"
    provider = SimpleNamespace(
        get_food_details=AsyncMock(
            return_value={
                "food_id": "provider-1",
                "food_name": "Potato",
                "protein_100g": 2.0,
                "carbs_100g": 17.0,
                "fat_100g": 0.1,
                "calories_100g": 77.0,
                "serving_options": [
                    {
                        "unit": "medium",
                        "gram_weight": 173.0,
                        "description": "1 medium potato",
                    }
                ],
            }
        )
    )

    class _Budget:
        async def acquire(self, namespace, limit):
            return True

    resolved = await ManualMealNutritionResolver(
        provider=provider,
        provider_budget=_Budget(),
        provider_rpm=10,
    ).resolve_items(
        [
            ManualMealItem(
                name="Potato",
                quantity=100,
                unit=raw_unit,
                origin="provider",
                source_namespace="fatsecret",
                source_food_id="provider-1",
            )
        ],
        _References(),
        contract_version=2,
    )

    assert resolved[0].quantity == pytest.approx(100)
    assert resolved[0].unit == raw_unit
    nutrition, _ = NutritionCalculationService().aggregate_from_command_items(resolved)
    assert nutrition.calories == pytest.approx(13303.7)
    assert raw_unit not in caplog.text


class _FoodReferencesAdopt:
    """Fake ``uow.food_references`` exposing only ``adopt_provider_food``."""

    def __init__(self, response=None, exc=None):
        self.response = response
        self.exc = exc
        self.calls = []

    async def adopt_provider_food(
        self,
        namespace,
        food_id,
        english_name,
        per_100g,
        servings,
        locale,
        locale_name,
    ):
        self.calls.append(
            {
                "namespace": namespace,
                "food_id": food_id,
                "english_name": english_name,
                "per_100g": per_100g,
                "servings": servings,
                "locale": locale,
                "locale_name": locale_name,
            }
        )
        if self.exc:
            raise self.exc
        return self.response


class _AdoptUow:
    def __init__(self, food_references):
        self.food_references = food_references

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _adopt_uow_factory(food_references):
    def factory():
        return _AdoptUow(food_references)

    return factory


@pytest.mark.asyncio
async def test_provider_resolution_adopts_identity_and_sets_food_reference_id():
    """Save must never search FatSecret by name — only adopt the given identity."""
    provider = SimpleNamespace(
        get_food_details=AsyncMock(
            return_value={
                "food_id": "provider-1",
                "food_name": "Rice",
                "protein_100g": 2.7,
                "carbs_100g": 28.0,
                "fat_100g": 0.3,
                "calories_100g": 124.7,
            }
        )
    )

    class _Budget:
        async def acquire(self, namespace, limit):
            return True

    adopt_repo = _FoodReferencesAdopt(
        response={
            "id": 99,
            "name": "Rice",
            "protein_100g": 2.7,
            "carbs_100g": 28.0,
            "fat_100g": 0.3,
            "fiber_100g": 0.0,
            "sugar_100g": 0.0,
            "serving_options": [{"unit": "g", "gram_weight": 1.0, "description": "1 g"}],
        }
    )

    resolved = await ManualMealNutritionResolver(
        provider=provider,
        provider_budget=_Budget(),
        provider_rpm=10,
        uow_factory=_adopt_uow_factory(adopt_repo),
    ).resolve_items(
        [
            ManualMealItem(
                name="Cơm",
                quantity=100,
                unit="g",
                origin="provider",
                source_namespace="fatsecret",
                source_food_id="provider-1",
            )
        ],
        _References(),
        contract_version=2,
    )

    assert resolved[0].food_reference_id == 99
    assert resolved[0].source_snapshot["canonical_name"] == "Rice"
    assert len(adopt_repo.calls) == 1
    assert adopt_repo.calls[0]["namespace"] == "fatsecret"
    assert adopt_repo.calls[0]["food_id"] == "provider-1"
    provider.get_food_details.assert_awaited_once_with("provider-1")
    assert not hasattr(provider, "search_foods")


@pytest.mark.asyncio
async def test_provider_resolution_prefers_adopted_catalog_density():
    """A frozen/verified catalog row wins over a fresh provider re-fetch."""
    provider = SimpleNamespace(
        get_food_details=AsyncMock(
            return_value={
                "food_id": "provider-1",
                "food_name": "Rice",
                "protein_100g": 50.0,
                "carbs_100g": 28.0,
                "fat_100g": 0.3,
                "calories_100g": 314.7,
            }
        )
    )

    class _Budget:
        async def acquire(self, namespace, limit):
            return True

    adopt_repo = _FoodReferencesAdopt(
        response={
            "id": 7,
            "name": "Rice",
            "protein_100g": 2.7,
            "carbs_100g": 28.0,
            "fat_100g": 0.3,
            "fiber_100g": 0.4,
            "sugar_100g": 0.1,
            "serving_options": [{"unit": "g", "gram_weight": 1.0, "description": "1 g"}],
        }
    )

    resolved = await ManualMealNutritionResolver(
        provider=provider,
        provider_budget=_Budget(),
        provider_rpm=10,
        uow_factory=_adopt_uow_factory(adopt_repo),
    ).resolve_items(
        [
            ManualMealItem(
                name="Cơm",
                quantity=100,
                unit="g",
                origin="provider",
                source_namespace="fatsecret",
                source_food_id="provider-1",
            )
        ],
        _References(),
        contract_version=2,
    )

    assert resolved[0].custom_nutrition.protein_per_100g == pytest.approx(2.7)
    assert resolved[0].food_reference_id == 7


@pytest.mark.asyncio
async def test_provider_resolution_adopt_failure_falls_back_to_provider_density():
    """A catalog write failure must not block an otherwise-valid save."""
    provider = SimpleNamespace(
        get_food_details=AsyncMock(
            return_value={
                "food_id": "provider-1",
                "food_name": "Rice",
                "protein_100g": 2.7,
                "carbs_100g": 28.0,
                "fat_100g": 0.3,
                "calories_100g": 124.7,
            }
        )
    )

    class _Budget:
        async def acquire(self, namespace, limit):
            return True

    adopt_repo = _FoodReferencesAdopt(exc=RuntimeError("db unavailable"))

    resolved = await ManualMealNutritionResolver(
        provider=provider,
        provider_budget=_Budget(),
        provider_rpm=10,
        uow_factory=_adopt_uow_factory(adopt_repo),
    ).resolve_items(
        [
            ManualMealItem(
                name="Cơm",
                quantity=100,
                unit="g",
                origin="provider",
                source_namespace="fatsecret",
                source_food_id="provider-1",
            )
        ],
        _References(),
        contract_version=2,
    )

    assert resolved[0].food_reference_id is None
    assert resolved[0].custom_nutrition.protein_per_100g == pytest.approx(2.7)


@pytest.mark.asyncio
async def test_custom_parse_text_unit_is_not_canonicalized_to_grams():
    resolved = await ManualMealNutritionResolver().resolve_items(
        [
            ManualMealItem(
                name="Sườn Nướng",
                quantity=1,
                unit="Miếng",
                origin="custom",
                custom_nutrition=CustomNutrition(
                    calories_per_100g=187.0,
                    protein_per_100g=18.0,
                    carbs_per_100g=4.0,
                    fat_per_100g=11.0,
                ),
            )
        ],
        _References(),
        contract_version=2,
    )

    assert resolved[0].quantity == pytest.approx(1.0)
    assert resolved[0].unit == "Miếng"
    assert resolved[0].serving_options == [{"unit": "g", "gram_weight": 1.0, "description": "1 g"}]
    nutrition, _ = NutritionCalculationService().aggregate_from_command_items(resolved)
    assert nutrition.macros.protein == pytest.approx(18.0)
