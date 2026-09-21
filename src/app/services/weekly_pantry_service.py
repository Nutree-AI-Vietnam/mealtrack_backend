"""Idempotent pantry mutations for weekly plans."""

from __future__ import annotations

from src.api.exceptions import (
    ConflictException,
    ResourceNotFoundException,
    ValidationException,
)
from src.app.commands.meal_planner import UpdateMealPlanPantryStockCommand
from src.domain.utils.fingerprint_utils import canonicalize_fingerprint


class WeeklyPantryService:
    """Validate stock updates and persist them in the plan transaction."""

    def __init__(self, uow_factory):
        self.uow_factory = uow_factory

    async def update(self, command: UpdateMealPlanPantryStockCommand):
        if len(command.updates) > 100:
            raise ValidationException(
                "at most 100 pantry updates are allowed", error_code="PANTRY_TOO_LARGE"
            )
        for update in command.updates:
            if update.get("kind") not in {"bought", "owned"}:
                raise ValidationException(
                    "kind must be bought or owned", error_code="PANTRY_KIND_INVALID"
                )
            amount = update.get("amount")
            if amount is not None and float(amount) < 0:
                raise ValidationException(
                    "amount must be non-negative", error_code="PANTRY_AMOUNT_INVALID"
                )
            if update.get("unit") is not None and not str(update["unit"]).strip():
                raise ValidationException(
                    "unit must be non-empty when provided",
                    error_code="PANTRY_UNIT_INVALID",
                )
            try:
                int(update["ingredient_id"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValidationException(
                    "ingredient_id must be a canonical food reference id",
                    error_code="PANTRY_INGREDIENT_INVALID",
                ) from exc
        fingerprint = canonicalize_fingerprint(
            {
                "plan_id": command.plan_id,
                "updates": command.updates,
                "user_id": command.user_id,
            }
        )
        async with self.uow_factory() as uow:
            reservation = await uow.meal_write_operations.reserve(
                user_id=command.user_id,
                operation="weekly_meal_plan_pantry",
                idempotency_key=command.idempotency_key,
                request_fingerprint=fingerprint,
            )
            if reservation.state == "fingerprint_conflict":
                raise ConflictException(
                    "Idempotency-Key was already used for a different pantry request",
                    error_code="IDEMPOTENCY_KEY_REUSED",
                )
            if reservation.state == "in_progress":
                raise ConflictException(
                    "The same pantry write is already in progress",
                    error_code="IDEMPOTENCY_IN_PROGRESS",
                )
            if reservation.state == "replay":
                return {"success": True, "updated_count": len(command.updates)}
            try:
                plan = await uow.weekly_meal_plans.update_pantry(
                    user_id=command.user_id,
                    plan_id=command.plan_id,
                    updates=command.updates,
                )
                if plan is None:
                    raise ResourceNotFoundException("Weekly meal plan not found")
                await uow.meal_write_operations.complete(
                    reservation,
                    target_meal_id=plan.id,
                    response={"success": True, "updated_count": len(command.updates)},
                )
                return {"success": True, "updated_count": len(command.updates)}
            except Exception:
                await uow.meal_write_operations.release(reservation)
                raise
