"""Portion-aware diary logging for weekly plan slots."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from src.api.exceptions import (
    ConflictException,
    ResourceNotFoundException,
    ValidationException,
)
from src.app.commands.meal_planner import LogMealPlanSlotCommand
from src.app.services.recommended_meal_materialization_service import (
    RecommendedMealMaterializationService,
)
from src.domain.utils.fingerprint_utils import canonicalize_fingerprint


@dataclass(frozen=True)
class LogMealPlanSlotResult:
    logged_meal_id: str
    slot_id: str
    calories: float


class WeeklyMealLoggingService:
    """Claim a weekly slot and create the normal backend-owned meal record."""

    def __init__(self, uow_factory, *, materializer=None):
        self.uow_factory = uow_factory
        self.materializer = materializer or RecommendedMealMaterializationService()

    async def log(self, command: LogMealPlanSlotCommand) -> LogMealPlanSlotResult:
        if command.portion_multiplier not in {0.5, 1.0, 1.5, 2.0}:
            raise ValidationException(
                "portion_multiplier must be 0.5, 1, 1.5, or 2",
                error_code="PORTION_INVALID",
            )
        fingerprint = canonicalize_fingerprint(
            {
                key: value
                for key, value in vars(command).items()
                if key != "idempotency_key"
            }
        )
        async with self.uow_factory() as uow:
            reservation = await uow.meal_write_operations.reserve(
                user_id=command.user_id,
                operation="weekly_meal_plan_slot_log",
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
                    "The same slot log is already in progress",
                    error_code="IDEMPOTENCY_IN_PROGRESS",
                )
            if reservation.state == "replay":
                return _replay_result(reservation.response)
            try:
                plan = await uow.weekly_meal_plans.get_by_id(
                    user_id=command.user_id, plan_id=command.plan_id
                )
                if plan is None:
                    raise ResourceNotFoundException("Weekly meal plan not found")
                slot = await uow.weekly_meal_plans.get_slot_for_update(
                    user_id=command.user_id,
                    plan_id=command.plan_id,
                    slot_id=command.slot_id,
                )
                if slot is None:
                    raise ResourceNotFoundException("Weekly meal slot not found")
                expected_date = plan.week_start_date + timedelta(days=slot.day_index)
                expected_type = "lunch" if slot.slot_index == 0 else "dinner"
                if (
                    command.meal_date != expected_date
                    or command.meal_type != expected_type
                ):
                    raise ValidationException(
                        "date and meal_type must match the selected weekly slot",
                        error_code="WEEKLY_SLOT_MISMATCH",
                    )
                if slot.is_logged:
                    raise ConflictException(
                        "Weekly meal slot is already logged",
                        error_code="WEEKLY_SLOT_ALREADY_LOGGED",
                    )
                if slot.catalog_meal_id is None:
                    raise ValidationException(
                        "Cannot log an empty weekly meal slot",
                        error_code="WEEKLY_SLOT_EMPTY",
                    )
                catalog_meal = await uow.catalog_recipes.get_meal(slot.catalog_meal_id)
                if catalog_meal is None:
                    raise ResourceNotFoundException(
                        "Planned recipe is no longer available"
                    )
                meal = await self.materializer.materialize_from_catalog(
                    uow,
                    user_id=command.user_id,
                    catalog_meal=catalog_meal,
                    meal_date=command.meal_date,
                    meal_type=command.meal_type,
                    timezone=command.timezone,
                    portion_multiplier=command.portion_multiplier,
                    source="weekly_meal_planner",
                )
                await uow.weekly_meal_plans.mark_slot_logged(
                    user_id=command.user_id,
                    plan_id=command.plan_id,
                    slot_id=command.slot_id,
                    meal_id=meal.meal_id,
                )
                result = LogMealPlanSlotResult(
                    logged_meal_id=meal.meal_id,
                    slot_id=command.slot_id,
                    calories=meal.nutrition.macros.total_calories,
                )
                await uow.meal_write_operations.complete(
                    reservation,
                    target_meal_id=meal.meal_id,
                    response={
                        "logged_meal_id": result.logged_meal_id,
                        "slot_id": result.slot_id,
                        "calories": result.calories,
                    },
                )
                return result
            except Exception:
                await uow.meal_write_operations.release(reservation)
                raise


def _replay_result(response: dict | None) -> LogMealPlanSlotResult:
    if (
        not isinstance(response, dict)
        or not response.get("logged_meal_id")
        or not response.get("slot_id")
    ):
        raise ConflictException(
            "Weekly slot log replay is missing its stored meal",
            error_code="IDEMPOTENCY_REPLAY_INVALID",
        )
    return LogMealPlanSlotResult(
        logged_meal_id=str(response["logged_meal_id"]),
        slot_id=str(response["slot_id"]),
        calories=float(response.get("calories") or 0),
    )
