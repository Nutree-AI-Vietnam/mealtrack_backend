"""Application services for weekly plan generation and mutation."""

from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from time import monotonic

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
from src.app.services.planner_presentation_copy import planner_copy
from src.app.services.weekly_grocery_service import WeeklyGroceryService
from src.domain.exceptions.weekly_meal_planner_exceptions import (
    WeeklyMealPlanConflictError,
)
from src.domain.model.weekly_meal_planner import (
    WEEKLY_SLOTS_PER_DAY,
    WeeklyMealPlan,
    WeeklyMealPlanAdjustmentProposal,
    WeeklyMealPlanPreferences,
    WeeklyMealPlanSlotAdjustment,
)
from src.domain.services.weekly_meal_planner import WeeklyPlanGenerationService
from src.domain.services.weekly_meal_planner.allergen_constraint import (
    resolve_allergen_preferences,
)
from src.domain.services.weekly_meal_planner.grocery_projection import (
    DerivedGroceryItem,
)
from src.domain.services.weekly_meal_planner.slot_rules import (
    ensure_base_revision,
    ensure_slot_coordinate,
)
from src.domain.utils.fingerprint_utils import canonicalize_fingerprint
from src.planner_observability import planner_phase, planner_timed
from src.planner_request_policy import (
    planner_deadline,
    planner_retry_budget,
    remaining_budget,
)


@dataclass(frozen=True)
class WeeklyPlanAiProposal:
    explanation: str
    diff_summary: str
    proposed_plan: WeeklyMealPlan
    slot_changes: tuple[dict, ...]
    base_revision: int = 1
    proposed_groceries: tuple[DerivedGroceryItem, ...] = ()


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
        self.grocery_service = WeeklyGroceryService()

    def _select_recipes(self, *args, **kwargs):
        with planner_phase("selection_cpu"):
            return self.generator.generate(*args, **kwargs)

    @planner_timed("service")
    async def generate(self, command: GenerateWeeklyMealPlanCommand) -> WeeklyMealPlan:
        if os.getenv("WEEKLY_PLANNER_SHORT_GENERATION", "false").lower() in {
            "true",
            "1",
        }:
            return await self._generate_prepared(command)
        fingerprint = _fingerprint(command)
        async with self.uow_factory() as uow:
            reservation = await uow.meal_write_operations.reserve(
                user_id=command.user_id,
                operation="weekly_meal_plan_generate",
                idempotency_key=command.idempotency_key,
                request_fingerprint=fingerprint,
            )
            if (
                reservation.state == "fingerprint_conflict"
                and reservation.request_fingerprint == _legacy_fingerprint(command)
            ):
                # Reservations written before preference fields were hashed
                # individually still belong to this same generate request.
                reservation = await uow.meal_write_operations.adopt_fingerprint(
                    reservation, request_fingerprint=fingerprint
                )
            if reservation.state == "replay":
                plan = await uow.weekly_meal_plans.get_by_id(
                    user_id=command.user_id, plan_id=reservation.target_meal_id
                )
                if plan is None:
                    raise ConflictException(
                        "Weekly plan replay is missing its plan",
                        error_code="IDEMPOTENCY_REPLAY_INVALID",
                    )
                if _plan_has_assigned_recipe(plan):
                    return plan
                reservation = await uow.meal_write_operations.reopen_completed(
                    reservation
                )
            self._check_reservation(reservation)
            try:
                await uow.weekly_meal_plans.lock_user_week(
                    user_id=command.user_id, week_start_date=command.week_start_date
                )
                current = await uow.weekly_meal_plans.get_current(
                    user_id=command.user_id, week_start_date=command.week_start_date
                )
                if current is not None and current.status.value == "confirmed":
                    raise WeeklyMealPlanConflictError()
                revision = await uow.catalog_recipes.get_active_catalog_revision()
                meals = await uow.catalog_recipes.list_active_meals()
                selected = self._select_recipes(
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
                    logged_coordinates = {
                        (slot.day_index, slot.slot_index)
                        for slot in current.slots
                        if slot.is_logged
                    }
                    generated_slots = {
                        coordinate: recipe_id
                        for coordinate, recipe_id in recipe_ids.items()
                        if coordinate not in logged_coordinates
                    }
                    plan = await uow.weekly_meal_plans.update(
                        user_id=command.user_id,
                        plan_id=current.id,
                        people=command.preferences.people,
                        preferences=command.preferences,
                        slots=generated_slots,
                        algorithm_version=self.generator.algorithm_version,
                    )
                await uow.meal_write_operations.complete(
                    reservation, target_meal_id=plan.id, response={"plan_id": plan.id}
                )
                await _enqueue_plan_preparation(uow, plan)
                return plan
            except Exception:
                await uow.meal_write_operations.release(reservation)
                raise

    async def _generate_prepared(
        self, command: GenerateWeeklyMealPlanCommand
    ) -> WeeklyMealPlan:
        """Select without a write claim; fence publication and owner/week at commit."""
        fingerprint = _fingerprint(command)
        for attempt in range(2):
            async with self.uow_factory() as uow:
                prior = await uow.meal_write_operations.lookup(
                    user_id=command.user_id,
                    operation="weekly_meal_plan_generate",
                    idempotency_key=command.idempotency_key,
                )
                if prior is not None:
                    if prior.request_fingerprint not in {
                        fingerprint,
                        _legacy_fingerprint(command),
                    }:
                        raise ConflictException(
                            "Idempotency key belongs to another request",
                            error_code="IDEMPOTENCY_KEY_REUSED",
                        )
                    if prior.state == "in_progress":
                        self._check_reservation(prior)
                    if prior.state == "replay":
                        replay = await uow.weekly_meal_plans.get_by_id(
                            user_id=command.user_id, plan_id=prior.target_meal_id
                        )
                        if replay is None:
                            raise ConflictException(
                                "Weekly plan replay is missing its plan",
                                error_code="IDEMPOTENCY_REPLAY_INVALID",
                            )
                        if _plan_has_assigned_recipe(replay):
                            return replay
                current = await uow.weekly_meal_plans.get_current(
                    user_id=command.user_id, week_start_date=command.week_start_date
                )
                if current is not None and current.status.value == "confirmed":
                    raise WeeklyMealPlanConflictError()
                captured_state = _generation_state(current)
                captured_version = (
                    await uow.catalog_recipes.capture_catalog_publication_version()
                )
                revision = await uow.catalog_recipes.get_active_catalog_revision()
                meals = await uow.catalog_recipes.list_selection_candidates()
            selected = self._select_recipes(
                meals,
                user_id=command.user_id,
                week_start_date=command.week_start_date.isoformat(),
                daily_calories=command.daily_calories,
                preferences=command.preferences,
            )
            recipe_ids = {
                (item.day_index, item.slot_index): item.recipe_id for item in selected
            }
            async with self.uow_factory() as uow:
                version = await uow.catalog_recipes.lock_catalog_publication(
                    shared=True
                )
                if (version.selection, version.ingredients) != (
                    captured_version.selection,
                    captured_version.ingredients,
                ):
                    if attempt == 0:
                        continue
                    raise ConflictException(
                        "Catalog changed during generation; retry",
                        error_code="WEEKLY_CATALOG_CHANGED",
                    )
                await uow.weekly_meal_plans.lock_user_week(
                    user_id=command.user_id, week_start_date=command.week_start_date
                )
                reservation = await uow.meal_write_operations.reserve(
                    user_id=command.user_id,
                    operation="weekly_meal_plan_generate",
                    idempotency_key=command.idempotency_key,
                    request_fingerprint=fingerprint,
                )
                if (
                    reservation.state == "fingerprint_conflict"
                    and reservation.request_fingerprint == _legacy_fingerprint(command)
                ):
                    reservation = await uow.meal_write_operations.adopt_fingerprint(
                        reservation, request_fingerprint=fingerprint
                    )
                if reservation.state == "replay":
                    replay = await uow.weekly_meal_plans.get_by_id(
                        user_id=command.user_id, plan_id=reservation.target_meal_id
                    )
                    if replay is None:
                        raise ConflictException(
                            "Weekly plan replay is missing its plan",
                            error_code="IDEMPOTENCY_REPLAY_INVALID",
                        )
                    if _plan_has_assigned_recipe(replay):
                        return replay
                    reservation = await uow.meal_write_operations.reopen_completed(
                        reservation
                    )
                self._check_reservation(reservation)
                current = await uow.weekly_meal_plans.get_current(
                    user_id=command.user_id, week_start_date=command.week_start_date
                )
                if _generation_state(current) != captured_state:
                    raise WeeklyMealPlanConflictError(
                        "Weekly meal plan changed since selection",
                        error_code="WEEKLY_PLAN_STALE_REVISION",
                    )
                if current is None:
                    plan = await uow.weekly_meal_plans.create(
                        user_id=command.user_id,
                        week_start_date=command.week_start_date,
                        timezone=command.timezone,
                        preferences=command.preferences,
                        daily_calories=command.daily_calories,
                        catalog_revision=_revision_key(revision),
                        recipe_ids=recipe_ids,
                    )
                else:
                    logged = {
                        (slot.day_index, slot.slot_index)
                        for slot in current.slots
                        if slot.is_logged
                    }
                    plan = await uow.weekly_meal_plans.update(
                        user_id=command.user_id,
                        plan_id=current.id,
                        people=command.preferences.people,
                        preferences=command.preferences,
                        slots={
                            key: value
                            for key, value in recipe_ids.items()
                            if key not in logged
                        },
                        expected_revision=current.revision,
                    )
                await uow.meal_write_operations.complete(
                    reservation, target_meal_id=plan.id, response={"plan_id": plan.id}
                )
                await _enqueue_plan_preparation(uow, plan)
                return plan
        raise AssertionError("generation retry exhausted")

    @planner_timed("service")
    async def update(self, command: UpdateWeeklyMealPlanCommand) -> WeeklyMealPlan:
        fingerprint = _fingerprint(command)
        async with self.uow_factory() as uow:
            if os.getenv("CATALOG_PUBLICATION_FENCING_ENABLED", "false").lower() in {
                "true",
                "1",
            }:
                await uow.catalog_recipes.lock_catalog_publication(shared=True)
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
                current = await uow.weekly_meal_plans.get_by_id(
                    user_id=command.user_id, plan_id=command.plan_id
                )
                if current is None:
                    raise ResourceNotFoundException("Weekly meal plan not found")
                preferences = command.preferences or current.preferences
                hard_preferences_changed = _hard_preferences_changed(
                    current.preferences, preferences
                )
                expected_revision = (
                    command.expected_revision
                    if command.expected_revision is not None
                    else current.revision
                )
                if command.slots or hard_preferences_changed:
                    requested_ids = {
                        recipe_id
                        for recipe_id in (command.slots or {}).values()
                        if recipe_id is not None
                    }
                    if hard_preferences_changed:
                        requested_ids.update(
                            slot.recipe_id
                            for slot in current.slots
                            if not slot.is_logged and slot.recipe_id is not None
                        )
                    meals = await uow.catalog_recipes.get_meals(sorted(requested_ids))
                    meals_by_id = {meal.id: meal for meal in meals}
                    slots_by_coordinate = {
                        (slot.day_index, slot.slot_index): slot
                        for slot in current.slots
                    }
                    proposed_recipe_ids = {
                        coordinate: slot.recipe_id
                        for coordinate, slot in slots_by_coordinate.items()
                    }
                    for coordinate, recipe_id in (command.slots or {}).items():
                        try:
                            ensure_slot_coordinate(*coordinate)
                        except ValueError as exc:
                            raise ValidationException(
                                "slot coordinate is invalid",
                                error_code="WEEKLY_SLOT_INVALID",
                            ) from exc
                        proposed_recipe_ids[coordinate] = recipe_id
                        if recipe_id is None:
                            continue
                        meal = meals_by_id.get(recipe_id)
                        if meal is None:
                            raise ValidationException(
                                "recipe_id is not an active catalog recipe",
                                error_code="RECIPE_NOT_FOUND",
                            )
                        if not self.generator.supports_slot(meal, coordinate[1]):
                            raise ValidationException(
                                "recipe is not suitable for the selected meal slot",
                                error_code="RECIPE_SLOT_INELIGIBLE",
                            )
                        if not self.generator.is_hard_eligible(meal, preferences):
                            raise ValidationException(
                                "recipe conflicts with saved diet, allergy, or dislike preferences",
                                error_code="RECIPE_INELIGIBLE",
                            )
                    if hard_preferences_changed:
                        for coordinate, recipe_id in proposed_recipe_ids.items():
                            slot = slots_by_coordinate[coordinate]
                            if slot.is_logged or recipe_id is None:
                                continue
                            meal = meals_by_id.get(recipe_id)
                            if meal is None or not self.generator.is_hard_eligible(
                                meal, preferences
                            ):
                                raise ValidationException(
                                    "preference change would leave an unlogged meal in conflict",
                                    error_code="PLAN_PREFERENCE_CONFLICT",
                                )
                            if not self.generator.supports_slot(meal, coordinate[1]):
                                raise ValidationException(
                                    "preference change would leave a meal in the wrong slot",
                                    error_code="PLAN_PREFERENCE_CONFLICT",
                                )
                plan = await uow.weekly_meal_plans.update(
                    user_id=command.user_id,
                    plan_id=command.plan_id,
                    people=command.people,
                    preferences=command.preferences,
                    status=command.status,
                    slots=command.slots,
                    expected_revision=expected_revision,
                )
                if plan is None:
                    raise ResourceNotFoundException("Weekly meal plan not found")
                await uow.meal_write_operations.complete(
                    reservation, target_meal_id=plan.id, response={"plan_id": plan.id}
                )
                await _enqueue_plan_preparation(uow, plan)
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

    @planner_timed("service")
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
            compact_candidates = getattr(
                uow.catalog_recipes, "list_selection_candidates", None
            )
            if (
                self.ai_adjustment_provider is not None
                and _projections_enabled()
                and callable(compact_candidates)
            ):
                meals = await compact_candidates()
            else:
                meals = await uow.catalog_recipes.list_active_meals()
            profile = await uow.users.get_profile(command.user_id)
            profile_allergies = _profile_values(profile, "allergies")
            known_allergen_codes = ()
            list_allergen_codes = getattr(
                uow.catalog_recipes, "list_allergen_codes", None
            )
            if (
                profile_allergies
                or re.search(r"\ballerg(?:ic|y|ies)\b", prompt, re.IGNORECASE)
            ) and callable(list_allergen_codes):
                known_allergen_codes = tuple(await list_allergen_codes())
        target = _target_coordinate(command)
        slots_by_coordinate = {
            (slot.day_index, slot.slot_index): slot for slot in plan.slots
        }
        if target is not None:
            target_slot = slots_by_coordinate.get(target)
            if target_slot is None:
                raise ValidationException(
                    "Target meal slot is invalid", error_code="AI_TARGET_INVALID"
                )
            if target_slot.is_logged:
                raise ValidationException(
                    "A logged meal cannot be replaced",
                    error_code="AI_TARGET_LOGGED",
                )
        meals = tuple(meals)
        request_preferences = _preferences_from_prompt(
            plan.preferences, prompt, meals, known_allergen_codes
        )
        preferences, profile_dietary_preferences = _with_profile_context(
            plan.preferences,
            request_preferences,
            profile,
            meals,
            request_explicitly_sets_diet=request_preferences.diet
            != plan.preferences.diet,
            known_allergen_codes=known_allergen_codes,
        )
        proposal_preferences = (
            request_preferences if target is None else plan.preferences
        )
        if self.ai_adjustment_provider is not None:
            return await self._provider_proposal(
                command=command,
                plan=plan,
                meals=meals,
                preferences=preferences,
                proposal_preferences=proposal_preferences,
                profile_dietary_preferences=profile_dietary_preferences,
                target=target,
            )
        if target is not None:
            old_slot = slots_by_coordinate[target]
            candidates = [
                meal
                for meal in meals
                if meal.id != old_slot.recipe_id
                and self.generator.is_hard_eligible(meal, preferences)
                and self.generator.supports_slot(meal, target[1])
            ]
            preferred_candidates = [
                meal
                for meal in candidates
                if self.generator.is_soft_eligible(meal, preferences)
            ]
            if preferred_candidates:
                candidates = preferred_candidates
            candidates.sort(
                key=lambda meal: (
                    meal.popularity_rank
                    if meal.popularity_rank is not None
                    else 2_147_483_647,
                    meal.id,
                )
            )
            if not candidates:
                raise _no_eligible_replacement()
            replacement_id = candidates[0].id
            proposed_slots = tuple(
                _slot_with_recipe(slot, replacement_id)
                if (slot.day_index, slot.slot_index) == target
                else slot
                for slot in plan.slots
            )
        else:
            selected = self._select_recipes(
                meals,
                user_id=command.user_id,
                week_start_date=f"{plan.week_start_date.isoformat()}:{prompt.casefold()}",
                daily_calories=plan.daily_calories or 2000,
                preferences=preferences,
            )
            recipe_ids = {
                (item.day_index, item.slot_index): item.recipe_id for item in selected
            }
            proposed_slots = tuple(
                _slot_with_recipe(
                    slot,
                    slot.recipe_id
                    if slot.is_logged
                    else recipe_ids.get((slot.day_index, slot.slot_index)),
                )
                for slot in plan.slots
            )
        if target is None and _hard_preferences_changed(plan.preferences, preferences):
            self._validate_proposed_hard_preferences(proposed_slots, meals, preferences)
        changes = tuple(
            {
                "day_index": before.day_index,
                "slot_index": before.slot_index,
                "previous_recipe_id": before.recipe_id,
                "new_recipe_id": after.recipe_id,
                "action": "replace" if after.recipe_id else "clear",
            }
            for before, after in zip(plan.slots, proposed_slots, strict=True)
            if before.recipe_id != after.recipe_id
        )
        _validate_proposal_outcome(
            plan=plan,
            proposed_slots=proposed_slots,
            changes=changes,
            target=target,
            prompt=prompt,
            proposal_preferences=proposal_preferences,
        )
        proposed = plan.__class__(
            id=plan.id,
            user_id=plan.user_id,
            week_start_date=plan.week_start_date,
            status=plan.status,
            people=proposal_preferences.people,
            preferences=proposal_preferences,
            timezone=plan.timezone,
            slots=proposed_slots,
            daily_calories=plan.daily_calories,
            catalog_revision=plan.catalog_revision,
            algorithm_version=plan.algorithm_version,
            revision=plan.revision,
            created_at=plan.created_at,
            updated_at=plan.updated_at,
        )
        return WeeklyPlanAiProposal(
            base_revision=plan.revision,
            explanation=planner_copy(command.language)[0],
            diff_summary=planner_copy(command.language, len(changes))[1],
            proposed_plan=proposed,
            slot_changes=changes,
            proposed_groceries=self.grocery_service.project(proposed, meals),
        )

    async def _provider_proposal(self, **kwargs):
        with (
            planner_deadline(monotonic() + remaining_budget(25)),
            planner_retry_budget(),
        ):
            return await self._bounded_provider_proposal(**kwargs)

    async def _bounded_provider_proposal(self, **kwargs):
        try:
            return await self._validated_provider_proposal(**kwargs)
        except ValidationException as exc:
            if exc.error_code not in {
                "AI_NO_ELIGIBLE_REPLACEMENT",
                "AI_NO_CHANGES",
                "AI_OUTPUT_INCOMPLETE",
                "AI_OUTPUT_INELIGIBLE",
                "AI_CONSTRAINT_UNSATISFIED",
            }:
                raise
            first = _ai_shortlist(
                self.generator,
                kwargs["command"].prompt,
                kwargs["meals"],
                kwargs["preferences"],
                kwargs["target"],
            )
            wider = _ai_shortlist(
                self.generator,
                kwargs["command"].prompt,
                kwargs["meals"],
                kwargs["preferences"],
                kwargs["target"],
                widened=True,
            )
            if {m.id for m in first} == {m.id for m in wider}:
                raise
            try:
                budget = remaining_budget(25)
            except TimeoutError:
                raise exc from None
            if budget < 2:
                raise
            return await self._validated_provider_proposal(**kwargs, widened=True)

    async def _validated_provider_proposal(
        self,
        *,
        command,
        plan,
        meals,
        preferences,
        proposal_preferences,
        profile_dietary_preferences,
        target,
        widened=False,
    ):
        try:
            response = await self.ai_adjustment_provider.propose(
                prompt=command.prompt,
                plan=plan,
                meals=_ai_shortlist(
                    self.generator,
                    command.prompt,
                    meals,
                    preferences,
                    target,
                    widened=widened,
                ),
                preferences=preferences,
                profile_dietary_preferences=profile_dietary_preferences,
                target_day_index=target[0] if target else None,
                target_slot_index=target[1] if target else None,
                language=command.language,
                current_meals=tuple(
                    meal
                    for meal in meals
                    if meal.id in {slot.recipe_id for slot in plan.slots}
                ),
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
        try:
            ensure_base_revision(response.base_revision, plan.revision)
        except ValueError as exc:
            raise ValidationException(
                "AI proposal was generated from a stale weekly meal plan",
                error_code="AI_OUTPUT_STALE_REVISION",
            ) from exc
        available_ids = {meal.id for meal in meals}
        by_coordinate = {(slot.day_index, slot.slot_index): slot for slot in plan.slots}
        response_changes = response.slot_changes
        if target is not None:
            current_recipe_id = by_coordinate[target].recipe_id
            if _target_response_is_noop(response_changes, target, current_recipe_id):
                requested_recipe = _requested_catalog_recipe(command.prompt, meals)
                if (
                    requested_recipe is not None
                    and requested_recipe.id != current_recipe_id
                ):
                    response_changes = (
                        WeeklyMealPlanSlotAdjustment(
                            day_index=target[0],
                            slot_index=target[1],
                            action="replace",
                            new_recipe_id=requested_recipe.id,
                        ),
                    )
        proposed = list(plan.slots)
        changes = []
        seen = set()
        for change in response_changes:
            try:
                ensure_slot_coordinate(change.day_index, change.slot_index)
            except ValueError as exc:
                raise ValidationException(
                    "AI returned duplicate or invalid weekly slot coordinates",
                    error_code="AI_OUTPUT_INVALID",
                ) from exc
            coordinate = (change.day_index, change.slot_index)
            if coordinate in seen or coordinate not in by_coordinate:
                raise ValidationException(
                    "AI returned duplicate or invalid weekly slot coordinates",
                    error_code="AI_OUTPUT_INVALID",
                )
            if target is not None and coordinate != target:
                raise ValidationException(
                    "AI changed a meal outside the requested slot",
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
            invalid_action = (
                (change.action == "replace" and change.new_recipe_id is None)
                or (change.action == "clear" and change.new_recipe_id is not None)
                or (target is not None and change.action != "replace")
            )
            if invalid_action:
                raise ValidationException(
                    "AI returned an inconsistent recipe action",
                    error_code="AI_OUTPUT_INVALID",
                )
            if new_recipe_id is not None and new_recipe_id not in available_ids:
                raise ValidationException(
                    "AI returned a recipe outside the active catalog",
                    error_code="AI_OUTPUT_INVALID",
                )
            if new_recipe_id is not None:
                meal = next(meal for meal in meals if meal.id == new_recipe_id)
                if not self.generator.is_hard_eligible(meal, preferences):
                    raise ValidationException(
                        "AI returned a recipe that conflicts with saved preferences",
                        error_code="AI_OUTPUT_INELIGIBLE",
                    )
                if not self.generator.supports_slot(meal, coordinate[1]):
                    raise ValidationException(
                        "AI returned a recipe for the wrong meal type",
                        error_code="AI_OUTPUT_INELIGIBLE",
                    )
            if change.action == "clear" and not _prompt_explicitly_requests_clear(
                command.prompt
            ):
                raise ValidationException(
                    "A meal replacement cannot leave a slot empty; no eligible recipe was returned",
                    error_code="AI_OUTPUT_INCOMPLETE",
                )
            if new_recipe_id == current.recipe_id:
                continue
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
            people=proposal_preferences.people,
            preferences=proposal_preferences,
            timezone=plan.timezone,
            slots=tuple(proposed),
            daily_calories=plan.daily_calories,
            catalog_revision=plan.catalog_revision,
            algorithm_version=plan.algorithm_version,
            revision=plan.revision,
            created_at=plan.created_at,
            updated_at=plan.updated_at,
        )
        if target is None and _hard_preferences_changed(plan.preferences, preferences):
            self._validate_proposed_hard_preferences(
                proposed_plan.slots, meals, preferences
            )
        _validate_proposal_outcome(
            plan=plan,
            proposed_slots=proposed_plan.slots,
            changes=changes,
            target=target,
            prompt=command.prompt,
            proposal_preferences=proposal_preferences,
        )
        explanation = response.explanation
        if proposal_preferences != plan.preferences:
            explanation += planner_copy(command.language)[2]
        return WeeklyPlanAiProposal(
            base_revision=plan.revision,
            explanation=explanation,
            diff_summary=response.diff_summary
            or planner_copy(command.language, len(changes))[1],
            proposed_plan=proposed_plan,
            slot_changes=tuple(changes),
            proposed_groceries=await self._proposal_groceries(proposed_plan, meals),
        )

    async def _proposal_groceries(self, plan, meals):
        if _projections_enabled():
            # Compact candidates carry constraint/nutrition features. Only the
            # fourteen selected recipes need canonical ingredient hydration.
            async with self.uow_factory() as uow:
                meals = await uow.catalog_recipes.get_meals(
                    tuple(
                        sorted(
                            {slot.recipe_id for slot in plan.slots if slot.recipe_id}
                        )
                    )
                )
        return self.grocery_service.project(plan, meals)

    def _validate_proposed_hard_preferences(self, slots, meals, preferences) -> None:
        meals_by_id = {meal.id: meal for meal in meals}
        for slot in slots:
            if slot.is_logged or slot.recipe_id is None:
                continue
            meal = meals_by_id.get(slot.recipe_id)
            if meal is None or not self.generator.is_hard_eligible(meal, preferences):
                raise ValidationException(
                    "preference change would leave an unlogged meal in conflict",
                    error_code="PLAN_PREFERENCE_CONFLICT",
                )
            if not self.generator.supports_slot(meal, slot.slot_index):
                raise ValidationException(
                    "preference change would leave a meal in the wrong slot",
                    error_code="PLAN_PREFERENCE_CONFLICT",
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
        key: _fingerprint_value(value)
        for key, value in vars(command).items()
        if key != "idempotency_key"
    }
    return canonicalize_fingerprint(payload)


def _legacy_fingerprint(command) -> str:
    """Hash used when dataclass fields were stringified instead of expanded."""

    payload = {
        key: value for key, value in vars(command).items() if key != "idempotency_key"
    }
    return canonicalize_fingerprint(payload)


def _plan_has_assigned_recipe(plan: WeeklyMealPlan) -> bool:
    return any(slot.recipe_id for slot in plan.slots)


def _fingerprint_value(value):
    if isinstance(value, dict):
        return {
            (
                ":".join(str(part) for part in key) if isinstance(key, tuple) else key
            ): _fingerprint_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)) and not isinstance(value, str):
        return [_fingerprint_value(item) for item in value]
    if hasattr(value, "__dataclass_fields__"):
        return {
            field: _fingerprint_value(getattr(value, field))
            for field in value.__dataclass_fields__
        }
    return value


def _revision_key(revision) -> str:
    return ":".join(
        str(value or "")
        for value in (
            revision.active_count,
            revision.catalog_updated_at,
            revision.food_reference_updated_at,
        )
    )


def _hard_preferences_changed(
    before: WeeklyMealPlanPreferences, after: WeeklyMealPlanPreferences
) -> bool:
    return (
        before.diet != after.diet
        or before.dislikes != after.dislikes
        or before.allergies != after.allergies
    )


def _no_eligible_replacement() -> ValidationException:
    return ValidationException(
        "No eligible replacement is available for this meal. Try another preference or choose a recipe manually.",
        error_code="AI_NO_ELIGIBLE_REPLACEMENT",
    )


def _target_response_is_noop(changes, target, current_recipe_id) -> bool:
    if not changes:
        return True
    return (
        len(changes) == 1
        and (changes[0].day_index, changes[0].slot_index) == target
        and changes[0].action == "replace"
        and changes[0].new_recipe_id == current_recipe_id
    )


def _requested_catalog_recipe(prompt: str, meals):
    normalized_prompt = _normalize_recipe_request(prompt)
    if not normalized_prompt:
        return None
    prompt_text = f" {normalized_prompt} "
    matches = [
        meal
        for meal in meals
        if (normalized_name := _normalize_recipe_request(meal.name))
        and _has_non_excluded_recipe_mention(prompt_text, normalized_name)
    ]
    return matches[0] if len(matches) == 1 else None


def _has_non_excluded_recipe_mention(prompt_text: str, normalized_name: str) -> bool:
    pattern = re.compile(rf"(?<![a-z0-9]){re.escape(normalized_name)}(?![a-z0-9])")
    for match in pattern.finditer(prompt_text):
        prefix = prompt_text[: match.start()]
        if re.search(
            r"\b(?:no|not|never|avoid|without|except|other than|instead of|do not|don t)"
            r"(?:\s+(?:to|want|use|choose|select|suggest|recommend|include|serve|eat))*\s*$",
            prefix,
        ):
            continue
        return True
    return False


def _normalize_recipe_request(value: str) -> str:
    folded = (
        unicodedata.normalize("NFKD", value)
        .encode("ascii", "ignore")
        .decode("ascii")
        .casefold()
    )
    return " ".join(re.findall(r"[a-z0-9]+", folded))


def _profile_values(profile, field: str) -> tuple[str, ...]:
    values = getattr(profile, field, None) if profile is not None else None
    if not isinstance(values, (list, tuple, set)):
        return ()
    return tuple(
        sorted(
            {
                _normalize_preference_text(value)
                for value in values
                if _normalize_preference_text(value)
            }
        )
    )


def _normalize_preference_text(value: object) -> str:
    return (
        unicodedata.normalize("NFKD", str(value))
        .encode("ascii", "ignore")
        .decode("ascii")
        .casefold()
        .replace("_", " ")
        .replace("-", " ")
        .strip()
    )


def _with_profile_context(
    plan_preferences,
    request_preferences,
    profile,
    meals,
    *,
    request_explicitly_sets_diet: bool,
    known_allergen_codes: tuple[str, ...] = (),
):
    # Saved profile diets are advisory context; strict diet constraints live in
    # plan preferences or explicit requests parsed by _preferences_from_prompt.
    profile_dietary_preferences = _profile_values(profile, "dietary_preferences")

    allergies = set(request_preferences.allergies)
    allergies.update(
        _profile_allergy_codes(
            _profile_values(profile, "allergies"), meals, known_allergen_codes
        )
    )
    diet = request_preferences.diet
    if (
        plan_preferences.diet == "any"
        and not request_explicitly_sets_diet
        and diet == "any"
    ):
        if "vegetarian" in profile_dietary_preferences:
            diet = "vegetarian"
        elif "no pork" in profile_dietary_preferences:
            diet = "no-pork"
    return (
        WeeklyMealPlanPreferences(
            people=request_preferences.people,
            diet=diet,
            cooking_time=request_preferences.cooking_time,
            cuisine=request_preferences.cuisine,
            dislikes=request_preferences.dislikes,
            allergies=tuple(sorted(allergies)),
        ),
        profile_dietary_preferences,
    )


def _profile_allergy_codes(
    values: tuple[str, ...], meals, known_allergen_codes: tuple[str, ...] = ()
) -> tuple[str, ...]:
    known_codes = list(known_allergen_codes)
    # Include observed codes for compatibility with catalog adapters that do not
    # expose the global allergen reference.
    for meal in meals:
        for raw_code in getattr(meal, "allergen_codes", ()):
            code = str(raw_code).strip()
            if code:
                known_codes.append(code)
    resolved = resolve_allergen_preferences(values, known_codes)
    if resolved is None:
        raise ValidationException(
            "A saved allergy cannot be checked against the available recipe data",
            error_code="AI_PROFILE_ALLERGY_UNSUPPORTED",
        )
    return resolved


def _validate_proposal_outcome(
    *, plan, proposed_slots, changes, target, prompt: str, proposal_preferences
) -> None:
    if any(
        change["action"] == "clear" for change in changes
    ) and not _prompt_explicitly_requests_clear(prompt):
        raise ValidationException(
            "A replacement proposal cannot leave a meal slot empty",
            error_code="AI_OUTPUT_INCOMPLETE",
        )
    if target is not None and not any(
        (change["day_index"], change["slot_index"]) == target
        and change["action"] == "replace"
        for change in changes
    ):
        raise _no_eligible_replacement()
    if target is None and not changes and proposal_preferences == plan.preferences:
        raise ValidationException(
            "No meal changes were proposed. The plan may already match that request.",
            error_code="AI_NO_CHANGES",
        )
    if _prompt_requires_unique_recipes(prompt):
        if target is not None:
            target_slot = next(
                slot
                for slot in proposed_slots
                if (slot.day_index, slot.slot_index) == target
            )
            if target_slot.recipe_id is not None and any(
                slot.recipe_id == target_slot.recipe_id
                for slot in proposed_slots
                if (slot.day_index, slot.slot_index) != target
            ):
                raise ValidationException(
                    "The available recipes cannot satisfy the no-repeat request for this meal",
                    error_code="AI_CONSTRAINT_UNSATISFIED",
                )
            return
        seen: set[str] = set()
        for slot in proposed_slots:
            if slot.recipe_id is None:
                continue
            if slot.recipe_id in seen and not slot.is_logged:
                raise ValidationException(
                    "The available recipes cannot satisfy the no-repeat request for this plan",
                    error_code="AI_CONSTRAINT_UNSATISFIED",
                )
            seen.add(slot.recipe_id)


def _prompt_requires_unique_recipes(prompt: str) -> bool:
    lowered = _normalize_preference_text(prompt)
    return bool(
        re.search(
            r"\b(?:no|without|avoid)\s+(?:any\s+)?repeats?\b|\bdo not repeat\b|\bdon t repeat\b",
            lowered,
        )
    )


def _prompt_explicitly_requests_clear(prompt: str) -> bool:
    lowered = _normalize_preference_text(prompt)
    lowered = re.sub(
        r"\b(?:dont|do not|never)\s+(?:clear|remove|delete)\b", "", lowered
    )
    return bool(
        re.search(
            r"\b(?:clear|remove|delete)\s+(?:the\s+)?(?:meal|slot|breakfast|lunch|dinner|it|this)\b|\bopen slot\b|\bleave\b.{0,20}\b(?:open|empty)\b|bo trong|xoa bua",
            lowered,
        )
    )


def _preferences_from_prompt(
    current: WeeklyMealPlanPreferences,
    prompt: str,
    meals=(),
    known_allergen_codes: tuple[str, ...] = (),
) -> WeeklyMealPlanPreferences:
    lowered = (
        unicodedata.normalize("NFKD", prompt)
        .encode("ascii", "ignore")
        .decode("ascii")
        .casefold()
    )
    says_vegan = bool(re.search(r"\bvegan\b", lowered)) and not bool(
        re.search(r"\b(?:not|is not|isn't|never)\s+vegan\b", lowered)
    )
    if says_vegan:
        raise ValidationException(
            "Vegan preferences are not supported yet; choose vegetarian or another available option",
            error_code="AI_PREFERENCE_UNSUPPORTED",
        )
    says_vegetarian = any(
        re.search(rf"\b{re.escape(term)}\b", lowered)
        for term in ("vegetarian", "meat-free", "meatless", "chay")
    )
    says_no_pork = any(
        term in lowered
        for term in (
            "no pork",
            "without pork",
            "avoid pork",
            "khong an thit heo",
            "khong heo",
            "khong lon",
        )
    )
    diet = (
        "vegetarian"
        if says_vegetarian
        else "no-pork"
        if says_no_pork and current.diet != "vegetarian"
        else current.diet
    )
    cooking_time = (
        "30"
        if "30" in lowered
        or "quick" in lowered
        or "fast" in lowered
        or "nhanh" in lowered
        else current.cooking_time
    )
    allergies = set(current.allergies)
    allergy_statement = _has_positive_allergy_statement(lowered)
    allergen_codes = {
        str(code).strip()
        for meal in meals
        for code in getattr(meal, "allergen_codes", ())
    }
    allergen_codes.update(str(code).strip() for code in known_allergen_codes)
    allergen_codes.update(
        {
            "peanut",
            "tree_nut",
            "milk",
            "egg",
            "soy",
            "wheat",
            "gluten",
            "fish",
            "shellfish",
            "sesame",
        }
    )
    if allergy_statement:
        resolved_allergies: set[str] = set()
        allergy_subjects = _explicit_allergy_subjects(lowered)
        if not allergy_subjects:
            raise ValidationException(
                "A requested allergy cannot be checked against the available recipe data",
                error_code="AI_PREFERENCE_ALLERGY_UNSUPPORTED",
            )
        for subject in allergy_subjects:
            resolved = resolve_allergen_preferences((subject,), allergen_codes)
            if resolved is None:
                raise ValidationException(
                    "A requested allergy cannot be checked against the available recipe data",
                    error_code="AI_PREFERENCE_ALLERGY_UNSUPPORTED",
                )
            resolved_allergies.update(resolved)
        if not resolved_allergies:
            raise ValidationException(
                "A requested allergy cannot be checked against the available recipe data",
                error_code="AI_PREFERENCE_ALLERGY_UNSUPPORTED",
            )
        allergies.update(resolved_allergies)
    dislikes = set(current.dislikes)
    ingredient_terms = {
        ingredient.display_name.strip().casefold()
        for meal in meals
        for ingredient in getattr(meal, "ingredients", ())
        if ingredient.display_name and len(ingredient.display_name.strip()) <= 80
    }
    ingredient_terms.update(allergen_codes)
    for term in ingredient_terms:
        variants = {term, f"{term}s" if not term.endswith("s") else term[:-1]}
        for variant in variants:
            if re.search(
                rf"\b(?:no|without|avoid|exclude|skip|leave out|free of)\s+(?:any\s+)?{re.escape(variant)}\b|\b{re.escape(variant)}[- ]free\b",
                lowered,
            ):
                dislikes.add(term)
                break
    return WeeklyMealPlanPreferences(
        people=current.people,
        diet=diet,
        cooking_time=cooking_time,
        cuisine=current.cuisine,
        dislikes=tuple(sorted(dislikes)),
        allergies=tuple(sorted(allergies)),
    )


def _explicit_allergy_subjects(prompt: str) -> tuple[str, ...]:
    parsed: list[str] = []
    subject_matches = [
        (match.start(), match.group(1), True)
        for match in re.finditer(
            r"\ballerg(?:ic|y|ies)\s+(?:(?:to|for|is|are|include|includes)\s+)?([^.!?;]+)",
            prompt,
        )
    ]
    subject_matches.extend(
        (match.end() - len("allergy"), match.group(1), True)
        for match in re.finditer(r"\b((?:[a-z0-9_'-]+\s+){1,6})allergy\b", prompt)
    )
    subject_matches.extend(
        (match.start(), match.group(1), True)
        for match in re.finditer(r"\ballergy\s*(?::|is)\s*([^.!?;]+)", prompt)
    )
    subject_matches.extend(
        (match.start(1), match.group(1), False)
        for match in _NO_ALLERGY_EXCEPTION_PATTERN.finditer(prompt)
    )
    for allergy_position, subject, check_negation in subject_matches:
        if check_negation and _is_negated_allergy_mention(prompt, allergy_position):
            continue
        cleaned = re.sub(r"^(?:i am|i'm|i have|i've got|my)\s+", "", subject.strip())
        cleaned = re.sub(r"^(?:a|an|the|food)\s+", "", cleaned)
        for part in re.split(r"\s*(?:,|;|\band\b|\bor\b|\bbut\b)\s*", cleaned):
            part = re.sub(r"^(?:and|or|but)\s+", "", part.strip())
            if re.match(
                r"^(?:but|please|so|because|avoid|no|not|without|change|switch|make|give|show|find|replace|recommend|suggest|prefer|like|enjoy|hate|dislike|could|can|don't like|do not like|don't want|do not want|don't eat|do not eat|i am|i'm|i have|i've got|i want|i prefer|i like|i enjoy|i hate|i dislike|i don't like|i do not like|i don't want|i do not want|i don't eat|i do not eat|i avoid|i would|i'd like|i can|i will|i need|i can't stand|i cannot stand)\b",
                part,
            ):
                break
            if part:
                parsed.append(part)
    return tuple(parsed)


def _has_positive_allergy_statement(prompt: str) -> bool:
    return bool(_NO_ALLERGY_EXCEPTION_PATTERN.search(prompt)) or any(
        not _is_negated_allergy_mention(prompt, match.start())
        for match in re.finditer(r"\ballerg(?:ic|y|ies)\b", prompt)
    )


def _is_negated_allergy_mention(prompt: str, position: int) -> bool:
    prefix = prompt[:position]
    boundaries = [
        match.end() for match in re.finditer(r"[.!?;]|\b(?:but|and)\b", prefix)
    ]
    clause_prefix = prefix[max(boundaries, default=0) :]
    return bool(
        re.search(
            r"\b(?:not|isn't|is\s+not|never|no\s+longer|no|don't\s+have|do\s+not\s+have)(?:\s+\w+){0,2}\s*$",
            clause_prefix,
        )
    )


_NO_ALLERGY_EXCEPTION_PATTERN = re.compile(
    r"\b(?:no\s+allerg(?:y|ies)|(?:don't|do\s+not)\s+have\s+(?:any\s+)?allerg(?:y|ies))\b"
    r"\s*(?:,|;)?\s*(?:except(?:\s+for)?|other\s+than|besides)\s+([^.!?;]+)"
)


def _target_coordinate(command: AiAdjustMealPlanCommand) -> tuple[int, int] | None:
    day_index = command.target_day_index
    slot_index = command.target_slot_index
    if (day_index is None) != (slot_index is None):
        raise ValidationException(
            "Target day and meal slot must be provided together",
            error_code="AI_TARGET_INVALID",
        )
    if day_index is None or slot_index is None:
        return None
    try:
        ensure_slot_coordinate(day_index, slot_index)
    except ValueError as exc:
        raise ValidationException(
            "Target meal slot is invalid", error_code="AI_TARGET_INVALID"
        ) from exc
    return (day_index, slot_index)


def _slot_with_recipe(slot, recipe_id):
    return slot.__class__(
        id=slot.id,
        day_index=slot.day_index,
        slot_index=slot.slot_index,
        recipe_id=recipe_id,
        is_logged=slot.is_logged,
        logged_meal_id=slot.logged_meal_id,
        version=slot.version,
    )


def _generation_state(plan):
    if plan is None:
        return None
    return (
        plan.id,
        plan.revision,
        plan.preferences,
        tuple(
            (slot.id, slot.version, slot.recipe_id, slot.is_logged)
            for slot in plan.slots
        ),
    )


def _ai_shortlist(
    generator, prompt, meals, preferences, target, limit=40, *, widened=False
):
    eligible = [
        meal
        for meal in meals
        if generator.is_hard_eligible(meal, preferences)
        and (target is None or generator.supports_slot(meal, target[1]))
    ]
    named = _requested_catalog_recipe(prompt, eligible)
    eligible.sort(
        key=lambda meal: (
            0 if named is not None and named.id == meal.id else 1,
            0 if generator.is_soft_eligible(meal, preferences) else 1,
            meal.popularity_rank if meal.popularity_rank is not None else 2_147_483_647,
            meal.id,
        )
    )
    if widened:
        # A second bounded window reaches lower-ranked eligible recipes. Named
        # requests stay in both windows; every window preserves meal-type coverage.
        eligible = eligible[limit:] + eligible[:limit]
    selected = [named] if named is not None else []
    pools = (
        [meal for meal in eligible if generator.supports_slot(meal, slot)]
        for slot in ((target[1],) if target else range(WEEKLY_SLOTS_PER_DAY))
    )
    iterators = [iter(pool) for pool in pools]
    seen = {meal.id for meal in selected}
    while iterators and len(selected) < limit:
        active = []
        for iterator in iterators:
            for meal in iterator:
                if meal.id not in seen:
                    selected.append(meal)
                    seen.add(meal.id)
                    active.append(iterator)
                    break
            if len(selected) == limit:
                break
        iterators = active
    return tuple(selected)


async def _enqueue_plan_preparation(uow, plan):
    if os.getenv("CATALOG_DURABLE_PREPARATION_ENABLED", "false").lower() in {
        "true",
        "1",
    }:
        await uow.catalog_preparation.enqueue_for_recipes(
            tuple(sorted({slot.recipe_id for slot in plan.slots if slot.recipe_id}))
        )


def _projections_enabled():
    return os.getenv("CATALOG_PROJECTIONS_ENABLED", "false").lower() in {"true", "1"}
