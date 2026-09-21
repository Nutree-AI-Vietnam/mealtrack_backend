"""Application services for weekly plan generation and mutation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from pydantic import ValidationError

from src.api.exceptions import (
    ConflictException,
    ExternalServiceException,
    ResourceNotFoundException,
    ValidationException,
)
from src.app.commands.meal_planner import (
    AiAdjustMealPlanCommand,
    GenerateWeeklyMealPlanCommand,
    UpdateWeeklyMealPlanCommand,
)
from src.domain.exceptions.weekly_meal_planner_exceptions import (
    WeeklyMealPlanConflictError,
)
from src.domain.model.weekly_meal_planner import (
    WeeklyMealPlan,
    WeeklyMealPlanAdjustmentProposal,
    WeeklyMealPlanPreferences,
)
from src.domain.services.weekly_meal_planner import WeeklyPlanGenerationService
from src.domain.utils.fingerprint_utils import canonicalize_fingerprint


@dataclass(frozen=True)
class WeeklyPlanAiProposal:
    explanation: str
    diff_summary: str
    proposed_plan: WeeklyMealPlan
    slot_changes: tuple[dict, ...]
    base_revision: int = 1


class WeeklyMealPlanService:
    """Coordinates domain generation with transactional repositories."""

    def __init__(
        self,
        uow_factory,
        *,
        generator: WeeklyPlanGenerationService | None = None,
        ai_adjustment_provider=None,
    ):
        self.uow_factory = uow_factory
        self.generator = generator or WeeklyPlanGenerationService()
        self.ai_adjustment_provider = ai_adjustment_provider

    async def generate(self, command: GenerateWeeklyMealPlanCommand) -> WeeklyMealPlan:
        fingerprint = _fingerprint(command)
        async with self.uow_factory() as uow:
            reservation = await uow.meal_write_operations.reserve(
                user_id=command.user_id,
                operation="weekly_meal_plan_generate",
                idempotency_key=command.idempotency_key,
                request_fingerprint=fingerprint,
            )
            self._check_reservation(reservation)
            if reservation.state == "replay":
                plan = await uow.weekly_meal_plans.get_by_id(
                    user_id=command.user_id, plan_id=reservation.target_meal_id
                )
                if plan is None:
                    raise ConflictException(
                        "Weekly plan replay is missing its plan",
                        error_code="IDEMPOTENCY_REPLAY_INVALID",
                    )
                return plan
            try:
                await uow.weekly_meal_plans.lock_user_week(
                    user_id=command.user_id, week_start_date=command.week_start_date
                )
                revision = await uow.catalog_recipes.get_active_catalog_revision()
                meals = await uow.catalog_recipes.list_active_meals()
                selected = self.generator.generate(
                    meals,
                    user_id=command.user_id,
                    week_start_date=command.week_start_date.isoformat(),
                    daily_calories=command.daily_calories,
                    preferences=command.preferences,
                )
                recipe_ids = {
                    (slot.day_index, slot.slot_index): slot.recipe_id
                    for slot in selected
                }
                revision_key = _revision_key(revision)
                current = await uow.weekly_meal_plans.get_current(
                    user_id=command.user_id, week_start_date=command.week_start_date
                )
                if current is None:
                    plan = await uow.weekly_meal_plans.create(
                        user_id=command.user_id,
                        week_start_date=command.week_start_date,
                        timezone=command.timezone,
                        preferences=command.preferences,
                        daily_calories=command.daily_calories,
                        catalog_revision=revision_key,
                        recipe_ids=recipe_ids,
                    )
                else:
                    if current.status.value == "confirmed":
                        raise WeeklyMealPlanConflictError()
                    plan = await uow.weekly_meal_plans.update(
                        user_id=command.user_id,
                        plan_id=current.id,
                        people=command.preferences.people,
                        preferences=command.preferences,
                        slots=recipe_ids,
                    )
                await uow.meal_write_operations.complete(
                    reservation, target_meal_id=plan.id, response={"plan_id": plan.id}
                )
                return plan
            except Exception:
                await uow.meal_write_operations.release(reservation)
                raise

    async def update(self, command: UpdateWeeklyMealPlanCommand) -> WeeklyMealPlan:
        fingerprint = _fingerprint(command)
        async with self.uow_factory() as uow:
            reservation = await uow.meal_write_operations.reserve(
                user_id=command.user_id,
                operation="weekly_meal_plan_update",
                idempotency_key=command.idempotency_key,
                request_fingerprint=fingerprint,
            )
            self._check_reservation(reservation)
            if reservation.state == "replay":
                plan = await uow.weekly_meal_plans.get_by_id(
                    user_id=command.user_id, plan_id=reservation.target_meal_id
                )
                if plan is None:
                    raise ConflictException(
                        "Weekly plan replay is missing its plan",
                        error_code="IDEMPOTENCY_REPLAY_INVALID",
                    )
                return plan
            try:
                await self._validate_recipe_ids(uow, command.slots)
                plan = await uow.weekly_meal_plans.update(
                    user_id=command.user_id,
                    plan_id=command.plan_id,
                    people=command.people,
                    preferences=command.preferences,
                    status=command.status,
                    slots=command.slots,
                    expected_revision=command.expected_revision,
                )
                if plan is None:
                    raise ResourceNotFoundException("Weekly meal plan not found")
                await uow.meal_write_operations.complete(
                    reservation, target_meal_id=plan.id, response={"plan_id": plan.id}
                )
                return plan
            except Exception:
                await uow.meal_write_operations.release(reservation)
                raise

    async def current(
        self, *, user_id: str, week_start_date: date
    ) -> WeeklyMealPlan | None:
        async with self.uow_factory() as uow:
            return await uow.weekly_meal_plans.get_current(
                user_id=user_id, week_start_date=week_start_date
            )

    async def ai_proposal(
        self, command: AiAdjustMealPlanCommand
    ) -> WeeklyPlanAiProposal:
        prompt = command.prompt.strip()
        if not prompt or len(prompt) > 1000:
            raise ValidationException(
                "prompt must contain 1 to 1000 characters",
                error_code="AI_PROMPT_INVALID",
            )
        async with self.uow_factory() as uow:
            plan = await uow.weekly_meal_plans.get_by_id(
                user_id=command.user_id, plan_id=command.plan_id
            )
            if plan is None:
                raise ResourceNotFoundException("Weekly meal plan not found")
            meals = await uow.catalog_recipes.list_active_meals()
            if self.ai_adjustment_provider is not None:
                return await self._provider_proposal(
                    command=command, plan=plan, meals=tuple(meals)
                )
            updated_preferences = _preferences_from_prompt(plan.preferences, prompt)
            selected = self.generator.generate(
                meals,
                user_id=command.user_id,
                week_start_date=plan.week_start_date.isoformat(),
                daily_calories=plan.daily_calories or 2000,
                preferences=updated_preferences,
            )
            proposed_slots = tuple(
                slot.__class__(
                    id=slot.id,
                    day_index=slot.day_index,
                    slot_index=slot.slot_index,
                    recipe_id={
                        (item.day_index, item.slot_index): item.recipe_id
                        for item in selected
                    }.get((slot.day_index, slot.slot_index)),
                    is_logged=slot.is_logged,
                    logged_meal_id=slot.logged_meal_id,
                    version=slot.version,
                )
                for slot in plan.slots
            )
            proposed = plan.__class__(
                id=plan.id,
                user_id=plan.user_id,
                week_start_date=plan.week_start_date,
                status=plan.status,
                people=updated_preferences.people,
                preferences=updated_preferences,
                timezone=plan.timezone,
                slots=proposed_slots,
                daily_calories=plan.daily_calories,
                catalog_revision=plan.catalog_revision,
                algorithm_version=plan.algorithm_version,
                revision=plan.revision,
                created_at=plan.created_at,
                updated_at=plan.updated_at,
            )
            changes = tuple(
                {
                    "day_index": before.day_index,
                    "slot_index": before.slot_index,
                    "previous_recipe_id": before.recipe_id,
                    "new_recipe_id": after.recipe_id,
                    "action": "replace" if after.recipe_id else "clear",
                }
                for before, after in zip(plan.slots, proposed.slots, strict=True)
                if before.recipe_id != after.recipe_id
            )
            return WeeklyPlanAiProposal(
                base_revision=plan.revision,
                explanation="Prepared a reviewable weekly plan proposal from your request.",
                diff_summary=f"{len(changes)} meals changed",
                proposed_plan=proposed,
                slot_changes=changes,
            )

    async def _provider_proposal(self, *, command, plan, meals):
        try:
            response = await self.ai_adjustment_provider.propose(
                prompt=command.prompt, plan=plan, meals=meals
            )
        except ValidationError as exc:
            raise ValidationException(
                "AI returned an invalid meal plan proposal",
                error_code="AI_OUTPUT_INVALID",
            ) from exc
        except Exception as exc:
            if isinstance(exc, (ValidationException, ResourceNotFoundException)):
                raise
            raise ExternalServiceException(
                "AI meal plan adjustment is temporarily unavailable",
                error_code="AI_MEAL_PLAN_UNAVAILABLE",
            ) from exc
        if not isinstance(response, WeeklyMealPlanAdjustmentProposal):
            raise ValidationException(
                "AI returned an invalid meal plan proposal",
                error_code="AI_OUTPUT_INVALID",
            )
        if response.base_revision != plan.revision:
            raise ValidationException(
                "AI proposal was generated from a stale weekly meal plan",
                error_code="AI_OUTPUT_STALE_REVISION",
            )
        available_ids = {meal.id for meal in meals}
        by_coordinate = {(slot.day_index, slot.slot_index): slot for slot in plan.slots}
        proposed = list(plan.slots)
        changes = []
        seen = set()
        for change in response.slot_changes:
            coordinate = (change.day_index, change.slot_index)
            if coordinate in seen or coordinate not in by_coordinate:
                raise ValidationException(
                    "AI returned duplicate or invalid weekly slot coordinates",
                    error_code="AI_OUTPUT_INVALID",
                )
            seen.add(coordinate)
            current = by_coordinate[coordinate]
            if current.is_logged:
                raise ValidationException(
                    "AI cannot change a logged weekly meal slot",
                    error_code="AI_OUTPUT_INVALID",
                )
            new_recipe_id = change.new_recipe_id if change.action == "replace" else None
            if new_recipe_id is not None and new_recipe_id not in available_ids:
                raise ValidationException(
                    "AI returned a recipe outside the active catalog",
                    error_code="AI_OUTPUT_INVALID",
                )
            proposed[plan.slots.index(current)] = current.__class__(
                id=current.id,
                day_index=current.day_index,
                slot_index=current.slot_index,
                recipe_id=new_recipe_id,
                is_logged=current.is_logged,
                logged_meal_id=current.logged_meal_id,
                version=current.version,
            )
            changes.append(
                {
                    "day_index": current.day_index,
                    "slot_index": current.slot_index,
                    "previous_recipe_id": current.recipe_id,
                    "new_recipe_id": new_recipe_id,
                    "action": change.action,
                }
            )
        proposed_plan = plan.__class__(
            id=plan.id,
            user_id=plan.user_id,
            week_start_date=plan.week_start_date,
            status=plan.status,
            people=plan.people,
            preferences=plan.preferences,
            timezone=plan.timezone,
            slots=tuple(proposed),
            daily_calories=plan.daily_calories,
            catalog_revision=plan.catalog_revision,
            algorithm_version=plan.algorithm_version,
            revision=plan.revision,
            created_at=plan.created_at,
            updated_at=plan.updated_at,
        )
        return WeeklyPlanAiProposal(
            base_revision=plan.revision,
            explanation=response.explanation,
            diff_summary=f"{len(changes)} meals changed",
            proposed_plan=proposed_plan,
            slot_changes=tuple(changes),
        )

    async def _validate_recipe_ids(
        self, uow, slots: dict[tuple[int, int], str | None] | None
    ) -> None:
        if not slots:
            return
        for (day_index, slot_index), recipe_id in slots.items():
            if day_index not in range(7) or slot_index not in range(2):
                raise ValidationException(
                    "slot coordinate is invalid", error_code="WEEKLY_SLOT_INVALID"
                )
            if (
                recipe_id is not None
                and await uow.catalog_recipes.get_meal(recipe_id) is None
            ):
                raise ValidationException(
                    "recipe_id is not an active catalog recipe",
                    error_code="RECIPE_NOT_FOUND",
                )

    @staticmethod
    def _check_reservation(reservation) -> None:
        if reservation.state == "fingerprint_conflict":
            raise ConflictException(
                "Idempotency-Key was already used for a different request",
                error_code="IDEMPOTENCY_KEY_REUSED",
            )
        if reservation.state == "in_progress":
            raise ConflictException(
                "The same weekly plan write is already in progress",
                error_code="IDEMPOTENCY_IN_PROGRESS",
            )


def _fingerprint(command) -> str:
    payload = {
        key: value for key, value in vars(command).items() if key != "idempotency_key"
    }
    return canonicalize_fingerprint(payload)


def _revision_key(revision) -> str:
    return ":".join(
        str(value or "")
        for value in (
            revision.active_count,
            revision.catalog_updated_at,
            revision.food_reference_updated_at,
        )
    )


def _preferences_from_prompt(
    current: WeeklyMealPlanPreferences, prompt: str
) -> WeeklyMealPlanPreferences:
    lowered = prompt.casefold()
    diet = (
        "vegetarian" if "vegetarian" in lowered or "chay" in lowered else current.diet
    )
    cooking_time = (
        "30"
        if "30" in lowered or "quick" in lowered or "nhanh" in lowered
        else current.cooking_time
    )
    return WeeklyMealPlanPreferences(
        people=current.people,
        diet=diet,
        cooking_time=cooking_time,
        cuisine=current.cuisine,
        dislikes=current.dislikes,
        allergies=current.allergies,
    )
