"""Unlog, link and skip for weekly plan slots.

Each write locks the plan and its slots (the PATCH lock order), runs under the
router's Idempotency-Key, and returns the updated plan. Only unlog can touch
the diary: it deletes a meal the plan's log endpoint created, through the same
removal as ``DELETE /v1/meals/{id}``, then publishes and purges like that route.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, timedelta
from typing import Any

from src.api.exceptions import (
    ConflictException,
    ResourceNotFoundException,
    ValidationException,
)
from src.app.commands.meal_planner import (
    LinkMealPlanSlotCommand,
    SetMealPlanSlotSkipCommand,
    UnlogMealPlanSlotCommand,
)
from src.app.events.meal.meal_events import (
    MealDeletedEvent,
    invoke_local_cache_invalidation_hook,
)
from src.app.handlers.command_handlers.delete_meal_command_handler import remove_meal
from src.domain.model.meal.meal import MealStatus
from src.domain.model.weekly_meal_planner import (
    SLOT_LOG_SOURCE_DIARY,
    WeeklyMealPlan,
    WeeklyMealPlanSlot,
    meal_type_for_slot,
)
from src.domain.ports.integration_event_publisher_port import (
    IntegrationEventPublisherPort,
    require_event_publisher,
)
from src.domain.utils.fingerprint_utils import canonicalize_fingerprint
from src.domain.utils.timezone_utils import get_zone_info, resolve_user_timezone_async

logger = logging.getLogger(__name__)

UNLOG_OPERATION = "weekly_meal_plan_slot_unlog"
LINK_OPERATION = "weekly_meal_plan_slot_link"
SKIP_OPERATION = "weekly_meal_plan_slot_skip"


class WeeklyMealSlotStateService:
    def __init__(
        self,
        uow_factory: Any,
        *,
        event_publisher: IntegrationEventPublisherPort | None = None,
        environment: str = "development",
    ):
        self.uow_factory = uow_factory
        self.event_publisher = event_publisher
        self.environment = environment

    async def unlog(self, command: UnlogMealPlanSlotCommand) -> WeeklyMealPlan:
        removed: tuple[MealDeletedEvent, date] | None = None
        async with self.uow_factory() as uow:
            reservation = await _reserve(uow, command, UNLOG_OPERATION)
            if reservation.state == "replay":
                return await _replayed_plan(uow, command.user_id, reservation)
            try:
                plan, slot = await _locked_slot(uow, command)
                if not slot.is_logged:
                    raise ConflictException(
                        "Weekly meal slot is not logged",
                        error_code="WEEKLY_SLOT_NOT_LOGGED",
                    )
                # Release the slot first: the meal delete below then finds no
                # slot left to clear.
                plan = await uow.weekly_meal_plans.update_slot_state(
                    user_id=command.user_id,
                    plan_id=command.plan_id,
                    slot_id=command.slot_id,
                    is_logged=False,
                    logged_meal_id=None,
                    log_source=None,
                )
                if slot.unlog_deletes_meal and slot.logged_meal_id is not None:
                    removed = await remove_meal(
                        uow, command.user_id, slot.logged_meal_id, self.environment
                    )
                await uow.meal_write_operations.complete(
                    reservation, target_meal_id=plan.id, response={"plan_id": plan.id}
                )
            except Exception:
                await uow.meal_write_operations.release(reservation)
                raise
        if removed is not None:
            await self._after_meal_removed(command.user_id, *removed)
        return plan

    async def link(self, command: LinkMealPlanSlotCommand) -> WeeklyMealPlan:
        async with self.uow_factory() as uow:
            reservation = await _reserve(uow, command, LINK_OPERATION)
            if reservation.state == "replay":
                return await _replayed_plan(uow, command.user_id, reservation)
            try:
                plan, slot = await _locked_slot(uow, command)
                if slot.is_logged:
                    raise ConflictException(
                        "Weekly meal slot is already logged",
                        error_code="WEEKLY_SLOT_ALREADY_LOGGED",
                    )
                meal = await uow.meals.find_by_id(command.meal_id)
                if (
                    meal is None
                    or meal.user_id != command.user_id
                    or meal.status == MealStatus.INACTIVE
                ):
                    raise ResourceNotFoundException(
                        "Meal not found", error_code="MEAL_NOT_FOUND"
                    )
                linked_slot_id = (
                    await uow.weekly_meal_plans.find_slot_id_for_logged_meal(
                        user_id=command.user_id, meal_id=command.meal_id
                    )
                )
                if linked_slot_id is not None:
                    raise ConflictException(
                        "This meal is already logged against another weekly slot",
                        error_code="WEEKLY_MEAL_ALREADY_LINKED",
                    )
                timezone = await resolve_user_timezone_async(
                    command.user_id, uow, header_timezone=command.timezone
                )
                if not _meal_fits_slot(meal, plan, slot, timezone):
                    raise ValidationException(
                        "The meal's date and meal type must match the weekly slot",
                        error_code="WEEKLY_SLOT_MISMATCH",
                    )
                plan = await uow.weekly_meal_plans.update_slot_state(
                    user_id=command.user_id,
                    plan_id=command.plan_id,
                    slot_id=command.slot_id,
                    is_logged=True,
                    logged_meal_id=command.meal_id,
                    log_source=SLOT_LOG_SOURCE_DIARY,
                    is_skipped=False,
                )
                await uow.meal_write_operations.complete(
                    reservation, target_meal_id=plan.id, response={"plan_id": plan.id}
                )
            except Exception:
                await uow.meal_write_operations.release(reservation)
                raise
        return plan

    async def set_skip(self, command: SetMealPlanSlotSkipCommand) -> WeeklyMealPlan:
        async with self.uow_factory() as uow:
            reservation = await _reserve(uow, command, SKIP_OPERATION)
            if reservation.state == "replay":
                return await _replayed_plan(uow, command.user_id, reservation)
            try:
                plan, slot = await _locked_slot(uow, command)
                if command.skipped and slot.is_logged:
                    raise ConflictException(
                        "A logged weekly meal slot cannot be skipped",
                        error_code="WEEKLY_SLOT_ALREADY_LOGGED",
                    )
                if slot.is_skipped != command.skipped:
                    plan = await uow.weekly_meal_plans.update_slot_state(
                        user_id=command.user_id,
                        plan_id=command.plan_id,
                        slot_id=command.slot_id,
                        is_skipped=command.skipped,
                    )
                await uow.meal_write_operations.complete(
                    reservation, target_meal_id=plan.id, response={"plan_id": plan.id}
                )
            except Exception:
                await uow.meal_write_operations.release(reservation)
                raise
        return plan

    async def _after_meal_removed(
        self, user_id: str, event: MealDeletedEvent, log_date: date
    ) -> None:
        """Publish and purge after commit, as the meal delete route does."""
        try:
            await require_event_publisher(self.event_publisher).publish(
                event.to_payload()
            )
        except Exception:
            logger.warning(
                "Weekly slot unlog could not publish meal.deleted aggregate_id=%s",
                event.aggregate_id,
                exc_info=True,
            )
        await invoke_local_cache_invalidation_hook(user_id, log_date, None)


async def _reserve(uow, command, operation: str):
    fingerprint = canonicalize_fingerprint(
        {key: value for key, value in vars(command).items() if key != "idempotency_key"}
    )
    reservation = await uow.meal_write_operations.reserve(
        user_id=command.user_id,
        operation=operation,
        idempotency_key=command.idempotency_key,
        request_fingerprint=fingerprint,
    )
    if reservation.state == "fingerprint_conflict":
        raise ConflictException(
            "Idempotency-Key was already used for a different request",
            error_code="IDEMPOTENCY_KEY_REUSED",
        )
    if reservation.state == "in_progress":
        raise ConflictException(
            "The same weekly slot write is already in progress",
            error_code="IDEMPOTENCY_IN_PROGRESS",
        )
    return reservation


async def _replayed_plan(uow, user_id: str, reservation) -> WeeklyMealPlan:
    plan = await uow.weekly_meal_plans.get_by_id(
        user_id=user_id, plan_id=reservation.target_meal_id
    )
    if plan is None:
        raise ConflictException(
            "Weekly slot replay is missing its plan",
            error_code="IDEMPOTENCY_REPLAY_INVALID",
        )
    return plan


async def _locked_slot(uow, command) -> tuple[WeeklyMealPlan, WeeklyMealPlanSlot]:
    plan = await uow.weekly_meal_plans.get_plan_for_slot_write(
        user_id=command.user_id, plan_id=command.plan_id
    )
    if plan is None:
        raise ResourceNotFoundException(
            "Weekly meal plan not found", error_code="WEEKLY_PLAN_NOT_FOUND"
        )
    slot = next((item for item in plan.slots if item.id == command.slot_id), None)
    if slot is None:
        raise ResourceNotFoundException(
            "Weekly meal slot not found", error_code="WEEKLY_SLOT_NOT_FOUND"
        )
    return plan, slot


def _meal_fits_slot(meal, plan: WeeklyMealPlan, slot: WeeklyMealPlanSlot, tz) -> bool:
    """Same slot date as logging: week start plus day index, in the user's day."""
    slot_date = plan.week_start_date + timedelta(days=slot.day_index)
    created_at = meal.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    meal_date = created_at.astimezone(get_zone_info(tz)).date()
    meal_type = (meal.meal_type or "").strip().casefold()
    return meal_date == slot_date and meal_type == meal_type_for_slot(slot.slot_index)
