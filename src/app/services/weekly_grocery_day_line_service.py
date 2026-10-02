"""Save one ingredient's day notes for the current weekly plan."""

from __future__ import annotations

from src.api.exceptions import (
    ConflictException,
    ResourceNotFoundException,
    ValidationException,
)
from src.app.commands.meal_planner import UpdateGroceryDayLinesCommand
from src.domain.utils.fingerprint_utils import canonicalize_fingerprint


class WeeklyGroceryDayLineService:
    """Replace an ingredient's day lines without touching pantry stock."""

    def __init__(self, uow_factory):
        self.uow_factory = uow_factory

    async def update(self, command: UpdateGroceryDayLinesCommand):
        lines = _validated_lines(command.ingredient_id, command.lines)
        fingerprint = canonicalize_fingerprint(
            {
                "ingredient_id": command.ingredient_id,
                "lines": lines,
                "plan_id": command.plan_id,
                "user_id": command.user_id,
            }
        )
        async with self.uow_factory() as uow:
            reservation = await uow.meal_write_operations.reserve(
                user_id=command.user_id,
                operation="weekly_grocery_day_lines",
                idempotency_key=command.idempotency_key,
                request_fingerprint=fingerprint,
            )
            if reservation.state == "fingerprint_conflict":
                raise ConflictException(
                    "Idempotency-Key was already used for a different day-note request",
                    error_code="IDEMPOTENCY_KEY_REUSED",
                )
            if reservation.state == "in_progress":
                raise ConflictException(
                    "The same day-note write is already in progress",
                    error_code="IDEMPOTENCY_IN_PROGRESS",
                )
            if reservation.state == "replay":
                return {"success": True, "updated_count": len(lines)}
            try:
                plan = await uow.weekly_meal_plans.replace_grocery_day_lines(
                    user_id=command.user_id,
                    plan_id=command.plan_id,
                    ingredient_id=command.ingredient_id,
                    lines=lines,
                )
                if plan is None:
                    raise ResourceNotFoundException("Weekly meal plan not found")
                await uow.meal_write_operations.complete(
                    reservation,
                    target_meal_id=plan.id,
                    response={"success": True, "updated_count": len(lines)},
                )
                return {"success": True, "updated_count": len(lines)}
            except Exception:
                await uow.meal_write_operations.release(reservation)
                raise


def _validated_lines(ingredient_id: int, lines: list[dict]) -> list[dict]:
    try:
        if int(ingredient_id) < 1:
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise ValidationException(
            "ingredient_id must be a canonical food reference id",
            error_code="GROCERY_DAY_INGREDIENT_INVALID",
        ) from exc
    if len(lines) > 7:
        raise ValidationException(
            "at most 7 day notes are allowed",
            error_code="GROCERY_DAY_TOO_LARGE",
        )
    seen: set[int] = set()
    normalized: list[dict] = []
    for line in lines:
        try:
            day_index = int(line["day_index"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValidationException(
                "day_index must be an integer from 0 to 6",
                error_code="GROCERY_DAY_INDEX_INVALID",
            ) from exc
        if day_index < 0 or day_index > 6 or day_index in seen:
            raise ValidationException(
                "day_index must be unique and from 0 to 6",
                error_code="GROCERY_DAY_INDEX_INVALID",
            )
        seen.add(day_index)
        amount = line.get("needed_amount")
        if amount is not None and float(amount) < 0:
            raise ValidationException(
                "needed_amount must be non-negative",
                error_code="GROCERY_DAY_AMOUNT_INVALID",
            )
        covered = bool(line.get("covered", False))
        if amount is None and not covered:
            raise ValidationException(
                "a day note needs an amount or a checked day",
                error_code="GROCERY_DAY_EMPTY",
            )
        normalized.append(
            {
                "day_index": day_index,
                "needed_amount": None if amount is None else float(amount),
                "covered": covered,
            }
        )
    return normalized
