"""Command handler for logging a caloric drink entry."""

import logging

from src.app.commands.hydration.log_caloric_drink_command import LogCaloricDrinkCommand
from src.app.events.base import EventHandler, handles
from src.app.services.cache_invalidation_service import CacheInvalidationService
from src.domain.model.hydration import DrinkCategory, HydrationEntry
from src.domain.model.nutrition.macros import Macros
from src.domain.services.hydration_catalog_service import find_by_id, localized_name
from src.domain.utils.timezone_utils import (
    format_iso_utc,
    get_zone_info,
    noon_utc_for_date,
    resolve_user_timezone_async,
    utc_now,
)
from src.infra.database.uow_async import AsyncUnitOfWork

logger = logging.getLogger(__name__)


@handles(LogCaloricDrinkCommand)
class LogCaloricDrinkCommandHandler(EventHandler[LogCaloricDrinkCommand, dict]):
    def __init__(
        self,
        uow: AsyncUnitOfWork,
        cache_invalidation: CacheInvalidationService | None = None,
    ):
        self.uow = uow
        self.cache_invalidation = cache_invalidation

    async def handle(self, cmd: LogCaloricDrinkCommand) -> dict:
        drink = find_by_id(cmd.drink_id)
        if drink is None:
            raise ValueError(f"Unknown drink: {cmd.drink_id}")
        if drink.category != DrinkCategory.CALORIC:
            raise ValueError("Drink is not a caloric drink")

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

            # Scale macros from per-100ml catalog values
            fat_100ml = max(0.0, (drink.kcal_per_100ml - drink.sugar_per_100ml * 4) / 9)
            carbs_100ml = max(0.0, (drink.kcal_per_100ml - fat_100ml * 9) / 4)
            volume_factor = cmd.volume_ml / 100.0
            credited_ml = drink.credited_ml_for_volume(cmd.volume_ml)

            carbs = round(carbs_100ml * volume_factor, 1)
            fat = round(fat_100ml * volume_factor, 1)
            sugar = round(drink.sugar_per_100ml * volume_factor, 1)

            hydration_entry = await uow.hydration_entries.add(
                HydrationEntry(
                    user_id=cmd.user_id,
                    drink_id=cmd.drink_id,
                    drink_name_snapshot=drink.name,
                    emoji_snapshot=drink.emoji,
                    volume_ml=cmd.volume_ml,
                    credited_ml=credited_ml,
                    protein_g=0.0,
                    carbs_g=carbs,
                    fat_g=fat,
                    fiber_g=0.0,
                    sugar_g=sugar,
                    logged_at=log_dt,
                    source="hydration",
                )
            )
            if self.cache_invalidation and getattr(uow, "outbox", None) is not None:
                await self.cache_invalidation.enqueue_hydration_invalidation(
                    uow.outbox, cmd.user_id, log_date
                )
            elif self.cache_invalidation:
                await self.cache_invalidation.after_hydration_write(
                    cmd.user_id, log_date
                )

        kcal = round(
            Macros(
                protein=0.0, carbs=carbs, fat=fat, fiber=0.0, sugar=sugar
            ).total_calories,
            1,
        )
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
            "meal_id": hydration_entry.id,  # backward-compat: hydration entry id
            "logged_at": format_iso_utc(hydration_entry.logged_at),
        }
