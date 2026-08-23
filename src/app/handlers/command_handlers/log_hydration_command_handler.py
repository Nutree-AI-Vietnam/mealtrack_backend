"""Command handler for logging a hydration (non-caloric) drink entry."""

import logging
from uuid import uuid4

from src.app.commands.hydration.log_hydration_command import LogHydrationCommand
from src.app.events.base import EventHandler, handles
from src.app.events.hydration.hydration_created_event import HydrationCreatedEvent
from src.domain.model.hydration import DrinkCategory, HydrationEntry
from src.domain.model.meal import Meal, MealImage, MealStatus
from src.domain.model.nutrition.macros import Macros
from src.domain.model.nutrition.nutrition import Nutrition
from src.domain.services.hydration_catalog_service import find_by_id, localized_name
from src.domain.utils.timezone_utils import (
    format_iso_utc,
    get_zone_info,
    noon_utc_for_date,
    resolve_user_timezone_async,
    utc_now,
)
from src.infra.config.settings import get_settings
from src.infra.database.uow_async import AsyncUnitOfWork

logger = logging.getLogger(__name__)


@handles(LogHydrationCommand)
class LogHydrationCommandHandler(EventHandler[LogHydrationCommand, dict]):
    def __init__(
        self,
        uow: AsyncUnitOfWork,
    ):
        self.uow = uow

    async def handle(self, cmd: LogHydrationCommand) -> dict:
        drink = find_by_id(cmd.drink_id)
        if drink is None:
            raise ValueError(f"Unknown drink: {cmd.drink_id}")
        if drink.category != DrinkCategory.HYDRATION:
            raise ValueError("Drink is not a hydration drink")

        async with self.uow as uow:
            now = utc_now()
            if cmd.target_date:
                user_tz = await resolve_user_timezone_async(
                    cmd.user_id, uow, cmd.header_timezone
                )
                log_date = cmd.target_date
                log_dt = noon_utc_for_date(log_date, user_tz)
            else:
                log_dt = now
                user_tz = await resolve_user_timezone_async(
                    cmd.user_id, uow, cmd.header_timezone
                )
                tz = get_zone_info(user_tz)
                log_date = log_dt.astimezone(tz).date()

            credited_ml = drink.credited_ml_for_volume(cmd.volume_ml)
            if drink.kcal_per_100ml > 0:
                fat_100ml = max(
                    0.0, (drink.kcal_per_100ml - drink.sugar_per_100ml * 4) / 9
                )
                carbs_100ml = max(0.0, (drink.kcal_per_100ml - fat_100ml * 9) / 4)
                volume_factor = cmd.volume_ml / 100.0
                nutrition = Nutrition(
                    macros=Macros(
                        protein=0.0,
                        carbs=round(carbs_100ml * volume_factor, 1),
                        fat=round(fat_100ml * volume_factor, 1),
                        fiber=0.0,
                        sugar=round(drink.sugar_per_100ml * volume_factor, 1),
                    ),
                    food_items=None,
                )
            else:
                nutrition = Nutrition(
                    macros=Macros(
                        protein=0.0, carbs=0.0, fat=0.0, fiber=0.0, sugar=0.0
                    ),
                    food_items=None,
                )
            meal = Meal(
                meal_id=str(uuid4()),
                user_id=cmd.user_id,
                status=MealStatus.READY,
                created_at=log_dt,
                ready_at=log_dt,
                image=MealImage(
                    image_id=str(uuid4()), format="jpeg", size_bytes=1, url=None
                ),
                dish_name=drink.name,
                emoji=drink.emoji,
                meal_type="hydration",
                source="hydration",
                quantity=credited_ml,
                nutrition=nutrition,
            )
            saved = await uow.meals.save(meal)
            macros = saved.nutrition.macros if saved.nutrition else nutrition.macros
            hydration_entry = await uow.hydration_entries.add(
                HydrationEntry(
                    user_id=cmd.user_id,
                    drink_id=cmd.drink_id,
                    drink_name_snapshot=drink.name,
                    emoji_snapshot=drink.emoji,
                    volume_ml=cmd.volume_ml,
                    credited_ml=credited_ml,
                    protein_g=macros.protein,
                    carbs_g=macros.carbs,
                    fat_g=macros.fat,
                    fiber_g=macros.fiber,
                    sugar_g=macros.sugar,
                    logged_at=log_dt,
                    source="hydration",
                    legacy_meal_id=saved.meal_id,
                )
            )
            integration_event = HydrationCreatedEvent(
                environment=get_settings().ENVIRONMENT,
                aggregate_id=hydration_entry.id,
            )
            await uow.outbox.enqueue(
                integration_event.event_type,
                integration_event.to_payload(),
                event_id=integration_event.event_id,
                aggregate_type=integration_event.aggregate_type,
                aggregate_id=integration_event.aggregate_id,
            )

        kcal = round(saved.nutrition.calories if saved.nutrition else 0.0, 1)
        return {
            "id": hydration_entry.id,
            "drink_id": cmd.drink_id,
            "drink_name": localized_name(drink, cmd.language),
            "emoji": drink.emoji,
            "volume_ml": cmd.volume_ml,
            "credited_ml": hydration_entry.credited_ml,
            "kcal": kcal,
            "calories": kcal,
            "source": "hydration",
            "meal_id": saved.meal_id,
            "logged_at": format_iso_utc(hydration_entry.logged_at),
        }
