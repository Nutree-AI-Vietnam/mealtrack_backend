"""Lock ordering and in-place food-item sync for meal nutrition writes."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call

import pytest

from src.domain.model.nutrition import FoodItem, Nutrition
from src.domain.model.nutrition.macros import Macros
from src.infra.database.models.meal.meal import MealORM
from src.infra.database.models.nutrition.nutrition import NutritionORM
from src.infra.repositories.meal_repository_async import AsyncMealRepository


def _macros() -> Macros:
    return Macros(protein=1.0, carbs=2.0, fat=3.0, fiber=0.0, sugar=0.0)


def _food_item(item_id: str, name: str = "Egg") -> FoodItem:
    return FoodItem(
        id=item_id,
        name=name,
        quantity=100,
        unit="g",
        macros=_macros(),
    )


def _domain_nutrition(food_items: list[FoodItem] | None = None) -> Nutrition:
    return Nutrition(
        macros=_macros(),
        food_items=food_items or [],
        confidence_score=1.0,
    )


def _db_item(item_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=item_id,
        name="old",
        quantity=1,
        unit="g",
        order_index=99,
        is_deleted=True,
        nutrition_id=None,
    )


def _db_nutrition(food_items: list) -> SimpleNamespace:
    return SimpleNamespace(
        id=42,
        food_items=food_items,
        protein=0,
        carbs=0,
        fat=0,
        fiber=0,
        sugar=0,
        micros=None,
        confidence_score=None,
        nutrition_override=None,
    )


def _repo() -> AsyncMealRepository:
    repo = AsyncMealRepository(session=MagicMock())
    repo.session.get = AsyncMock()
    repo.session.delete = AsyncMock()
    repo.session.flush = AsyncMock()
    return repo


@pytest.mark.asyncio
async def test_lock_meal_row_uses_single_table_for_update():
    repo = AsyncMealRepository(session=MagicMock())
    result = MagicMock()
    result.scalar_one_or_none.return_value = "meal-1"
    repo.session.execute = AsyncMock(return_value=result)

    locked = await repo._lock_meal_row("meal-1")

    assert locked == "meal-1"
    stmt = repo.session.execute.await_args.args[0]
    assert stmt._for_update_arg is not None
    assert list(stmt.columns_clause_froms)[0].name == MealORM.__table__.name


@pytest.mark.asyncio
async def test_update_nutrition_locks_parent_then_deletes_removed_items_by_id():
    repo = _repo()
    item_b = _db_item("b-id")
    item_a = _db_item("a-id")
    db_nutrition = _db_nutrition([item_b, item_a])

    await repo._update_nutrition(db_nutrition, _domain_nutrition())

    repo.session.get.assert_awaited_once_with(NutritionORM, 42, with_for_update=True)
    assert repo.session.delete.await_args_list == [call(item_a), call(item_b)]
    repo.session.add.assert_not_called()


@pytest.mark.asyncio
async def test_update_nutrition_updates_matching_food_items_in_place():
    repo = _repo()
    existing = _db_item("keep-id")
    db_nutrition = _db_nutrition([existing])
    desired = _food_item("keep-id", name="Egg")

    await repo._update_nutrition(db_nutrition, _domain_nutrition([desired]))

    repo.session.delete.assert_not_awaited()
    repo.session.add.assert_not_called()
    assert existing.name == "Egg"
    assert existing.quantity == 100
    assert existing.order_index == 0
    assert existing.is_deleted is False
    assert existing.id == "keep-id"


@pytest.mark.asyncio
async def test_update_nutrition_inserts_only_new_food_items():
    repo = _repo()
    existing = _db_item("keep-id")
    db_nutrition = _db_nutrition([existing])

    await repo._update_nutrition(
        db_nutrition,
        _domain_nutrition([_food_item("keep-id"), _food_item("new-id", name="Rice")]),
    )

    repo.session.delete.assert_not_awaited()
    repo.session.add.assert_called_once()
    added = repo.session.add.call_args.args[0]
    assert added.id == "new-id"
    assert added.name == "Rice"
    assert added.order_index == 1
    assert existing.order_index == 0
