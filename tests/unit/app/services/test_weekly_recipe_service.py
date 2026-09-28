from decimal import Decimal

import pytest

from src.app.services.weekly_recipe_service import WeeklyRecipeService
from src.domain.model.meal_recommendation import CatalogMeal


def _meal(recipe_id: str, name: str) -> CatalogMeal:
    return CatalogMeal(
        id=recipe_id,
        catalog_key=recipe_id,
        content_hash="a" * 64,
        name=name,
        cuisine="vietnamese",
        description=None,
        image_url=None,
        protein_g=Decimal("10"),
        carbs_g=Decimal("20"),
        fat_g=Decimal("3"),
        fiber_g=Decimal("2"),
        meal_types=("dinner",),
    )


class _Catalog:
    async def list_active_meals(self, **kwargs):
        assert kwargs["meal_type"] == "dinner"
        return (
            _meal("dinner", "Grilled tofu"),
            _meal("dessert", "Chocolate tofu pudding"),
            _meal("remedy", "Herbal cough remedy"),
        )


class _UnitOfWork:
    catalog_recipes = _Catalog()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None


@pytest.mark.asyncio
async def test_dinner_browse_excludes_desserts_and_remedies():
    page = await WeeklyRecipeService(_UnitOfWork).list(meal_type="dinner")

    assert [meal.name for meal in page.items] == ["Grilled tofu"]
    assert page.total == 1
