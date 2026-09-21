"""Owner-scoped weekly meal plan repository."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.domain.exceptions.weekly_meal_planner_exceptions import (
    WeeklyMealPlanConflictError,
)
from src.domain.model.weekly_meal_planner import (
    WeeklyMealPlan,
    WeeklyMealPlanPreferences,
    WeeklyMealPlanSlot,
    WeeklyMealPlanStatus,
)
from src.domain.ports.weekly_meal_plan_repository_port import (
    WeeklyMealPlanRepositoryPort,
)
from src.infra.database.models.weekly_meal_planner import (
    WeeklyMealPlanORM,
    WeeklyMealPlanPantryItemORM,
    WeeklyMealPlanSlotORM,
)


class AsyncWeeklyMealPlanRepository(WeeklyMealPlanRepositoryPort):
    """SQLAlchemy implementation; callers own transaction commit."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def lock_user_week(self, *, user_id: str, week_start_date: date) -> None:
        await self.session.execute(
            select(WeeklyMealPlanORM.id)
            .where(
                WeeklyMealPlanORM.user_id == user_id,
                WeeklyMealPlanORM.week_start_date == week_start_date,
            )
            .with_for_update()
        )

    async def get_current(
        self, *, user_id: str, week_start_date: date
    ) -> WeeklyMealPlan | None:
        return await self._load(user_id=user_id, week_start_date=week_start_date)

    async def get_by_id(self, *, user_id: str, plan_id: str) -> WeeklyMealPlan | None:
        return await self._load(user_id=user_id, plan_id=plan_id)

    async def get_for_update(
        self, *, user_id: str, plan_id: str
    ) -> WeeklyMealPlanORM | None:
        result = await self.session.execute(
            select(WeeklyMealPlanORM)
            .options(
                selectinload(WeeklyMealPlanORM.slots),
                selectinload(WeeklyMealPlanORM.pantry_items),
            )
            .where(
                WeeklyMealPlanORM.id == plan_id, WeeklyMealPlanORM.user_id == user_id
            )
            .with_for_update()
        )
        row = result.scalar_one_or_none()
        return row

    async def create(
        self,
        *,
        user_id: str,
        week_start_date: date,
        timezone: str,
        preferences: WeeklyMealPlanPreferences,
        status: str = "draft",
        daily_calories: int | None = None,
        catalog_revision: str | None = None,
        recipe_ids: dict[tuple[int, int], str | None] | None = None,
    ) -> WeeklyMealPlan:
        plan = WeeklyMealPlanORM(
            id=str(uuid.uuid4()),
            user_id=user_id,
            week_start_date=week_start_date,
            status=status,
            people=preferences.people,
            preferences=preferences.to_dict(),
            timezone=timezone,
            daily_calories=daily_calories,
            catalog_revision=catalog_revision,
            algorithm_version="v1",
        )
        selected = recipe_ids or {}
        plan.slots = [
            WeeklyMealPlanSlotORM(
                id=str(uuid.uuid4()),
                day_index=day,
                slot_index=slot,
                catalog_meal_id=selected.get((day, slot)),
            )
            for day in range(7)
            for slot in range(2)
        ]
        self.session.add(plan)
        await self.session.flush()
        return _to_domain(plan)

    async def update(
        self,
        *,
        user_id: str,
        plan_id: str,
        people: int | None = None,
        preferences: WeeklyMealPlanPreferences | None = None,
        status: str | None = None,
        slots: dict[tuple[int, int], str | None] | None = None,
    ) -> WeeklyMealPlan:
        row = await self.get_for_update(user_id=user_id, plan_id=plan_id)
        if row is None:
            return None  # type: ignore[return-value]
        row = cast(Any, row)
        if row.status == WeeklyMealPlanStatus.CONFIRMED.value and any(
            value is not None for value in (people, preferences, slots)
        ):
            raise WeeklyMealPlanConflictError()
        if people is not None:
            row.people = people
            if preferences is None:
                preferences = WeeklyMealPlanPreferences.from_payload(
                    cast(dict | None, row.preferences), people=people
                )
        if preferences is not None:
            if people is None and preferences.people != row.people:
                preferences = WeeklyMealPlanPreferences(
                    people=cast(int, row.people),
                    diet=preferences.diet,
                    cooking_time=preferences.cooking_time,
                    cuisine=preferences.cuisine,
                    dislikes=preferences.dislikes,
                    allergies=preferences.allergies,
                )
            row.preferences = preferences.to_dict()
            row.people = preferences.people
        if status is not None:
            if (
                status == WeeklyMealPlanStatus.DRAFT.value
                and row.status == WeeklyMealPlanStatus.CONFIRMED.value
            ):
                raise WeeklyMealPlanConflictError()
            row.status = status
        if slots:
            by_coordinate = {
                (slot.day_index, slot.slot_index): slot for slot in row.slots
            }
            for coordinate, recipe_id in slots.items():
                slot = by_coordinate.get(coordinate)
                if slot is None:
                    raise ValueError("unknown weekly meal slot")
                if slot.is_logged:
                    raise WeeklyMealPlanConflictError(
                        "Logged weekly meal slots cannot be replaced"
                    )
                slot.catalog_meal_id = recipe_id
                slot.version += 1
        await self.session.flush()
        return _to_domain(row)

    async def update_pantry(
        self,
        *,
        user_id: str,
        plan_id: str,
        updates: list[dict],
    ) -> WeeklyMealPlan:
        row = await self.get_for_update(user_id=user_id, plan_id=plan_id)
        if row is None:
            return None  # type: ignore[return-value]
        by_food = {item.food_reference_id: item for item in row.pantry_items}
        for update in updates:
            food_id = int(update["ingredient_id"])
            item = cast(Any, by_food.get(food_id))
            if item is None:
                item = WeeklyMealPlanPantryItemORM(
                    id=str(uuid.uuid4()),
                    plan_id=plan_id,
                    food_reference_id=food_id,
                    custom_amount=update.get("amount"),
                    stock_kind=update["kind"],
                )
                self.session.add(item)
            else:
                item.custom_amount = update.get("amount")
                item.stock_kind = update["kind"]
        await self.session.flush()
        return _to_domain(row)

    async def list_pantry(self, *, user_id: str, plan_id: str) -> list[dict]:
        result = await self.session.execute(
            select(WeeklyMealPlanPantryItemORM)
            .join(WeeklyMealPlanORM)
            .where(
                WeeklyMealPlanPantryItemORM.plan_id == plan_id,
                WeeklyMealPlanORM.user_id == user_id,
            )
        )
        return [
            {
                "food_reference_id": item.food_reference_id,
                "custom_amount": item.custom_amount,
                "stock_kind": item.stock_kind,
            }
            for item in result.scalars().all()
        ]

    async def mark_slot_logged(
        self, *, user_id: str, plan_id: str, slot_id: str, meal_id: str
    ) -> None:
        result = await self.session.execute(
            select(WeeklyMealPlanSlotORM)
            .join(WeeklyMealPlanORM)
            .where(
                WeeklyMealPlanSlotORM.id == slot_id,
                WeeklyMealPlanSlotORM.plan_id == plan_id,
                WeeklyMealPlanORM.user_id == user_id,
            )
            .with_for_update()
        )
        slot = result.scalar_one_or_none()
        if slot is None:
            raise ValueError("weekly meal slot not found")
        slot = cast(Any, slot)
        slot.is_logged = True
        slot.logged_meal_id = meal_id
        slot.version += 1
        await self.session.flush()

    async def get_slot_for_update(self, *, user_id: str, plan_id: str, slot_id: str):
        result = await self.session.execute(
            select(WeeklyMealPlanSlotORM)
            .join(WeeklyMealPlanORM)
            .where(
                WeeklyMealPlanSlotORM.id == slot_id,
                WeeklyMealPlanSlotORM.plan_id == plan_id,
                WeeklyMealPlanORM.user_id == user_id,
            )
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def _load(
        self,
        *,
        user_id: str,
        plan_id: str | None = None,
        week_start_date: date | None = None,
    ):
        stmt = (
            select(WeeklyMealPlanORM)
            .options(
                selectinload(WeeklyMealPlanORM.slots),
                selectinload(WeeklyMealPlanORM.pantry_items),
            )
            .where(WeeklyMealPlanORM.user_id == user_id)
        )
        if plan_id is not None:
            stmt = stmt.where(WeeklyMealPlanORM.id == plan_id)
        if week_start_date is not None:
            stmt = stmt.where(WeeklyMealPlanORM.week_start_date == week_start_date)
        result = await self.session.execute(stmt)
        row = result.scalar_one_or_none()
        return _to_domain(row) if row else None


def _to_domain(row: WeeklyMealPlanORM | None) -> WeeklyMealPlan:
    if row is None:
        raise ValueError("weekly plan row is required")
    data = cast(Any, row)
    preferences = WeeklyMealPlanPreferences.from_payload(
        cast(dict | None, data.preferences), people=cast(int, data.people)
    )
    slots = tuple(
        WeeklyMealPlanSlot(
            id=cast(str, slot.id),
            day_index=cast(int, slot.day_index),
            slot_index=cast(int, slot.slot_index),
            recipe_id=cast(str | None, slot.catalog_meal_id),
            is_logged=cast(bool, slot.is_logged),
            logged_meal_id=cast(str | None, slot.logged_meal_id),
            version=cast(int, slot.version),
        )
        for slot in sorted(
            data.slots, key=lambda item: (item.day_index, item.slot_index)
        )
    )
    return WeeklyMealPlan(
        id=cast(str, data.id),
        user_id=cast(str, data.user_id),
        week_start_date=cast(date, data.week_start_date),
        status=WeeklyMealPlanStatus(cast(str, data.status)),
        people=cast(int, data.people),
        preferences=preferences,
        timezone=cast(str, data.timezone),
        slots=slots,
        daily_calories=cast(int | None, data.daily_calories),
        catalog_revision=cast(str | None, data.catalog_revision),
        algorithm_version=cast(str, data.algorithm_version),
        created_at=cast(datetime | None, data.created_at),
        updated_at=cast(datetime | None, data.updated_at),
    )
