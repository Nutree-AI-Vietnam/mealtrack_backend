import hashlib
import json
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.api.exceptions import ConflictException, ValidationException
from src.app.commands.meal_planner import (
    AiAdjustMealPlanCommand,
    GenerateWeeklyMealPlanCommand,
    UpdateWeeklyMealPlanCommand,
)
from src.app.services import weekly_meal_plan_service
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
from src.domain.services.weekly_meal_planner import WeeklyPlanGenerationService


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


_CATALOG_VERSION = SimpleNamespace(selection=1, ingredients=1)


async def _noop_async(*_args, **_kwargs):
    return None


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
        self.requested_meal_ids = []
        self.catalog_preparation = SimpleNamespace(enqueue_for_recipes=_noop_async)

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

    async def list_selection_candidates(self):
        return self.meals

    async def list_active_ingredient_names(self):
        return sorted(
            {item.display_name for meal in self.meals for item in meal.ingredients}
        )

    async def lookup(self, **_):
        return None

    async def capture_catalog_publication_version(self):
        return _CATALOG_VERSION

    async def lock_catalog_publication(self, *, shared=True):
        return _CATALOG_VERSION

    async def get_meals(self, recipe_ids):
        self.requested_meal_ids.append(tuple(recipe_ids))
        return [meal for meal in self.meals if meal.id in recipe_ids]

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
async def test_provider_proposal_reads_compact_candidates_and_hydrates_selected_ids_only(
    monkeypatch,
):
    from dataclasses import replace

    full_meals = (
        _meal("chicken", "Chicken"),
        _meal("tofu", "Tofu"),
        _meal("unused", "Unused recipe"),
    )
    plan = _plan()
    uows = []

    class CompactUow(_Uow):
        async def list_active_meals(self):
            pytest.fail("Provider proposals must not hydrate the full catalog")

        async def list_selection_candidates(self):
            return tuple(
                replace(meal, ingredients=(), steps=(), recipe_payload=None)
                for meal in self.meals
            )

        async def list_active_ingredient_names(self):
            return ("Chicken", "Tofu")

    def factory():
        uow = CompactUow(plan, full_meals)
        uows.append(uow)
        return uow

    provider = _Provider((WeeklyMealPlanSlotAdjustment(1, 0, "replace", "tofu"),))
    service = WeeklyMealPlanService(factory, ai_adjustment_provider=provider)
    proposal = await service.ai_proposal(
        AiAdjustMealPlanCommand(
            user_id="user-1",
            plan_id=plan.id,
            prompt="Replace this lunch with tofu",
            target_day_index=1,
            target_slot_index=0,
        )
    )
    assert len(uows) == 2
    assert not any(uow.is_open for uow in uows)
    assert uows[0].requested_meal_ids == []
    assert uows[1].requested_meal_ids == [("chicken", "tofu")]
    assert all(not meal.ingredients for meal in provider.context["meals"])
    assert proposal.proposed_groceries


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
async def test_provider_partial_answer_fills_remaining_hard_preference_conflicts():
    provider = _Provider((WeeklyMealPlanSlotAdjustment(1, 1, "replace", "tofu"),))
    service = _service(
        _plan(),
        (
            _meal("chicken", "Chicken"),
            _meal("tofu", "Tofu", rank=2),
            _meal("lentils", "Lentil bowl", rank=3),
        ),
        provider,
    )

    proposal = await service.ai_proposal(
        AiAdjustMealPlanCommand(
            user_id="user-1", plan_id="plan-1", prompt="Make this week vegetarian"
        )
    )

    by_coordinate = {
        (slot.day_index, slot.slot_index): slot for slot in proposal.proposed_plan.slots
    }
    assert by_coordinate[(0, 0)].recipe_id == "chicken"
    unlogged = [slot for slot in proposal.proposed_plan.slots if not slot.is_logged]
    assert all(slot.recipe_id in {"tofu", "lentils"} for slot in unlogged)
    assert {slot.recipe_id for slot in unlogged} == {"tofu", "lentils"}
    assert len(proposal.slot_changes) == WEEKLY_PLAN_SLOT_COUNT - 1


@pytest.mark.asyncio
async def test_provider_rejects_new_hard_preference_when_no_recipe_fits():
    provider = _Provider(())
    service = _service(_plan(), (_meal("chicken", "Chicken"),), provider)

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


def test_prompt_dislikes_use_ingredient_names_when_candidates_are_compact():
    from dataclasses import replace

    from src.app.services.weekly_meal_plan_service import _preferences_from_prompt

    compact = replace(_meal("soup", "Soup"), ingredients=())

    preferences = _preferences_from_prompt(
        WeeklyMealPlanPreferences(),
        "No mushrooms this week",
        (compact,),
        ingredient_names=("Mushroom", "Rice noodles"),
    )

    assert preferences.dislikes == ("mushroom",)


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
    assert uow.requested_meal_ids == [("tofu",)]


@pytest.mark.asyncio
async def test_patch_fetches_only_deduplicated_replacements():
    uow = _Uow(_plan(), (_meal("chicken", "Chicken"), _meal("tofu", "Tofu")))
    await WeeklyMealPlanService(lambda: uow).update(
        UpdateWeeklyMealPlanCommand(
            user_id="user-1",
            plan_id="plan-1",
            idempotency_key="swap",
            slots={(1, 0): "tofu", (1, 1): "tofu", (2, 0): None},
        )
    )
    assert uow.requested_meal_ids == [("tofu",)]
    assert uow.update_args["slots"][(2, 0)] is None


@pytest.mark.asyncio
async def test_patch_soft_preferences_do_not_load_catalog():
    uow = _Uow(_plan(), ())
    await WeeklyMealPlanService(lambda: uow).update(
        UpdateWeeklyMealPlanCommand(
            user_id="user-1",
            plan_id="plan-1",
            idempotency_key="soft-prefs",
            preferences=WeeklyMealPlanPreferences(cuisine="italian"),
        )
    )
    assert uow.requested_meal_ids == []


@pytest.mark.asyncio
async def test_patch_hard_preferences_validate_unlogged_recipes_and_replacements():
    from dataclasses import replace

    plan = _plan(recipe_id="tofu")
    plan = replace(
        plan, slots=(replace(plan.slots[0], recipe_id="logged-only"), *plan.slots[1:])
    )
    uow = _Uow(plan, (_meal("tofu", "Tofu"), _meal("lentils", "Lentil bowl")))
    await WeeklyMealPlanService(lambda: uow).update(
        UpdateWeeklyMealPlanCommand(
            user_id="user-1",
            plan_id="plan-1",
            idempotency_key="hard-prefs",
            preferences=WeeklyMealPlanPreferences(diet="vegetarian"),
            slots={(1, 0): "lentils"},
        )
    )
    assert uow.requested_meal_ids == [("lentils", "tofu")]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "meal,expected",
    [
        (None, "RECIPE_NOT_FOUND"),
        (_meal("tofu", "Tofu", meal_types=("dinner",)), "RECIPE_SLOT_INELIGIBLE"),
        (_meal("tofu", "Tofu", allergen_codes=("peanut",)), "RECIPE_INELIGIBLE"),
    ],
)
async def test_patch_selected_recipe_keeps_exact_validation_errors(meal, expected):
    plan = _plan(WeeklyMealPlanPreferences(allergies=("peanut",)))
    uow = _Uow(plan, () if meal is None else (meal,))
    with pytest.raises(ValidationException) as exc:
        await WeeklyMealPlanService(lambda: uow).update(
            UpdateWeeklyMealPlanCommand(
                user_id="user-1",
                plan_id="plan-1",
                idempotency_key="invalid",
                slots={(1, 0): "tofu"},
            )
        )
    assert exc.value.error_code == expected
    assert uow.update_args is None


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


@pytest.fixture
def empty_basket_cache(monkeypatch):
    monkeypatch.setattr(weekly_meal_plan_service, "_basket_ids_cache", None)


class _IngredientLinesUow(_Uow):
    def __init__(self, plan, meals, lines):
        super().__init__(plan, meals)
        self.lines = lines
        self.line_reads = 0
        self.version = _CATALOG_VERSION

    async def capture_catalog_publication_version(self):
        return self.version

    async def lock_catalog_publication(self, *, shared=True):
        return self.version

    async def list_selection_ingredient_lines(self):
        self.line_reads += 1
        return self.lines


class _RecordingGenerator(WeeklyPlanGenerationService):
    kwargs = None
    compact_kwargs = None

    def generate(self, meals, **kwargs):
        self.kwargs = kwargs
        return super().generate(meals, **kwargs)

    def generate_compact(self, meals, **kwargs):
        self.compact_kwargs = kwargs
        return super().generate_compact(meals, **kwargs)


def test_compact_week_arm_is_a_stable_share_of_users_that_only_grows():
    users = [f"user-{index}" for index in range(400)]

    def arm(percent):
        service = WeeklyMealPlanService(None, compact_week_percent=percent)
        return {user for user in users if service._plans_compact_week(user)}

    assert arm(0) == set()
    assert arm(100) == set(users)
    assert 150 < len(arm(50)) < 250
    assert arm(50) == arm(50)
    assert arm(20) <= arm(50)


@pytest.mark.asyncio
async def test_generation_outside_the_compact_arm_reads_no_ingredient_lines(
    empty_basket_cache,
):
    plan = _plan(recipe_id=None)
    uow = _IngredientLinesUow(plan, (_meal("tofu", "Tofu"),), [])
    generator = _RecordingGenerator()

    await WeeklyMealPlanService(lambda: uow, generator=generator).generate(
        _generate_command(plan)
    )

    assert uow.line_reads == 0
    assert generator.compact_kwargs is None
    assert uow.update_args["algorithm_version"] == "v2"


@pytest.mark.asyncio
async def test_compact_arm_keeps_logged_meals_and_reads_lines_once_per_catalog(
    empty_basket_cache,
):
    plan = _plan(recipe_id="tofu")  # breakfast on day 0 is logged
    uow = _IngredientLinesUow(
        plan,
        (_meal("tofu", "Tofu"), _meal("lentils", "Lentil bowl")),
        [
            ("tofu", 7, "Đậu hũ"),
            ("tofu", None, "Muối"),
            ("lentils", 9, "Lentils"),
            ("plain-rice", None, "Gạo"),
        ],
    )
    generator = _RecordingGenerator()
    service = WeeklyMealPlanService(
        lambda: uow, generator=generator, compact_week_percent=100
    )

    await service.generate(_generate_command(plan))
    await service.generate(_generate_command(plan))

    assert generator.compact_kwargs["basket_ids"] == {
        "tofu": frozenset({7}),
        "lentils": frozenset({9}),
        "plain-rice": frozenset(),
    }
    assert generator.compact_kwargs["fixed"] == {(0, 0): "tofu"}
    assert generator.compact_kwargs["last_week"] == frozenset({"tofu"})
    assert uow.update_args["algorithm_version"] == "v3-compact"
    assert (0, 0) not in uow.update_args["slots"]
    assert uow.line_reads == 1

    uow.version = SimpleNamespace(selection=1, ingredients=2)
    await service.generate(_generate_command(plan))

    assert uow.line_reads == 2


def test_week_shortlist_preserves_dinner_coverage_and_named_recipe():
    from src.app.services.weekly_meal_plan_service import _ai_shortlist
    from src.domain.services.weekly_meal_planner import WeeklyPlanGenerationService

    meals = tuple(
        _meal(f"lunch-{i}", f"Lunch {i}", meal_types=("lunch",), rank=i)
        for i in range(40)
    )
    dinner = _meal("dinner", "Rare dinner", meal_types=("dinner",), rank=100)
    chosen = _ai_shortlist(
        WeeklyPlanGenerationService(),
        "replace Tuesday dinner",
        (*meals, dinner),
        WeeklyMealPlanPreferences(),
        None,
    )
    assert len(chosen) == 40
    assert dinner in chosen
    named = _ai_shortlist(
        WeeklyPlanGenerationService(),
        "use Lunch 39",
        (*meals, dinner),
        WeeklyMealPlanPreferences(),
        None,
    )
    assert named[0].id == "lunch-39"


@pytest.mark.asyncio
async def test_shortlist_widens_once_to_reach_lower_ranked_target_recipe():
    class Provider:
        calls = 0

        async def propose(self, **context):
            self.calls += 1
            assert len(context["meals"]) <= 40
            replacement = next(
                (meal for meal in context["meals"] if meal.id == "desired"), None
            )
            return WeeklyMealPlanAdjustmentProposal(
                base_revision=context["plan"].revision,
                explanation="Ready",
                slot_changes=(WeeklyMealPlanSlotAdjustment(1, 1, "replace", "desired"),)
                if replacement
                else (),
            )

    provider = Provider()
    meals = tuple(_meal(f"recipe-{i}", f"Recipe {i}", rank=i) for i in range(45)) + (
        _meal("desired", "Desired", rank=100),
        _meal("chicken", "Chicken", rank=0),
    )
    result = await _service(_plan(), meals, provider).ai_proposal(
        AiAdjustMealPlanCommand(
            user_id="user-1",
            plan_id="plan-1",
            prompt="something different",
            target_day_index=1,
            target_slot_index=1,
        )
    )
    target_slot = next(
        slot
        for slot in result.proposed_plan.slots
        if slot.day_index == 1 and slot.slot_index == 1
    )
    assert target_slot.recipe_id == "desired"
    assert provider.calls == 2


class _TwoWeeksUow(_Uow):
    """This week's plan and last week's, told apart by week start."""

    def __init__(self, plan, last_week_plan, meals):
        super().__init__(plan, meals)
        self.last_week_plan = last_week_plan
        self.requested_weeks = []

    async def get_current(self, *, user_id, week_start_date):
        self.requested_weeks.append(week_start_date)
        if week_start_date == self.plan.week_start_date:
            return self.plan
        return self.last_week_plan


@pytest.mark.asyncio
async def test_generation_hands_last_weeks_dishes_to_the_generator():
    plan = _plan(recipe_id=None)
    uow = _TwoWeeksUow(
        plan,
        _plan(recipe_id="tofu"),
        (_meal("tofu", "Tofu"), _meal("lentils", "Lentil bowl")),
    )
    generator = _RecordingGenerator()
    service = WeeklyMealPlanService(lambda: uow, generator=generator)

    await service.generate(_generate_command(plan))

    assert plan.week_start_date - timedelta(days=7) in uow.requested_weeks
    assert generator.kwargs["last_week"] == frozenset({"tofu"})


@pytest.mark.asyncio
async def test_a_first_week_has_nothing_to_avoid():
    plan = _plan(recipe_id=None)
    uow = _TwoWeeksUow(plan, None, (_meal("tofu", "Tofu"),))
    generator = _RecordingGenerator()
    service = WeeklyMealPlanService(lambda: uow, generator=generator)

    await service.generate(_generate_command(plan))

    assert generator.kwargs["last_week"] == frozenset()


# Saved recipes in slots ------------------------------------------------------

_SAVED_ID = "recipe_import_imp-1"


def _saved_data(**overrides):
    data = {
        "meal_name": "Chicken pho",
        "macros": {"protein": 30.0, "carbs": 50.0, "fat": 10.0, "fiber": 4.0},
        "ingredients": [
            {"name": "Chicken", "amount": 400, "unit": "g", "food_reference_id": 3}
        ],
        "servings": 4,
        "recipe_import": {"source_url": "https://cook.example/pho"},
    }
    data.update(overrides)
    return data


def _saved_snapshot():
    from src.domain.model.weekly_meal_planner import SavedRecipeSnapshot

    return SavedRecipeSnapshot.from_saved_suggestion(_SAVED_ID, _saved_data())


def _with_saved(plan, coordinate, *, logged=False):
    from dataclasses import replace

    return replace(
        plan,
        slots=tuple(
            replace(
                slot,
                recipe_id=None,
                saved_suggestion_id=_SAVED_ID,
                saved_recipe=_saved_snapshot(),
                is_logged=logged,
                logged_meal_id="saved-meal" if logged else None,
            )
            if (slot.day_index, slot.slot_index) == coordinate
            else slot
            for slot in plan.slots
        ),
    )


class _SavedSuggestions:
    def __init__(self, records):
        self.records = records
        self.lookups = []

    async def find_by_user_and_suggestion(self, user_id, suggestion_id):
        self.lookups.append((user_id, suggestion_id))
        return self.records.get((user_id, suggestion_id))


class _SavedUow(_Uow):
    def __init__(self, plan, meals, records=None):
        super().__init__(plan, meals)
        self.saved_suggestions = _SavedSuggestions(
            {("user-1", _SAVED_ID): {"suggestion_data": _saved_data()}}
            if records is None
            else records
        )
        self.released = 0

    async def release(self, *_args, **_kwargs):
        self.released += 1


@pytest.mark.asyncio
async def test_patch_puts_a_copy_of_the_callers_saved_recipe_in_the_slot():
    uow = _SavedUow(_plan(), (_meal("chicken", "Chicken"),))

    await WeeklyMealPlanService(lambda: uow).update(
        UpdateWeeklyMealPlanCommand(
            user_id="user-1",
            plan_id="plan-1",
            idempotency_key="saved-1",
            slots={},
            expected_revision=3,
            saved_slots={(1, 2): _SAVED_ID, (2, 2): _SAVED_ID},
        )
    )

    saved = uow.update_args["saved_slots"]
    assert set(saved) == {(1, 2), (2, 2)}
    assert saved[(1, 2)] == _saved_snapshot()
    assert saved[(1, 2)].name == "Chicken pho"
    assert uow.update_args["slots"] == {}
    assert uow.update_args["expected_revision"] == 3
    # One lookup per distinct saved recipe, scoped to the caller.
    assert uow.saved_suggestions.lookups == [("user-1", _SAVED_ID)]


@pytest.mark.asyncio
async def test_saved_recipe_is_exempt_from_plan_diet_rules():
    # Vegetarian plan, chicken saved recipe, and a preference change that
    # would reject the catalog recipe the saved one replaces.
    uow = _SavedUow(_plan(recipe_id="chicken"), (_meal("chicken", "Chicken pork"),))

    await WeeklyMealPlanService(lambda: uow).update(
        UpdateWeeklyMealPlanCommand(
            user_id="user-1",
            plan_id="plan-1",
            idempotency_key="saved-diet",
            people=1,
            preferences=WeeklyMealPlanPreferences(people=1, dislikes=("chicken",)),
            slots={
                coordinate: None
                for coordinate in ((day, slot) for day in range(7) for slot in range(3))
                if coordinate not in {(0, 0), (1, 2)}
            },
            saved_slots={(1, 2): _SAVED_ID},
        )
    )

    assert uow.update_args["saved_slots"][(1, 2)].name == "Chicken pho"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "records",
    [
        {},
        # Another user's saved recipe with the same id.
        {("user-2", _SAVED_ID): {"suggestion_data": _saved_data()}},
    ],
)
async def test_patch_rejects_a_saved_recipe_the_caller_does_not_have(records):
    from src.api.exceptions import ResourceNotFoundException

    uow = _SavedUow(_plan(), (), records)
    with pytest.raises(ResourceNotFoundException) as exc:
        await WeeklyMealPlanService(lambda: uow).update(
            UpdateWeeklyMealPlanCommand(
                user_id="user-1",
                plan_id="plan-1",
                idempotency_key="saved-missing",
                saved_slots={(1, 2): _SAVED_ID},
            )
        )
    assert exc.value.error_code == "SAVED_RECIPE_NOT_FOUND"
    assert uow.update_args is None
    assert uow.released == 1


@pytest.mark.asyncio
async def test_patch_rejects_a_saved_recipe_without_nutrition():
    from src.api.exceptions import BusinessLogicException

    uow = _SavedUow(
        _plan(),
        (),
        {("user-1", _SAVED_ID): {"suggestion_data": {"meal_name": "Only a name"}}},
    )
    with pytest.raises(BusinessLogicException) as exc:
        await WeeklyMealPlanService(lambda: uow).update(
            UpdateWeeklyMealPlanCommand(
                user_id="user-1",
                plan_id="plan-1",
                idempotency_key="saved-unusable",
                saved_slots={(1, 2): _SAVED_ID},
            )
        )
    assert exc.value.error_code == "SAVED_RECIPE_UNUSABLE"


@pytest.mark.asyncio
@pytest.mark.parametrize("coordinate", [(7, 0), (0, 3)])
async def test_patch_rejects_invalid_saved_coordinates(coordinate):
    uow = _SavedUow(_plan(), ())
    with pytest.raises(ValidationException) as exc:
        await WeeklyMealPlanService(lambda: uow).update(
            UpdateWeeklyMealPlanCommand(
                user_id="user-1",
                plan_id="plan-1",
                idempotency_key="saved-bad",
                saved_slots={coordinate: _SAVED_ID},
            )
        )
    assert exc.value.error_code == "WEEKLY_SLOT_INVALID"


@pytest.mark.asyncio
async def test_patch_rejects_a_slot_given_both_recipe_kinds():
    uow = _SavedUow(_plan(), (_meal("tofu", "Tofu"),))
    with pytest.raises(ValidationException) as exc:
        await WeeklyMealPlanService(lambda: uow).update(
            UpdateWeeklyMealPlanCommand(
                user_id="user-1",
                plan_id="plan-1",
                idempotency_key="saved-both",
                slots={(1, 2): "tofu"},
                saved_slots={(1, 2): _SAVED_ID},
            )
        )
    assert exc.value.error_code == "WEEKLY_SLOT_INVALID"


def test_requests_without_saved_recipes_keep_their_fingerprint():
    from src.app.services.weekly_meal_plan_service import _fingerprint_value
    from src.domain.utils.fingerprint_utils import canonicalize_fingerprint

    command = UpdateWeeklyMealPlanCommand(
        user_id="user-1",
        plan_id="plan-1",
        idempotency_key="k",
        slots={(1, 0): "tofu"},
        expected_revision=2,
    )
    before_field = canonicalize_fingerprint(
        {
            key: _fingerprint_value(value)
            for key, value in vars(command).items()
            if key not in {"idempotency_key", "saved_slots"}
        }
    )

    assert _fingerprint(command) == before_field
    with_saved = UpdateWeeklyMealPlanCommand(
        **{**vars(command), "saved_slots": {(1, 2): _SAVED_ID}}
    )
    assert _fingerprint(with_saved) != before_field


@pytest.mark.asyncio
async def test_meal_proposal_replaces_a_saved_slot_with_a_catalog_recipe():
    plan = _with_saved(_plan(), (2, 1))
    service = _service(plan, (_meal("chicken", "Chicken"), _meal("tofu", "Tofu")))

    proposal = await service.ai_proposal(
        AiAdjustMealPlanCommand(
            user_id="user-1",
            plan_id="plan-1",
            prompt="replace with tofu",
            target_day_index=2,
            target_slot_index=1,
        )
    )

    assert proposal.slot_changes == (
        {
            "day_index": 2,
            "slot_index": 1,
            "previous_recipe_id": _SAVED_ID,
            "new_recipe_id": "chicken",
            "action": "replace",
        },
    )
    replaced = proposal.proposed_plan.slots[_slot_offset(2, 1)]
    assert replaced.recipe_id == "chicken"
    assert (replaced.saved_suggestion_id, replaced.saved_recipe) == (None, None)


@pytest.mark.asyncio
async def test_week_proposal_keeps_a_logged_saved_slot_and_projects_its_groceries():
    plan = _with_saved(_with_saved(_plan(), (1, 2), logged=True), (3, 2))
    service = _service(plan, (_meal("chicken", "Chicken"), _meal("tofu", "Tofu")))

    proposal = await service.ai_proposal(
        AiAdjustMealPlanCommand(
            user_id="user-1", plan_id="plan-1", prompt="Make this week vegetarian"
        )
    )

    logged = proposal.proposed_plan.slots[_slot_offset(1, 2)]
    assert (logged.saved_suggestion_id, logged.is_logged) == (_SAVED_ID, True)
    unlogged = proposal.proposed_plan.slots[_slot_offset(3, 2)]
    assert unlogged.recipe_id == "tofu" and unlogged.saved_recipe is None
    changed = {(c["day_index"], c["slot_index"]): c for c in proposal.slot_changes}
    assert (1, 2) not in changed
    assert changed[(3, 2)]["previous_recipe_id"] == _SAVED_ID
    # The logged saved recipe's chicken is still on the list (100 g a person).
    chicken = next(
        item for item in proposal.proposed_groceries if item.ingredient_id == 3
    )
    assert chicken.total_needed == 100


@pytest.mark.asyncio
async def test_provider_sees_saved_slots_and_can_replace_or_clear_them():
    plan = _with_saved(_with_saved(_plan(), (1, 1)), (1, 2))
    provider = _Provider(
        (
            WeeklyMealPlanSlotAdjustment(1, 1, "replace", "tofu"),
            WeeklyMealPlanSlotAdjustment(1, 2, "clear", None),
        )
    )
    service = _service(
        plan, (_meal("chicken", "Chicken"), _meal("tofu", "Tofu")), provider
    )

    proposal = await service.ai_proposal(
        AiAdjustMealPlanCommand(
            user_id="user-1",
            plan_id="plan-1",
            prompt="swap Tuesday lunch for tofu and remove the meal at dinner",
        )
    )

    assert [
        (c["previous_recipe_id"], c["new_recipe_id"], c["action"])
        for c in proposal.slot_changes
    ] == [(_SAVED_ID, "tofu", "replace"), (_SAVED_ID, None, "clear")]
    cleared = proposal.proposed_plan.slots[_slot_offset(1, 2)]
    assert not cleared.has_recipe


@pytest.mark.asyncio
async def test_regeneration_replaces_unlogged_saved_slots_and_keeps_logged_ones(
    empty_basket_cache,
):
    plan = _with_saved(_with_saved(_plan(recipe_id=None), (1, 2), logged=True), (2, 2))
    uow = _IngredientLinesUow(
        plan,
        (_meal("tofu", "Tofu"), _meal("lentils", "Lentil bowl")),
        [("tofu", 7, "Tofu"), ("lentils", 9, "Lentils")],
    )
    generator = _RecordingGenerator()
    service = WeeklyMealPlanService(
        lambda: uow, generator=generator, compact_week_percent=100
    )

    await service.generate(_generate_command(plan))

    # A logged saved dinner stays fixed and feeds no leftover lunch.
    assert generator.compact_kwargs["fixed"] == {(1, 2): f"saved:{_SAVED_ID}"}
    slots = uow.update_args["slots"]
    assert (1, 2) not in slots
    assert slots[(2, 2)] in {"tofu", "lentils"}
    assert all(not value.startswith("saved:") for value in slots.values() if value)
