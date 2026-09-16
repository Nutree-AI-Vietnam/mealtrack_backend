"""Postgres save() paths that 500'd when FOR UPDATE hit the mealimage join."""

from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.model.auth import AuthProviderEnum
from src.domain.model.meal import Meal, MealImage, MealStatus
from src.domain.model.nutrition import Nutrition
from src.domain.model.nutrition.macros import Macros
from src.domain.utils.timezone_utils import utc_now
from src.infra.database.models.enums import MealStatusEnum
from src.infra.database.models.meal.meal import MealORM
from src.infra.database.models.meal.meal_image import MealImageORM
from src.infra.database.models.nutrition.food_item import FoodItemORM
from src.infra.database.models.nutrition.nutrition import NutritionORM
from src.infra.database.models.user.user import User
from src.infra.repositories.meal_repository_async import AsyncMealRepository

pytestmark = pytest.mark.integration


def _macros() -> Macros:
    return Macros(protein=10.0, carbs=20.0, fat=5.0, fiber=1.0, sugar=2.0)


async def _seed_user(session: AsyncSession, user_id: str) -> None:
    session.add(
        User(
            id=user_id,
            firebase_uid=f"fb-{user_id}",
            email=f"{user_id}@example.com",
            username=f"u{user_id[:8]}",
            password_hash="x",
            provider=AuthProviderEnum.GOOGLE,
        )
    )
    await session.flush()


async def _seed_meal(
    session: AsyncSession,
    *,
    user_id: str,
    meal_id: str,
    with_image: bool,
) -> str:
    food_item_id = str(uuid4())
    image_id = str(uuid4()) if with_image else None
    if with_image:
        session.add(
            MealImageORM(
                image_id=image_id,
                format="jpeg",
                size_bytes=1,
                url="https://example.com/meal.jpg",
            )
        )
        await session.flush()

    now = utc_now()
    meal = MealORM(
        meal_id=meal_id,
        user_id=user_id,
        status=MealStatusEnum.READY,
        dish_name="Chicken",
        ready_at=now,
        image_id=image_id,
    )
    nutrition = NutritionORM(
        meal_id=meal_id,
        protein=10.0,
        carbs=20.0,
        fat=5.0,
        fiber=1.0,
        sugar=2.0,
        confidence_score=1.0,
    )
    nutrition.food_items = [
        FoodItemORM(
            id=food_item_id,
            name="Chicken",
            quantity=100.0,
            unit="g",
            protein=10.0,
            carbs=0.0,
            fat=5.0,
            fiber=0.0,
            sugar=0.0,
            confidence=1.0,
            order_index=0,
        )
    ]
    meal.nutrition = nutrition
    session.add(meal)
    await session.flush()
    return food_item_id


@pytest.mark.asyncio
async def test_save_updates_meal_that_has_a_joined_image(pg_session: AsyncSession):
    user_id = str(uuid4())
    meal_id = str(uuid4())
    await _seed_user(pg_session, user_id)
    food_item_id = await _seed_meal(
        pg_session, user_id=user_id, meal_id=meal_id, with_image=True
    )

    repo = AsyncMealRepository(pg_session)
    meal = await repo.find_by_id(meal_id)
    assert meal is not None
    assert meal.image is not None

    item = meal.nutrition.food_items[0]
    updated_nutrition = Nutrition(
        macros=meal.nutrition.macros,
        food_items=[replace(item, quantity=150.0)],
        confidence_score=meal.nutrition.confidence_score,
    )
    saved = await repo.save(meal.mark_edited(updated_nutrition, meal.dish_name))

    assert saved.image is not None
    assert saved.image.image_id == meal.image.image_id
    assert saved.nutrition.food_items[0].id == food_item_id
    assert saved.nutrition.food_items[0].quantity == 150.0
    persisted_ids = (
        (
            await pg_session.execute(
                select(FoodItemORM.id).where(FoodItemORM.id == food_item_id)
            )
        )
        .scalars()
        .all()
    )
    assert persisted_ids == [food_item_id]


@pytest.mark.asyncio
async def test_save_updates_meal_with_nullable_image(pg_session: AsyncSession):
    user_id = str(uuid4())
    meal_id = str(uuid4())
    await _seed_user(pg_session, user_id)
    await _seed_meal(pg_session, user_id=user_id, meal_id=meal_id, with_image=False)

    repo = AsyncMealRepository(pg_session)
    meal = await repo.find_by_id(meal_id)
    assert meal is not None
    assert meal.image is None

    saved = await repo.save(meal.mark_edited(meal.nutrition, "Updated bowl"))
    assert saved.dish_name == "Updated bowl"
    assert saved.image is None


@pytest.mark.asyncio
async def test_save_inserts_new_meal_like_hydration_and_scan(pg_session: AsyncSession):
    user_id = str(uuid4())
    await _seed_user(pg_session, user_id)
    now = utc_now()
    meal = Meal(
        meal_id=str(uuid4()),
        user_id=user_id,
        status=MealStatus.READY,
        created_at=now,
        ready_at=now,
        image=MealImage(image_id=str(uuid4()), format="jpeg", size_bytes=1, url=None),
        dish_name="Water",
        meal_type="hydration",
        source="hydration",
        nutrition=Nutrition(macros=_macros(), food_items=None),
    )

    saved = await AsyncMealRepository(pg_session).save(meal)
    assert saved.meal_id == meal.meal_id
    assert saved.dish_name == "Water"
    assert saved.nutrition is not None
