import hashlib
import json
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.api.exceptions import ConflictException, ValidationException
from src.app.commands.meal_planner import (
    AiAdjustMealPlanCommand,
    GenerateWeeklyMealPlanCommand,
    UpdateWeeklyMealPlanCommand,
)
from src.app.services.weekly_meal_plan_service import (
    WeeklyMealPlanService,
    _fingerprint,
    _legacy_fingerprint,
)
from src.domain.model.meal_recommendation import CatalogMeal, CatalogMealIngredient
from src.domain.model.weekly_meal_planner import (
    WEEKLY_PLAN_SLOT_COUNT,
    WEEKLY_SLOTS_PER_DAY,
    WeeklyMealPlan,
    WeeklyMealPlanAdjustmentProposal,
    WeeklyMealPlanPreferences,
    WeeklyMealPlanSlot,
    WeeklyMealPlanSlotAdjustment,
    WeeklyMealPlanStatus,
)


def _meal(
    meal_id: str,
    name: str,
    *,
    meal_types=("breakfast", "lunch", "dinner"),
    rank=1,
    cook_time_minutes=None,
    allergen_codes=(),
):
    return CatalogMeal(
        id=meal_id,
        catalog_key=meal_id,
        content_hash="a" * 64,
        name=name,
        cuisine="vietnamese",
        description=None,
        image_url=None,
        protein_g=Decimal("30"),
        carbs_g=Decimal("40"),
        fat_g=Decimal("10"),
        fiber_g=Decimal("5"),
        meal_types=meal_types,
        popularity_rank=rank,
        cook_time_minutes=cook_time_minutes,
        allergen_codes=allergen_codes,
        ingredients=(
            CatalogMealIngredient(
                food_reference_id=1,
                display_name=name,
                quantity=Decimal("100"),
                unit="g",
            ),
        ),
    )


def _slot_offset(day: int, slot: int) -> int:
    return day * WEEKLY_SLOTS_PER_DAY + slot


def _plan(preferences=None, recipe_id="chicken"):
    slots = tuple(
        WeeklyMealPlanSlot(
            id=f"slot-{day}-{meal}",
            day_index=day,
            slot_index=meal,
            recipe_id=recipe_id,
            is_logged=(day == 0 and meal == 0),
            logged_meal_id="logged-meal" if day == 0 and meal == 0 else None,
        )
        for day in range(7)
        for meal in range(WEEKLY_SLOTS_PER_DAY)
    )
    return WeeklyMealPlan(
        id="plan-1",
        user_id="user-1",
        week_start_date=date(2026, 9, 21),
        status=WeeklyMealPlanStatus.DRAFT,
        people=1,
        preferences=preferences or WeeklyMealPlanPreferences(cuisine="vietnamese"),
        timezone="Asia/Ho_Chi_Minh",
        slots=slots,
        daily_calories=2000,
        revision=3,
    )


class _Uow:
    def __init__(self, plan, meals, profile=None):
        self.weekly_meal_plans = self
        self.catalog_recipes = self
        self.meal_write_operations = self
        self.users = self
        self.plan = plan
        self.meals = meals
        self.profile = profile
        self.is_open = False
        self.update_args = None

    async def __aenter__(self):
        self.is_open = True
        return self

    async def __aexit__(self, *_):
        self.is_open = False
        return False

    async def get_by_id(self, **_):
        return self.plan

    async def list_active_meals(self):
        return self.meals

    async def lock_user_week(self, **_):
        return None

    async def get_active_catalog_revision(self):
        return SimpleNamespace(
            active_count=2,
            catalog_updated_at="2026-09-26T00:00:00",
            food_reference_updated_at="2026-09-26T00:00:00",
        )

    async def get_current(self, **_):
        return self.plan

    async def get_profile(self, _user_id):
        return self.profile

    async def reserve(self, **_):
        return SimpleNamespace(state="reserved")

    async def complete(self, *_args, **_kwargs):
        return None

    async def release(self, *_args, **_kwargs):
        return None

    async def update(self, **kwargs):
        self.update_args = kwargs
        return self.plan


@pytest.mark.asyncio
async def test_generation_fills_unlogged_slots_without_replacing_logged_meals():
    empty_plan = _plan(recipe_id=None)
    uow = _Uow(
        empty_plan,
        (_meal("tofu", "Tofu"), _meal("lentils", "Lentil bowl")),
    )
    service = WeeklyMealPlanService(lambda: uow)

    await service.generate(
        GenerateWeeklyMealPlanCommand(
            user_id="user-1",
            week_start_date=date(2026, 9, 21),
            timezone="Asia/Ho_Chi_Minh",
            preferences=empty_plan.preferences,
            idempotency_key="initial-week-2026-09-21",
            daily_calories=2000,
        )
    )

    generated_coordinates = set(uow.update_args["slots"])
    assert len(generated_coordinates) == WEEKLY_PLAN_SLOT_COUNT - 1
    assert (0, 0) not in generated_coordinates


class _Provider:
    def __init__(self, slot_changes):
        self.slot_changes = slot_changes
        self.context = None
        self.uow = None
        self.uow_was_open = None

    async def propose(self, **kwargs):
        self.context = kwargs
        if self.uow is not None:
            self.uow_was_open = self.uow.is_open
        return WeeklyMealPlanAdjustmentProposal(
            explanation="Suggested a replacement",
            slot_changes=self.slot_changes,
            base_revision=kwargs["plan"].revision,
        )


def _service(plan, meals, provider=None, profile=None):
    return WeeklyMealPlanService(
        lambda: _Uow(plan, meals, profile), ai_adjustment_provider=provider
    )


@pytest.mark.asyncio
async def test_local_week_proposal_preserves_logged_slot_and_carries_prompt_preferences():
    service = _service(_plan(), (_meal("chicken", "Chicken"), _meal("tofu", "Tofu")))

    proposal = await service.ai_proposal(
        AiAdjustMealPlanCommand(
            user_id="user-1", plan_id="plan-1", prompt="Make this week vegetarian"
        )
    )

    assert proposal.proposed_plan.preferences.diet == "vegetarian"
    assert proposal.proposed_plan.slots[0].recipe_id == "chicken"
    assert proposal.proposed_plan.slots[0].is_logged
    assert all(slot.recipe_id == "tofu" for slot in proposal.proposed_plan.slots[1:])
    assert all(
        change["day_index"] != 0 or change["slot_index"] != 0
        for change in proposal.slot_changes
    )


@pytest.mark.asyncio
async def test_local_meal_proposal_changes_only_the_requested_slot():
    service = _service(_plan(), (_meal("chicken", "Chicken"), _meal("tofu", "Tofu")))

    proposal = await service.ai_proposal(
        AiAdjustMealPlanCommand(
            user_id="user-1",
            plan_id="plan-1",
            prompt="replace with tofu",
            target_day_index=2,
            target_slot_index=1,
        )
    )

    assert [
        (change["day_index"], change["slot_index"]) for change in proposal.slot_changes
    ] == [(2, 1)]
    changed = _slot_offset(2, 1)
    assert proposal.proposed_plan.slots[changed].recipe_id == "tofu"
    assert all(
        slot.recipe_id == "chicken"
        for index, slot in enumerate(proposal.proposed_plan.slots)
        if index != changed
    )
    assert proposal.proposed_groceries
    assert all(item.total_needed > 0 for item in proposal.proposed_groceries)


@pytest.mark.asyncio
async def test_provider_receives_saved_preferences_and_target_scope():
    provider = _Provider((WeeklyMealPlanSlotAdjustment(2, 1, "replace", "tofu"),))
    service = _service(
        _plan(WeeklyMealPlanPreferences(diet="vegetarian")),
        (_meal("chicken", "Chicken"), _meal("tofu", "Tofu")),
        provider,
    )

    proposal = await service.ai_proposal(
        AiAdjustMealPlanCommand(
            user_id="user-1",
            plan_id="plan-1",
            prompt="quick vegetarian",
            target_day_index=2,
            target_slot_index=1,
        )
    )

    assert provider.context["target_day_index"] == 2
    assert provider.context["target_slot_index"] == 1
    assert provider.context["preferences"].diet == "vegetarian"
    assert provider.context["preferences"].cooking_time == "30"
    assert proposal.proposed_plan.preferences.diet == "vegetarian"
    assert proposal.proposed_plan.preferences.cooking_time == "any"


@pytest.mark.asyncio
async def test_provider_runs_after_database_unit_of_work_closes():
    uow = _Uow(_plan(), (_meal("chicken", "Chicken"), _meal("tofu", "Tofu")))
    provider = _Provider((WeeklyMealPlanSlotAdjustment(2, 1, "replace", "tofu"),))
    provider.uow = uow
    service = WeeklyMealPlanService(lambda: uow, ai_adjustment_provider=provider)

    await service.ai_proposal(
        AiAdjustMealPlanCommand(
            user_id="user-1",
            plan_id="plan-1",
            prompt="replace this meal",
            target_day_index=2,
            target_slot_index=1,
        )
    )

    assert provider.uow_was_open is False
    assert uow.is_open is False


@pytest.mark.asyncio
async def test_profile_constraints_and_ranking_preferences_reach_target_proposal():
    profile = SimpleNamespace(
        dietary_preferences=["Vegetarian", "high_protein"],
        allergies=["peanut"],
    )
    provider = _Provider((WeeklyMealPlanSlotAdjustment(1, 1, "replace", "tofu"),))
    plan = _plan(WeeklyMealPlanPreferences(), recipe_id="chicken")
    service = _service(
        plan,
        (
            _meal("chicken", "Chicken"),
            _meal("tofu", "Tofu", allergen_codes=("soy",)),
            _meal("peanut", "Peanut salad", allergen_codes=("peanut",)),
        ),
        provider,
        profile,
    )

    proposal = await service.ai_proposal(
        AiAdjustMealPlanCommand(
            user_id="user-1",
            plan_id="plan-1",
            prompt="replace this dinner",
            target_day_index=1,
            target_slot_index=1,
        )
    )

    assert provider.context["preferences"].diet == "vegetarian"
    assert provider.context["preferences"].allergies == ("peanut",)
    assert provider.context["profile_dietary_preferences"] == (
        "high protein",
        "vegetarian",
    )
    assert proposal.proposed_plan.preferences == plan.preferences
    assert proposal.proposed_plan.slots[_slot_offset(1, 1)].recipe_id == "tofu"


@pytest.mark.asyncio
async def test_one_meal_prompt_does_not_validate_or_rewrite_other_slots():
    provider = _Provider((WeeklyMealPlanSlotAdjustment(1, 1, "replace", "tofu"),))
    plan = _plan(WeeklyMealPlanPreferences(), recipe_id="chicken")
    service = _service(
        plan, (_meal("chicken", "Chicken"), _meal("tofu", "Tofu")), provider
    )

    proposal = await service.ai_proposal(
        AiAdjustMealPlanCommand(
            user_id="user-1",
            plan_id="plan-1",
            prompt="make this meal vegetarian",
            target_day_index=1,
            target_slot_index=1,
        )
    )

    assert proposal.proposed_plan.preferences == plan.preferences
    assert proposal.proposed_plan.slots[_slot_offset(1, 1)].recipe_id == "tofu"
    assert proposal.proposed_plan.slots[_slot_offset(1, 0)].recipe_id == "chicken"


@pytest.mark.asyncio
async def test_provider_clear_is_rejected_for_a_replacement_request():
    provider = _Provider((WeeklyMealPlanSlotAdjustment(1, 1, "clear", None),))
    service = _service(_plan(), (_meal("chicken", "Chicken"),), provider)

    with pytest.raises(ValidationException, match="cannot leave a slot empty"):
        await service.ai_proposal(
            AiAdjustMealPlanCommand(
                user_id="user-1",
                plan_id="plan-1",
                prompt="Replace every meal with a vegetarian option",
            )
        )


@pytest.mark.asyncio
async def test_target_no_op_returns_specific_no_eligible_replacement_error():
    provider = _Provider((WeeklyMealPlanSlotAdjustment(1, 1, "replace", "chicken"),))
    service = _service(_plan(), (_meal("chicken", "Chicken"),), provider)

    with pytest.raises(ValidationException) as exc:
        await service.ai_proposal(
            AiAdjustMealPlanCommand(
                user_id="user-1",
                plan_id="plan-1",
                prompt="replace this meal",
                target_day_index=1,
                target_slot_index=1,
            )
        )

    assert exc.value.error_code == "AI_NO_ELIGIBLE_REPLACEMENT"


@pytest.mark.asyncio
async def test_target_no_op_resolves_a_uniquely_named_catalog_recipe():
    provider = _Provider(())
    service = _service(
        _plan(),
        (
            _meal("chicken", "Chicken"),
            _meal("egg-rice", "Egg Rice Lunch"),
            _meal("tofu", "Tofu"),
        ),
        provider,
    )

    proposal = await service.ai_proposal(
        AiAdjustMealPlanCommand(
            user_id="user-1",
            plan_id="plan-1",
            prompt="Replace this Tuesday dinner with Egg Rice Lunch. Keep every other meal unchanged.",
            target_day_index=1,
            target_slot_index=1,
        )
    )

    assert proposal.proposed_plan.slots[_slot_offset(1, 1)].recipe_id == "egg-rice"
    assert [
        (change["day_index"], change["slot_index"], change["new_recipe_id"])
        for change in proposal.slot_changes
    ] == [(1, 1, "egg-rice")]
    assert all(
        slot.recipe_id == "chicken"
        for slot in proposal.proposed_plan.slots
        if (slot.day_index, slot.slot_index) != (1, 1)
    )


@pytest.mark.asyncio
async def test_target_no_op_does_not_guess_between_duplicate_recipe_names():
    provider = _Provider(())
    service = _service(
        _plan(),
        (
            _meal("chicken", "Chicken"),
            _meal("egg-rice-a", "Egg Rice Lunch"),
            _meal("egg-rice-b", "Egg Rice Lunch"),
        ),
        provider,
    )

    with pytest.raises(ValidationException) as exc:
        await service.ai_proposal(
            AiAdjustMealPlanCommand(
                user_id="user-1",
                plan_id="plan-1",
                prompt="Replace this Tuesday dinner with Egg Rice Lunch",
                target_day_index=1,
                target_slot_index=1,
            )
        )

    assert exc.value.error_code == "AI_NO_ELIGIBLE_REPLACEMENT"


@pytest.mark.asyncio
async def test_target_no_op_does_not_select_a_recipe_the_prompt_excludes():
    provider = _Provider(())
    service = _service(
        _plan(),
        (_meal("chicken", "Chicken"), _meal("egg-rice", "Egg Rice Lunch")),
        provider,
    )

    with pytest.raises(ValidationException) as exc:
        await service.ai_proposal(
            AiAdjustMealPlanCommand(
                user_id="user-1",
                plan_id="plan-1",
                prompt="Do not use Egg Rice Lunch; choose another recipe",
                target_day_index=1,
                target_slot_index=1,
            )
        )

    assert exc.value.error_code == "AI_NO_ELIGIBLE_REPLACEMENT"


@pytest.mark.asyncio
async def test_named_recipe_fallback_still_enforces_saved_allergies():
    provider = _Provider(())
    service = _service(
        _plan(WeeklyMealPlanPreferences(allergies=("egg",))),
        (
            _meal("chicken", "Chicken"),
            _meal("egg-rice", "Egg Rice Lunch", allergen_codes=("egg",)),
        ),
        provider,
    )

    with pytest.raises(ValidationException) as exc:
        await service.ai_proposal(
            AiAdjustMealPlanCommand(
                user_id="user-1",
                plan_id="plan-1",
                prompt="Replace this meal with Egg Rice Lunch",
                target_day_index=1,
                target_slot_index=1,
            )
        )

    assert exc.value.error_code == "AI_OUTPUT_INELIGIBLE"


@pytest.mark.asyncio
async def test_week_no_repeat_constraint_rejects_repeated_or_insufficient_output():
    provider = _Provider((WeeklyMealPlanSlotAdjustment(1, 1, "replace", "tofu"),))
    service = _service(
        _plan(), (_meal("chicken", "Chicken"), _meal("tofu", "Tofu")), provider
    )

    with pytest.raises(
        ValidationException, match="cannot satisfy the no-repeat request"
    ):
        await service.ai_proposal(
            AiAdjustMealPlanCommand(
                user_id="user-1",
                plan_id="plan-1",
                prompt="Do not repeat recipes across the week",
            )
        )


@pytest.mark.asyncio
async def test_provider_rejects_new_hard_preference_if_unchanged_slots_conflict():
    provider = _Provider((WeeklyMealPlanSlotAdjustment(1, 1, "replace", "tofu"),))
    service = _service(
        _plan(), (_meal("chicken", "Chicken"), _meal("tofu", "Tofu")), provider
    )

    with pytest.raises(ValidationException, match="leave an unlogged meal in conflict"):
        await service.ai_proposal(
            AiAdjustMealPlanCommand(
                user_id="user-1", plan_id="plan-1", prompt="Make this week vegetarian"
            )
        )


@pytest.mark.asyncio
async def test_vegan_prompt_is_rejected_instead_of_downgraded_to_vegetarian():
    service = _service(_plan(), (_meal("chicken", "Chicken"), _meal("tofu", "Tofu")))

    with pytest.raises(
        ValidationException, match="Vegan preferences are not supported"
    ):
        await service.ai_proposal(
            AiAdjustMealPlanCommand(
                user_id="user-1", plan_id="plan-1", prompt="Make this week vegan"
            )
        )


def test_chat_explicit_allergies_and_avoidance_become_hard_plan_preferences():
    from src.app.services.weekly_meal_plan_service import _preferences_from_prompt

    preferences = _preferences_from_prompt(
        WeeklyMealPlanPreferences(),
        "I'm allergic to peanuts; no chicken, please",
        (
            _meal("peanut", "Salad", allergen_codes=("peanut",)),
            _meal("chicken", "Chicken"),
        ),
    )

    assert preferences.allergies == ("peanut",)
    assert preferences.dislikes == ("chicken",)


def test_peanut_free_phrase_becomes_a_hard_ingredient_dislike():
    from src.app.services.weekly_meal_plan_service import _preferences_from_prompt

    preferences = _preferences_from_prompt(
        WeeklyMealPlanPreferences(),
        "Make this meal peanut-free",
        (_meal("peanut", "Salad", allergen_codes=("peanut",)),),
    )

    assert preferences.dislikes == ("peanut",)


@pytest.mark.asyncio
async def test_patch_checks_new_hard_preferences_and_omits_unchanged_slots():
    plan = _plan(recipe_id="tofu")
    uow = _Uow(plan, (_meal("chicken", "Chicken"), _meal("tofu", "Tofu")))
    service = WeeklyMealPlanService(lambda: uow)

    updated = await service.update(
        UpdateWeeklyMealPlanCommand(
            user_id="user-1",
            plan_id="plan-1",
            idempotency_key="prefs-1",
            people=1,
            preferences=WeeklyMealPlanPreferences(people=1, diet="vegetarian"),
        )
    )

    assert updated is plan
    assert uow.update_args["slots"] is None
    assert uow.update_args["expected_revision"] == plan.revision


@pytest.mark.asyncio
async def test_provider_cannot_replace_logged_meal_or_violate_saved_diet():
    provider = _Provider((WeeklyMealPlanSlotAdjustment(1, 1, "replace", "chicken"),))
    plan = _plan(WeeklyMealPlanPreferences(diet="vegetarian"))
    service = _service(
        plan,
        (_meal("chicken", "Chicken"), _meal("tofu", "Tofu")),
        provider,
    )

    with pytest.raises(ValidationException, match="conflicts with saved preferences"):
        await service.ai_proposal(
            AiAdjustMealPlanCommand(
                user_id="user-1", plan_id="plan-1", prompt="replace a meal"
            )
        )

    with pytest.raises(ValidationException, match="logged meal"):
        await service.ai_proposal(
            AiAdjustMealPlanCommand(
                user_id="user-1",
                plan_id="plan-1",
                prompt="replace this meal",
                target_day_index=0,
                target_slot_index=0,
            )
        )


@pytest.mark.asyncio
async def test_provider_can_follow_explicit_request_over_soft_cooking_time_preference():
    slow = _meal("slow", "Slow recipe", cook_time_minutes=55)
    quick = _meal("quick", "Quick recipe", cook_time_minutes=20)
    provider = _Provider((WeeklyMealPlanSlotAdjustment(1, 1, "replace", "slow"),))
    service = _service(
        _plan(WeeklyMealPlanPreferences(cooking_time="30")),
        (_meal("chicken", "Chicken"), slow, quick),
        provider,
    )

    proposal = await service.ai_proposal(
        AiAdjustMealPlanCommand(
            user_id="user-1",
            plan_id="plan-1",
            prompt="Replace this with the slow recipe",
            target_day_index=1,
            target_slot_index=1,
        )
    )

    assert proposal.proposed_plan.slots[_slot_offset(1, 1)].recipe_id == "slow"


def _generate_command(plan):
    return GenerateWeeklyMealPlanCommand(
        user_id=plan.user_id,
        week_start_date=plan.week_start_date,
        timezone=plan.timezone,
        preferences=plan.preferences,
        idempotency_key="weekly-plan-initial-2026-09-21",
        daily_calories=plan.daily_calories or 2000,
    )


class _Reservation:
    def __init__(self, state, *, fingerprint=None, target_meal_id="plan-1"):
        self.state = state
        self.request_fingerprint = fingerprint
        self.target_meal_id = target_meal_id
        self.operation_id = "op-1"


class _GenerateUow(_Uow):
    def __init__(self, plan, meals, reservation):
        super().__init__(plan, meals)
        self.reservation = reservation
        self.adopted_fingerprint = None
        self.reopened = False

    async def reserve(self, **kwargs):
        self.reserve_fingerprint = kwargs["request_fingerprint"]
        return self.reservation

    async def adopt_fingerprint(self, reservation, *, request_fingerprint):
        self.adopted_fingerprint = request_fingerprint
        return _Reservation("replay", fingerprint=request_fingerprint)

    async def reopen_completed(self, reservation):
        self.reopened = True
        return _Reservation("acquired")


def test_legacy_generate_fingerprint_stringifies_preferences():
    command = _generate_command(_plan())
    payload = {
        key: value for key, value in vars(command).items() if key != "idempotency_key"
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)

    assert "WeeklyMealPlanPreferences(" in encoded
    assert _legacy_fingerprint(command) == hashlib.sha256(encoded.encode()).hexdigest()
    assert _legacy_fingerprint(command) != _fingerprint(command)


@pytest.mark.asyncio
async def test_generate_replays_a_plan_that_already_has_meals():
    plan = _plan()
    uow = _GenerateUow(plan, (_meal("chicken", "Chicken"),), _Reservation("replay"))
    service = WeeklyMealPlanService(lambda: uow)

    result = await service.generate(_generate_command(plan))

    assert result is plan
    assert uow.reopened is False
    assert uow.update_args is None


@pytest.mark.asyncio
async def test_generate_refills_a_completed_empty_draft():
    plan = _plan(recipe_id=None)
    uow = _GenerateUow(
        plan,
        (_meal("tofu", "Tofu"), _meal("lentils", "Lentil bowl")),
        _Reservation("replay"),
    )
    service = WeeklyMealPlanService(lambda: uow)

    await service.generate(_generate_command(plan))

    assert uow.reopened is True
    assert (0, 0) not in uow.update_args["slots"]
    assert len(uow.update_args["slots"]) == WEEKLY_PLAN_SLOT_COUNT - 1


@pytest.mark.asyncio
async def test_generate_adopts_legacy_fingerprint_and_refills_empty_draft():
    plan = _plan(recipe_id=None)
    command = _generate_command(plan)
    uow = _GenerateUow(
        plan,
        (_meal("tofu", "Tofu"), _meal("lentils", "Lentil bowl")),
        _Reservation("fingerprint_conflict", fingerprint=_legacy_fingerprint(command)),
    )
    service = WeeklyMealPlanService(lambda: uow)

    await service.generate(command)

    assert uow.adopted_fingerprint == _fingerprint(command)
    assert uow.reopened is True
    assert uow.update_args["slots"]


@pytest.mark.asyncio
async def test_generate_rejects_a_reused_key_for_a_different_request():
    plan = _plan()
    uow = _GenerateUow(
        plan,
        (_meal("chicken", "Chicken"),),
        _Reservation("fingerprint_conflict", fingerprint="different-request"),
    )
    service = WeeklyMealPlanService(lambda: uow)

    with pytest.raises(ConflictException) as exc_info:
        await service.generate(_generate_command(plan))

    assert exc_info.value.error_code == "IDEMPOTENCY_KEY_REUSED"
    assert uow.reopened is False
