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
from src.domain.model.weekly_meal_planner.meal_locale import locale_for_timezone
from src.domain.model.weekly_meal_planner.weekly_meal_plan import (
    SLOT_MEAL_TYPES,
    WEEKLY_DAYS,
    WeeklyMealPlanStatus,
)
from src.domain.services.prompts.prompt_template_manager import PromptTemplateManager
from src.domain.services.weekly_meal_planner.ai_dish import (
    DishRequest,
    generate_week_of_dishes,
)

logger = logging.getLogger(__name__)

# How many catalogue dishes are worth showing the model as a hint.
REFERENCE_DISH_LIMIT = 8


@handles(WriteWeeklyPlanDishesCommand)
class WriteWeeklyPlanDishesCommandHandler(EventHandler):
    """Ask for the week's dishes and store them on the waiting plan.

    The plan row already exists in 'preparing', so a user who opens the
    planner while this runs sees a week being written rather than nothing.
    It leaves 'preparing' only when at least one dish was stored; a week
    nobody could write stays as it is and the job reports the failure.
    """

    def __init__(self, uow_factory: Any, generation_service: Any):
        self._uow_factory = uow_factory
        self._generation = generation_service

    async def handle(self, command: WriteWeeklyPlanDishesCommand) -> dict[str, Any]:
        async with self._uow_factory() as uow:
            reference = await _reference_dishes(uow)
            memory_text = await _memory_text(uow, command)

        week = await generate_week_of_dishes(
            _requests(command),
            ask=self._ask,
            build_names_prompt=PromptTemplateManager.build_discovery_prompt,
            build_recipe_prompt=PromptTemplateManager.build_recipe_details_prompt,
            reference_dishes=reference,
            memory_text=memory_text,
            language=command.language,
        )

        if not week.dishes:
            logger.error(
                "No dish could be written for plan %s: %s",
                command.plan_id,
                [dish.reasons for dish in week.rejected][:5],
            )
            raise RuntimeError("WEEKLY_PLAN_NO_DISHES")

        async with self._uow_factory() as uow:
            await uow.weekly_meal_plans.update(
                user_id=command.user_id,
                plan_id=command.plan_id,
                saved_slots=week.dishes,
                status=WeeklyMealPlanStatus.DRAFT.value,
            )
            await uow.commit()

        return {
            "plan_id": command.plan_id,
            "dishes": len(week.dishes),
            "unfilled": [list(slot) for slot in week.unfilled],
        }

    async def _ask(self, prompt: str, system_message: str):
        return await self._generation.generate_meal_plan_async(
            prompt, system_message, "json"
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
                )
            )
    return tuple(requests)


def _share(total: float | None, share: float) -> float | None:
    return round(total * share, 1) if total else None


async def _reference_dishes(uow) -> tuple[str, ...]:
    """A few catalogue names, shown as a hint and never as an answer."""
    repo = getattr(uow, "catalog_recipes", None)
    lister = getattr(repo, "list_selection_candidates", None)
    if lister is None:
        return ()
    try:
        meals = await lister()
    except Exception:  # noqa: BLE001 - a hint is never worth failing a week
        logger.warning("Could not read catalogue dishes for the planner prompt")
        return ()
    names = [str(meal.name).strip() for meal in meals if getattr(meal, "name", "")]
    return tuple(names[:REFERENCE_DISH_LIMIT])


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
