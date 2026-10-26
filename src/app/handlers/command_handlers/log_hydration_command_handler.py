"""Command handler for logging a hydration (non-caloric) drink entry."""

import logging
from typing import Any

from src.app.commands.hydration.log_hydration_command import LogHydrationCommand
from src.app.events.base import EventHandler, handles
from src.app.events.hydration.hydration_created_event import HydrationCreatedEvent
from src.app.events.hydration.local_cache_invalidation import (
    invoke_local_hydration_cache_invalidation_hook,
)
from src.app.services.hydration_entry_factory import water_macros, water_meal_for
from src.domain.model.hydration import DrinkCategory, HydrationEntry
from src.domain.ports.integration_event_publisher_port import (
    IntegrationEventPublisherPort,
    require_event_publisher,
)
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


@handles(LogHydrationCommand)
class LogHydrationCommandHandler(EventHandler[LogHydrationCommand, dict]):
    def __init__(
        self,
        uow: AsyncUnitOfWork | None = None,
        uow_factory: Any = None,
        event_publisher: IntegrationEventPublisherPort | None = None,
        environment: str = "development",
    ):
        self.uow_factory: Any = uow_factory or (lambda: uow)
        self.environment = environment
        self.event_publisher = event_publisher

    async def handle(self, cmd: LogHydrationCommand) -> dict:
        drink = find_by_id(cmd.drink_id)
        if drink is None:
            raise ValueError(f"Unknown drink: {cmd.drink_id}")
        if drink.category != DrinkCategory.HYDRATION:
            raise ValueError("Drink is not a hydration drink")

        async with self.uow_factory() as uow:
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
            meal = water_meal_for(drink, cmd.volume_ml, log_dt, cmd.user_id)
            saved = await uow.meals.save(meal)
            macros = (
                saved.nutrition.macros
                if saved.nutrition
                else water_macros(drink, cmd.volume_ml)
            )
            hydration_entry = await uow.hydration_entries.add(
                HydrationEntry(
                    user_id=cmd.user_id,
                    drink_id=cmd.drink_id,
                    drink_name_snapshot=drink.name,
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
                environment=self.environment,
                aggregate_id=hydration_entry.id,
            )

        await require_event_publisher(self.event_publisher).publish(
            integration_event.to_payload()
        )
        logger.info(
            "Published hydration integration event event_id=%s aggregate_id=%s",
            integration_event.event_id,
            integration_event.aggregate_id,
        )
        await invoke_local_hydration_cache_invalidation_hook(cmd.user_id, log_date)

        kcal = round(saved.nutrition.calories if saved.nutrition else 0.0, 1)
        return {
            "id": hydration_entry.id,
            "drink_id": cmd.drink_id,
            "drink_name": localized_name(drink, cmd.language),
            "volume_ml": cmd.volume_ml,
            "credited_ml": hydration_entry.credited_ml,
            "kcal": kcal,
            "calories": kcal,
            "source": "hydration",
            "meal_id": saved.meal_id,
            "logged_at": format_iso_utc(hydration_entry.logged_at),
        }
