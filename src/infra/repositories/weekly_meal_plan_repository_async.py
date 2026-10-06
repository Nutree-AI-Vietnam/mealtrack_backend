"""Owner-scoped weekly meal plan repository."""

from __future__ import annotations

import hashlib
import uuid
from datetime import date, datetime
from typing import Any, cast

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import contains_eager, noload, selectinload

from src.domain.exceptions.weekly_meal_planner_exceptions import (
    WeeklyMealPlanConflictError,
)
from src.domain.model.weekly_meal_planner import (
    WEEKLY_DAYS,
    WEEKLY_SLOTS_PER_DAY,
    WeeklyMealPlan,
    WeeklyMealPlanPreferences,
    WeeklyMealPlanSlot,
    WeeklyMealPlanStatus,
)
from src.domain.ports.weekly_meal_plan_repository_port import (
    WeeklyMealPlanRepositoryPort,
)
from src.domain.services.weekly_meal_planner.late_legacy_slot_shape import (
    is_late_legacy_two_slot_plan,
)
from src.domain.services.weekly_meal_planner.weekly_plan_generation_service import (
    WeeklyPlanGenerationService,
)
from src.infra.database.models.weekly_meal_planner import (
    WeeklyGroceryDayLineORM,
    WeeklyGroceryItemStateORM,
    WeeklyMealPlanORM,
    WeeklyMealPlanPantryItemORM,
    WeeklyMealPlanSlotORM,
)
from src.planner_observability import planner_timed


class AsyncWeeklyMealPlanRepository(WeeklyMealPlanRepositoryPort):
    """SQLAlchemy implementation; callers own transaction commit."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def lock_user_week(self, *, user_id: str, week_start_date: date) -> None:
        # Existing-row locks cannot serialize simultaneous creation of a missing week.
        if self.session.get_bind().dialect.name == "postgresql":
            digest = hashlib.sha256(
                f"weekly-plan:{user_id}:{week_start_date.isoformat()}".encode()
            ).digest()
            key = int.from_bytes(digest[:8], "big", signed=True)
            await self.session.execute(
                text("SELECT pg_advisory_xact_lock(:key)"), {"key": key}
            )
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

    @planner_timed("lock")
    async def get_for_update(
        self, *, user_id: str, plan_id: str, include_pantry: bool = True
    ) -> WeeklyMealPlanORM | None:
        result = await self.session.execute(
            select(WeeklyMealPlanORM)
            .options(
                selectinload(WeeklyMealPlanORM.slots).raiseload(
                    WeeklyMealPlanSlotORM.catalog_meal
                ),
                selectinload(WeeklyMealPlanORM.pantry_items)
                if include_pantry
                else noload(WeeklyMealPlanORM.pantry_items),
            )
            .where(
                WeeklyMealPlanORM.id == plan_id, WeeklyMealPlanORM.user_id == user_id
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        row = result.scalar_one_or_none()
        if row is not None:
            await self.session.execute(
                select(WeeklyMealPlanSlotORM.id)
                .where(WeeklyMealPlanSlotORM.plan_id == plan_id)
                .order_by(
                    WeeklyMealPlanSlotORM.day_index, WeeklyMealPlanSlotORM.slot_index
                )
                .with_for_update()
            )
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
            algorithm_version=WeeklyPlanGenerationService.algorithm_version,
        )
        selected = recipe_ids or {}
        plan.slots = [
            WeeklyMealPlanSlotORM(
                id=str(uuid.uuid4()),
                day_index=day,
                slot_index=slot,
                catalog_meal_id=selected.get((day, slot)),
            )
            for day in range(WEEKLY_DAYS)
            for slot in range(WEEKLY_SLOTS_PER_DAY)
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
        expected_revision: int | None = None,
        algorithm_version: str | None = None,
    ) -> WeeklyMealPlan:
        row = await self.get_for_update(
            user_id=user_id, plan_id=plan_id, include_pantry=False
        )
        if row is None:
            return None  # type: ignore[return-value]
        row = cast(Any, row)
        # Repair before the revision check. The client loaded this revision
        # against the two-slot grid; dinner is slot 2 only after the shift.
        await self._repair_late_legacy_slots(row)
        if expected_revision is not None and row.revision != expected_revision:
            raise WeeklyMealPlanConflictError(
                "Weekly meal plan changed since it was loaded",
                error_code="WEEKLY_PLAN_STALE_REVISION",
            )
        if row.status == WeeklyMealPlanStatus.CONFIRMED.value and any(
            value is not None for value in (people, preferences)
        ):
            raise WeeklyMealPlanConflictError(
                "Preferences cannot be changed once a weekly meal plan is confirmed"
            )
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
        if algorithm_version is not None:
            row.algorithm_version = algorithm_version
        changed = any(
            value is not None
            for value in (people, preferences, status, algorithm_version)
        ) or bool(slots)
        if changed:
            row.revision += 1
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
        await self._repair_late_legacy_slots(row)
        by_food = {item.food_reference_id: item for item in row.pantry_items}
        state_result = await self.session.execute(
            select(WeeklyGroceryItemStateORM).where(
                WeeklyGroceryItemStateORM.plan_id == plan_id
            )
        )
        by_state = {
            int(cast(Any, item.food_reference_id)): item
            for item in state_result.scalars().all()
        }
        for update in updates:
            food_id = int(update["ingredient_id"])
            item = cast(Any, by_food.get(food_id))
            if item is None:
                item = WeeklyMealPlanPantryItemORM(
                    id=str(uuid.uuid4()),
                    plan_id=plan_id,
                    food_reference_id=food_id,
                    available_amount=update.get("available_amount"),
                    available_unit=update.get("available_unit"),
                )
                self.session.add(item)
                by_food[food_id] = item
            else:
                item.available_amount = update.get("available_amount")
                item.available_unit = update.get("available_unit")
            if any(
                key in update for key in ("checked", "do_not_buy", "manually_owned")
            ):
                state = cast(Any, by_state.get(food_id))
                if state is None:
                    state = cast(
                        Any,
                        WeeklyGroceryItemStateORM(
                            id=str(uuid.uuid4()),
                            plan_id=plan_id,
                            food_reference_id=food_id,
                        ),
                    )
                    self.session.add(state)
                    by_state[food_id] = state
                state.checked = bool(update.get("checked", False))
                state.do_not_buy = bool(update.get("do_not_buy", False))
                state.manually_owned = bool(update.get("manually_owned", False))
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
                "available_amount": item.available_amount,
                "available_unit": item.available_unit,
            }
            for item in result.scalars().all()
        ]

    async def list_grocery_interactions(
        self, *, user_id: str, plan_id: str
    ) -> list[dict]:
        result = await self.session.execute(
            select(WeeklyGroceryItemStateORM)
            .join(
                WeeklyMealPlanORM,
                WeeklyMealPlanORM.id == WeeklyGroceryItemStateORM.plan_id,
            )
            .where(
                WeeklyGroceryItemStateORM.plan_id == plan_id,
                WeeklyMealPlanORM.user_id == user_id,
            )
        )
        return [
            {
                "food_reference_id": item.food_reference_id,
                "checked": item.checked,
                "do_not_buy": item.do_not_buy,
                "manually_owned": item.manually_owned,
            }
            for item in result.scalars().all()
        ]

    async def list_grocery_day_lines(self, *, user_id: str, plan_id: str) -> list[dict]:
        result = await self.session.execute(
            select(WeeklyGroceryDayLineORM)
            .join(
                WeeklyMealPlanORM,
                WeeklyMealPlanORM.id == WeeklyGroceryDayLineORM.plan_id,
            )
            .where(
                WeeklyGroceryDayLineORM.plan_id == plan_id,
                WeeklyMealPlanORM.user_id == user_id,
            )
        )
        return [
            {
                "food_reference_id": item.food_reference_id,
                "day_index": item.day_index,
                "needed_amount": item.needed_amount,
                "covered": item.covered,
            }
            for item in result.scalars().all()
        ]

    async def replace_grocery_day_lines(
        self,
        *,
        user_id: str,
        plan_id: str,
        ingredient_id: int,
        lines: list[dict],
    ) -> WeeklyMealPlan | None:
        row = await self.get_for_update(user_id=user_id, plan_id=plan_id)
        if row is None:
            return None
        await self._repair_late_legacy_slots(row)
        existing = await self.session.execute(
            select(WeeklyGroceryDayLineORM).where(
                WeeklyGroceryDayLineORM.plan_id == plan_id,
                WeeklyGroceryDayLineORM.food_reference_id == ingredient_id,
            )
        )
        by_day = {int(item.day_index): item for item in existing.scalars().all()}
        kept: set[int] = set()
        for line in lines:
            day_index = int(line["day_index"])
            kept.add(day_index)
            item = by_day.get(day_index)
            if item is None:
                self.session.add(
                    WeeklyGroceryDayLineORM(
                        id=str(uuid.uuid4()),
                        plan_id=plan_id,
                        food_reference_id=ingredient_id,
                        day_index=day_index,
                        needed_amount=line.get("needed_amount"),
                        covered=bool(line.get("covered", False)),
                    )
                )
                continue
            item.needed_amount = line.get("needed_amount")
            item.covered = bool(line.get("covered", False))
        for day_index, item in by_day.items():
            if day_index not in kept:
                await self.session.delete(item)
        await self.session.flush()
        return _to_domain(row)

    async def mark_slot_logged(
        self,
        *,
        user_id: str,
        plan_id: str,
        slot_id: str,
        meal_id: str,
        slot: WeeklyMealPlanSlotORM | None = None,
    ) -> None:
        if slot is None:
            result = await self.session.execute(
                select(WeeklyMealPlanSlotORM)
                .join(WeeklyMealPlanORM)
                .where(
                    WeeklyMealPlanSlotORM.id == slot_id,
                    WeeklyMealPlanSlotORM.plan_id == plan_id,
                    WeeklyMealPlanORM.user_id == user_id,
                )
                .with_for_update(of=WeeklyMealPlanSlotORM)
            )
            slot = result.scalar_one_or_none()
            if slot is None:
                raise ValueError("weekly meal slot not found")
        slot = cast(Any, slot)
        slot.is_logged = True
        slot.logged_meal_id = meal_id
        slot.version += 1
        await self.session.flush()

    @planner_timed("lock")
    async def get_slot_for_update(self, *, user_id: str, plan_id: str, slot_id: str):
        # Match mutation lock order: parent first, then slot. A fresh query below
        # observes logging/swaps that committed while the parent lock was awaited.
        await self.session.execute(
            select(WeeklyMealPlanORM.id)
            .where(
                WeeklyMealPlanORM.id == plan_id, WeeklyMealPlanORM.user_id == user_id
            )
            .with_for_update()
        )
        result = await self.session.execute(
            select(WeeklyMealPlanSlotORM)
            .join(WeeklyMealPlanORM)
            .options(
                noload(WeeklyMealPlanSlotORM.catalog_meal),
                contains_eager(WeeklyMealPlanSlotORM.plan).noload(
                    WeeklyMealPlanORM.slots
                ),
                contains_eager(WeeklyMealPlanSlotORM.plan).noload(
                    WeeklyMealPlanORM.pantry_items
                ),
            )
            .execution_options(populate_existing=True)
            .where(
                WeeklyMealPlanSlotORM.id == slot_id,
                WeeklyMealPlanSlotORM.plan_id == plan_id,
                WeeklyMealPlanORM.user_id == user_id,
            )
            .with_for_update(of=WeeklyMealPlanSlotORM)
        )
        return result.scalar_one_or_none()

    async def plan_exists(self, *, user_id: str, plan_id: str) -> bool:
        stmt = (
            select(WeeklyMealPlanORM.id)
            .where(
                WeeklyMealPlanORM.id == plan_id,
                WeeklyMealPlanORM.user_id == user_id,
            )
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none() is not None

    @planner_timed("plan_sql")
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
                selectinload(WeeklyMealPlanORM.slots).raiseload(
                    WeeklyMealPlanSlotORM.catalog_meal
                ),
                noload(WeeklyMealPlanORM.pantry_items),
            )
            .where(WeeklyMealPlanORM.user_id == user_id)
        )
        if plan_id is not None:
            stmt = stmt.where(WeeklyMealPlanORM.id == plan_id)
        if week_start_date is not None:
            stmt = stmt.where(WeeklyMealPlanORM.week_start_date == week_start_date)
        result = await self.session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            return None
        if _is_late_legacy_row(row):
            # Lock, then repair, so a concurrent writer cannot keep the 14-slot
            # shape. Reading bumps the revision; an in-flight meal edit repairs
            # inside its own lock and still matches the revision it loaded.
            locked = await self.get_for_update(
                user_id=user_id,
                plan_id=cast(str, row.id),
                include_pantry=False,
            )
            if locked is None:
                return None
            if await self._repair_late_legacy_slots(locked):
                locked.revision = int(locked.revision) + 1
                await self.session.flush()
            return _to_domain(locked)
        return _to_domain(row)

    async def _repair_late_legacy_slots(self, row: WeeklyMealPlanORM) -> bool:
        """Move lunch/dinner to slots 1 and 2 and insert an empty breakfast.

        Existing slot ids, recipes, and logged-meal links stay on their rows.
        Returns whether this call changed the plan. A second call is a no-op.
        """
        if not _is_late_legacy_row(row):
            return False
        slots = list(row.slots)
        dinners = [slot for slot in slots if int(slot.slot_index) == 1]
        lunches = [slot for slot in slots if int(slot.slot_index) == 0]
        for slot in dinners:
            slot.slot_index = 2
        await self.session.flush()
        for slot in lunches:
            slot.slot_index = 1
        await self.session.flush()
        plan_id = cast(str, row.id)
        for day in range(WEEKLY_DAYS):
            row.slots.append(
                WeeklyMealPlanSlotORM(
                    id=str(uuid.uuid4()),
                    plan_id=plan_id,
                    day_index=day,
                    slot_index=0,
                    is_logged=False,
                    version=1,
                )
            )
        await self.session.flush()
        return True


def _is_late_legacy_row(row: WeeklyMealPlanORM) -> bool:
    data = cast(Any, row)
    coordinates = {(int(slot.day_index), int(slot.slot_index)) for slot in data.slots}
    return is_late_legacy_two_slot_plan(
        algorithm_version=cast(str, data.algorithm_version),
        status=cast(str, data.status),
        coordinates=coordinates,
    )


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
            recipe_override=cast(dict | None, getattr(slot, "recipe_override", None)),
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
        revision=cast(int, data.revision),
        created_at=cast(datetime | None, data.created_at),
        updated_at=cast(datetime | None, data.updated_at),
    )
