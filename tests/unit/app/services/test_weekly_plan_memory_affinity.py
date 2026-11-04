"""The planner's memory reads real ingredient history, not a stand-in.

This path broke in production and no unit test noticed, because every fake
repository here returned an empty history: the branch that reads it was never
entered. These tests hand it real buckets and let the real affinity service
run, which is the only way the wrong method call shows up.
"""

from datetime import date

import pytest

from src.app.services.weekly_meal_plan_service import _user_food_memory
from src.domain.services.meal_recommendation.ingredient_affinity_service import (
    IngredientHistoryBucket,
)


class _MealsRepo:
    """Answers the two reads the memory makes; history is not empty."""

    def __init__(self, buckets):
        self._buckets = buckets
        self.windows = []

    async def aggregate_linked_ingredient_history(self, **kwargs):
        self.windows.append(kwargs)
        return self._buckets

    async def recent_logged_meal_shapes(self, **kwargs):
        return []


class _Uow:
    def __init__(self, meals):
        self.meals = meals


@pytest.mark.asyncio
async def test_a_user_with_ingredient_history_gets_weights():
    repo = _MealsRepo(
        [
            IngredientHistoryBucket(
                food_reference_id=11, capped_grams=800.0, age_days=3
            ),
            IngredientHistoryBucket(
                food_reference_id=12, capped_grams=200.0, age_days=40
            ),
        ]
    )

    memory = await _user_food_memory(
        _Uow(repo),
        user_id="u1",
        week_start_date=date(2026, 11, 2),
        timezone="Asia/Ho_Chi_Minh",
    )

    assert memory.liked_ingredients
    # The fresher, larger amount outweighs the older, smaller one.
    assert memory.liked_ingredients[11] > memory.liked_ingredients[12]
    assert repo.windows and repo.windows[0]["user_id"] == "u1"


@pytest.mark.asyncio
async def test_history_outside_the_window_leaves_no_weights():
    repo = _MealsRepo(
        [
            IngredientHistoryBucket(
                food_reference_id=11, capped_grams=800.0, age_days=400
            )
        ]
    )

    memory = await _user_food_memory(
        _Uow(repo),
        user_id="u1",
        week_start_date=date(2026, 11, 2),
        timezone=None,
    )

    assert not memory.liked_ingredients


@pytest.mark.asyncio
async def test_a_repository_that_cannot_answer_is_not_a_blocker():
    class _Broken(_MealsRepo):
        async def aggregate_linked_ingredient_history(self, **kwargs):
            raise RuntimeError("history is unavailable")

    memory = await _user_food_memory(
        _Uow(_Broken([])),
        user_id="u1",
        week_start_date=date(2026, 11, 2),
        timezone=None,
    )

    assert not memory.liked_ingredients
