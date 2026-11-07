"""Write a preparing week's dishes, then let the plan out of 'preparing'."""

from __future__ import annotations

import logging
from typing import Any

from src.app.commands.meal_planner.prepare_weekly_meal_plan_command import (
    PrepareWeeklyMealPlanCommand,
)
from src.app.commands.meal_planner.write_weekly_plan_dishes_command import (
    WriteWeeklyPlanDishesCommand,
)
from src.app.events.base import EventHandler, handles
from src.app.services.planner_dish_photos import Photographer, PlannerDishPhotos
from src.domain.model.weekly_meal_planner.meal_locale import locale_for_timezone
from src.domain.model.weekly_meal_planner.weekly_meal_plan import (
    SLOT_MEAL_TYPES,
    WEEKLY_DAYS,
    WeeklyMealPlanStatus,
)
from src.domain.services.prompts.prompt_template_manager import PromptTemplateManager
from src.domain.services.weekly_meal_planner.ai_dish import (
    DishRequest,
    PricingUnavailable,
    generate_week_of_dishes,
)

logger = logging.getLogger(__name__)

# How many catalogue rows each meal type is shown. The catalogue is this
# kitchen's knowledge now, not a style hint, so a meal type needs enough of
# it to find seven distinct dishes among rows that include headlines.
REFERENCE_DISH_LIMIT = 40
# Output room for one meal type's recipes in a single call: up to eight
# recipes at ~1,200 tokens each, the single-recipe budget.
BATCH_MAX_TOKENS = 10_000


@handles(WriteWeeklyPlanDishesCommand)
class WriteWeeklyPlanDishesCommandHandler(EventHandler):
    """Ask for the week's dishes and store them on the waiting plan.

    The plan row already exists in 'preparing', so a user who opens the
    planner while this runs sees a week being written rather than nothing.
    It leaves 'preparing' only when at least one dish was stored; a week
    nobody could write stays as it is and the job reports the failure.
    Each dish carries a photo when one was ready in time; one that was not
    leaves the empty plate, never the week.
    """

    def __init__(
        self,
        uow_factory: Any,
        generation_service: Any,
        nutrition_lookup: Any = None,
        photographer: Photographer | None = None,
    ):
        self._uow_factory = uow_factory
        self._generation = generation_service
        self._nutrition = nutrition_lookup
        # Draws a dish's photo; None leaves every dish on the empty plate.
        self._photographer = photographer

    async def handle(self, command: WriteWeeklyPlanDishesCommand) -> dict[str, Any]:
        async with self._uow_factory() as uow:
            reference = await _reference_dishes(uow)
            memory_text = await _memory_text(uow, command)

        # Each dish's photo starts as its slot keeps it, while the rest of
        # the week is still being written.
        photos = PlannerDishPhotos(self._photographer, plan_id=command.plan_id)
        try:
            week = await generate_week_of_dishes(
                _requests(command),
                ask=self._ask,
                price=self._price if self._nutrition is not None else None,
                build_names_prompt=PromptTemplateManager.build_discovery_prompt,
                build_recipe_prompt=PromptTemplateManager.build_recipe_details_prompt,
                reference_dishes=reference,
                memory_text=memory_text,
                language=command.language,
                on_dish=photos.start,
                ask_many=self._ask_many,
            )
        except PricingUnavailable as exc:
            photos.cancel()
            # Our own pricing is down, not the model's dishes. Say which,
            # so the job's failure reads as an outage rather than a week
            # nobody could write, and so a retry waits for the tiers.
            logger.error(
                "Weekly plan %s could not be priced; every nutrition tier failed on %s",
                command.plan_id,
                exc,
            )
            raise RuntimeError("WEEKLY_PLAN_PRICING_UNAVAILABLE") from exc
        except BaseException:
            # A week that failed or was stopped leaves no photo running.
            photos.cancel()
            raise

        # Why dishes were turned away, every week: a week that came back with
        # seven of twenty-one slots filled said nothing about which check
        # emptied the other fourteen.
        logger.info(
            "Weekly plan %s written: %d dishes, %d unfilled, rejected %s",
            command.plan_id,
            len(week.dishes),
            len(week.unfilled),
            week.rejection_counts(),
        )
        if not week.dishes:
            photos.cancel()
            logger.error(
                "No dish could be written for plan %s: %s",
                command.plan_id,
                [dish.reasons for dish in week.rejected][:5],
            )
            raise RuntimeError("WEEKLY_PLAN_NO_DISHES")

        dishes = await photos.attach(week.dishes)

        async with self._uow_factory() as uow:
            await uow.weekly_meal_plans.update(
                user_id=command.user_id,
                plan_id=command.plan_id,
                saved_slots=dishes,
                status=WeeklyMealPlanStatus.DRAFT.value,
            )
            await uow.commit()

        return {
            "plan_id": command.plan_id,
            "dishes": len(week.dishes),
            "unfilled": [list(slot) for slot in week.unfilled],
        }

    async def _price(self, lines):
        """What this dish's ingredients come to, through the usual tiers.

        A generated recipe carries grams and no macros, by design: the
        backend prices every line against food_reference, then FatSecret,
        then an estimate, with the same plausibility gate as everywhere.
        """
        return await self._nutrition.calculate_meal_macros(lines)

    async def _ask(self, prompt: str, system_message: str):
        # A dish is a recipe: Luna first, Workers AI only as the last fallback.
        # Without a purpose this fell to "general", where Workers AI runs first.
        return await self._generation.generate_meal_plan_async(
            prompt, system_message, "json", model_purpose="recipe"
        )

    async def _ask_many(self, prompt: str, system_message: str):
        # A meal type's recipes in one answer: room for every one of them,
        # so the JSON is never cut off and lost whole.
        return await self._generation.generate_meal_plan_async(
            prompt,
            system_message,
            "json",
            max_tokens=BATCH_MAX_TOKENS,
            model_purpose="recipe",
        )


def _requests(command: WriteWeeklyPlanDishesCommand) -> tuple[DishRequest, ...]:
    """One request per slot, carrying that slot's share of the day."""
    locale = locale_for_timezone(command.timezone)
    shares = locale.slot_calorie_shares
    requests: list[DishRequest] = []
    for day in range(WEEKLY_DAYS):
        for index, meal_type in enumerate(SLOT_MEAL_TYPES):
            share = shares[index] if index < len(shares) else 1 / len(SLOT_MEAL_TYPES)
            requests.append(
                DishRequest(
                    day_index=day,
                    slot_index=index,
                    meal_type=meal_type,
                    target_calories=int(round(command.daily_calories * share)),
                    protein=_share(command.daily_protein, share),
                    carbs=_share(command.daily_carbs, share),
                    fat=_share(command.daily_fat, share),
                    note=locale.slot_note(index),
                    minutes=locale.breakfast_minutes if index == 0 else None,
                    # Only breakfast: the region's note asks each morning to
                    # start from a different base, and asks nothing of the
                    # kind of lunch and dinner, which are rice both times.
                    vary_base=index == 0 and bool(locale.breakfast_note),
                )
            )
    return tuple(requests)


def _share(total: float | None, share: float) -> float | None:
    return round(total * share, 1) if total else None


async def _reference_dishes(uow) -> dict[str, tuple[str, ...]]:
    """The catalogue this kitchen cooks from, split by the meal it suits.

    A row carries its own ``meal_types``, so breakfast is shown breakfast
    rows. A row that says nothing is shown to every meal: an untagged
    catalogue should still be usable, just less precisely.
    """
    repo = getattr(uow, "catalog_recipes", None)
    lister = getattr(repo, "list_selection_candidates", None)
    if lister is None:
        return {}
    try:
        meals = await lister()
    except Exception:  # noqa: BLE001 - never worth failing a week over
        logger.warning("Could not read catalogue dishes for the planner prompt")
        return {}

    by_meal: dict[str, list[str]] = {meal: [] for meal in SLOT_MEAL_TYPES}
    for meal in meals:
        name = str(getattr(meal, "name", "") or "").strip()
        if not name:
            continue
        tags = {
            str(tag).strip().casefold() for tag in getattr(meal, "meal_types", ()) or ()
        }
        wanted = tags & set(SLOT_MEAL_TYPES) or set(SLOT_MEAL_TYPES)
        for meal_type in wanted:
            if len(by_meal[meal_type]) < REFERENCE_DISH_LIMIT:
                by_meal[meal_type].append(name)
    return {meal: tuple(names) for meal, names in by_meal.items() if names}


async def _memory_text(uow, command: WriteWeeklyPlanDishesCommand) -> str:
    """What the user's own log says, for the prompt. Never a blocker."""
    try:
        from src.app.services.weekly_meal_plan_service import _user_food_memory

        memory = await _user_food_memory(
            uow,
            user_id=command.user_id,
            week_start_date=command.week_start_date,
            timezone=command.timezone,
        )
    except Exception:  # noqa: BLE001
        logger.warning("Could not read the food memory for the planner prompt")
        return ""
    return memory.prompt_text()


@handles(PrepareWeeklyMealPlanCommand)
class PrepareWeeklyMealPlanCommandHandler(EventHandler):
    """Open the week with no dishes, for a job to write into.

    An existing plan is returned untouched, whichever state it is in: a
    second tab asking for the same week must not start a second write.
    """

    def __init__(self, uow_factory: Any):
        self._uow_factory = uow_factory

    async def handle(self, command: PrepareWeeklyMealPlanCommand):
        async with self._uow_factory() as uow:
            current = await uow.weekly_meal_plans.get_current(
                user_id=command.user_id, week_start_date=command.week_start_date
            )
            if current is not None:
                return current
            plan = await uow.weekly_meal_plans.create(
                user_id=command.user_id,
                week_start_date=command.week_start_date,
                timezone=command.timezone,
                preferences=command.preferences,
                status=WeeklyMealPlanStatus.PREPARING.value,
                daily_calories=command.daily_calories,
            )
            await uow.commit()
            return plan
