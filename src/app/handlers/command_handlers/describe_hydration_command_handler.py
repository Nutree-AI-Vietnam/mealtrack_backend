"""Command handler that logs every drink in one sentence (Ghi nhanh)."""

import logging
import time
from datetime import date, datetime, timedelta
from typing import Any

from src.api.exceptions import BusinessLogicException, ValidationException
from src.app.commands.hydration.describe_hydration_command import (
    DescribeHydrationCommand,
)
from src.app.events.base import EventHandler, handles
from src.app.events.hydration.hydration_caloric_created_event import (
    HydrationCaloricCreatedEvent,
)
from src.app.events.hydration.hydration_created_event import HydrationCreatedEvent
from src.app.events.hydration.local_cache_invalidation import (
    invoke_local_hydration_cache_invalidation_hook,
)
from src.app.events.integration_event import IntegrationEvent
from src.app.services.describe_hydration_items import DescribedDrink, normalize_drinks
from src.app.services.describe_log_parser import parse_describe_sentence
from src.app.services.describe_log_replay import (
    HYDRATION_DESCRIBE_OPERATION,
    complete,
    describe_key,
    lease_seconds_for,
    run_once,
)
from src.app.services.hydration_entry_factory import (
    catalog_macros,
    water_macros,
    water_meal_for,
)
from src.domain.model.ai.describe_log_contracts import DrinkLogParse
from src.domain.model.hydration import DrinkCategory, HydrationEntry
from src.domain.ports.integration_event_publisher_port import (
    IntegrationEventPublisherPort,
    require_event_publisher,
)
from src.domain.ports.meal_generation_service_port import MealGenerationServicePort
from src.domain.services.hydration_catalog_service import (
    localized_name_for_catalog_name,
)
from src.domain.services.prompts.system_prompts import SystemPrompts
from src.domain.utils.timezone_utils import (
    format_iso_utc,
    get_zone_info,
    is_same_day_log,
    log_moment,
    resolve_user_timezone_async,
    utc_now,
)

logger = logging.getLogger(__name__)


@handles(DescribeHydrationCommand)
class DescribeHydrationCommandHandler(EventHandler[DescribeHydrationCommand, dict]):
    def __init__(
        self,
        uow_factory: Any,
        meal_generation_service: MealGenerationServicePort,
        event_publisher: IntegrationEventPublisherPort | None = None,
        environment: str = "development",
        ai_timeout_seconds: float = 10.0,
    ):
        self.uow_factory = uow_factory
        self.meal_generation_service = meal_generation_service
        self.event_publisher = event_publisher
        self.environment = environment
        self.ai_timeout_seconds = ai_timeout_seconds

    async def handle(self, cmd: DescribeHydrationCommand) -> dict:
        key = describe_key(
            HYDRATION_DESCRIBE_OPERATION, cmd.idempotency_key, cmd.request_fingerprint
        )
        return await run_once(
            self.uow_factory,
            cmd.user_id,
            key,
            lease_seconds=lease_seconds_for(self.ai_timeout_seconds),
            log=lambda reservation: self._log(cmd, reservation),
        )

    async def _log(self, cmd: DescribeHydrationCommand, reservation: Any) -> dict:
        started = time.monotonic()
        log_date, log_dt, logged_on_day = await self._log_moment(cmd)
        parse = await parse_describe_sentence(
            self.meal_generation_service,
            sentence=cmd.text,
            system_prompt=SystemPrompts.get_drink_log_parsing_prompt(),
            schema=DrinkLogParse,
            purpose="describe_hydration",
            timeout_seconds=self.ai_timeout_seconds,
        )
        described = normalize_drinks(parse["items"])
        if not described.drinks:
            raise BusinessLogicException(
                "No drink was recognised in the sentence",
                "DESCRIBE_NOTHING_RECOGNIZED",
            )

        saved: list[tuple[HydrationEntry, str | None, IntegrationEvent]] = []
        async with self.uow_factory() as uow:
            for drink in described.drinks:
                saved.append(
                    await self._save(
                        uow, cmd.user_id, drink, log_dt, log_date, logged_on_day
                    )
                )
            response = {
                "entries": [
                    _entry_response(entry, meal_id, cmd.language)
                    for entry, meal_id, _ in saved
                ]
            }
            if reservation is not None:
                # Same transaction as the entries: saved ⇔ reply stored.
                await complete(uow, reservation, response)

        await self._after_commit(cmd.user_id, log_date, [event for *_, event in saved])
        logger.info(
            "[DESCRIBE-LOG-COMPLETE] kind=hydration items=%d unlisted=%d "
            "dropped=%d overflow=%d total_ms=%.0f",
            len(saved),
            sum(1 for drink in described.drinks if drink.drink is None),
            described.dropped,
            described.overflow,
            (time.monotonic() - started) * 1000,
        )
        return response

    async def _log_moment(
        self, cmd: DescribeHydrationCommand
    ) -> tuple[date, datetime, bool]:
        """The day, instant and same-day flag of every entry, fixed before the model runs."""
        async with self.uow_factory() as uow:
            user_tz = await resolve_user_timezone_async(
                cmd.user_id, uow, cmd.header_timezone
            )
        now = utc_now()
        today = now.astimezone(get_zone_info(user_tz)).date()
        if cmd.target_date is None:
            return today, now, True
        if cmd.target_date > today + timedelta(days=1):
            raise ValidationException(
                "Hydration date cannot be more than one day in the future",
                "INVALID_DATE",
            )
        return (
            cmd.target_date,
            log_moment(cmd.target_date, user_tz, now),
            is_same_day_log(cmd.target_date, user_tz, now),
        )

    async def _save(
        self,
        uow: Any,
        user_id: str,
        drink: DescribedDrink,
        log_dt: datetime,
        log_date: date,
        logged_on_day: bool,
    ) -> tuple[HydrationEntry, str | None, IntegrationEvent]:
        catalog = drink.drink
        meal_id = None
        if catalog is not None and catalog.category == DrinkCategory.HYDRATION:
            # Same legacy meal + entry path as POST /v1/hydration/log.
            meal = await uow.meals.save(
                water_meal_for(
                    catalog,
                    drink.volume_ml,
                    log_dt,
                    user_id,
                    logged_on_day=logged_on_day,
                )
            )
            macros = (
                meal.nutrition.macros
                if meal.nutrition
                else water_macros(catalog, drink.volume_ml)
            )
            meal_id = meal.meal_id
        elif catalog is not None:
            macros = catalog_macros(catalog, drink.volume_ml)
        else:
            macros = drink.unlisted_macros()

        entry = await uow.hydration_entries.add(
            HydrationEntry(
                user_id=user_id,
                drink_id=drink.drink_id,
                drink_name_snapshot=drink.name,
                volume_ml=drink.volume_ml,
                credited_ml=drink.credited_ml,
                protein_g=macros.protein,
                carbs_g=macros.carbs,
                fat_g=macros.fat,
                fiber_g=macros.fiber,
                sugar_g=macros.sugar,
                logged_at=log_dt,
                source="hydration",
                legacy_meal_id=meal_id,
            )
        )
        event: IntegrationEvent
        if meal_id is not None:
            event = HydrationCreatedEvent(
                environment=self.environment, aggregate_id=entry.id
            )
        else:
            event = HydrationCaloricCreatedEvent(
                environment=self.environment,
                aggregate_id=entry.id,
                data={"user_id": user_id, "log_date": log_date.isoformat()},
            )
        return entry, meal_id, event

    async def _after_commit(
        self, user_id: str, log_date: date, events: list[IntegrationEvent]
    ) -> None:
        """Publish and purge like the tap paths; the entries are already saved."""
        for event in events:
            try:
                await require_event_publisher(self.event_publisher).publish(
                    event.to_payload()
                )
            except Exception:
                logger.warning(
                    "Describe hydration event publish failed aggregate_id=%s",
                    event.aggregate_id,
                    exc_info=True,
                )
        try:
            await invoke_local_hydration_cache_invalidation_hook(user_id, log_date)
        except Exception:
            logger.warning("Describe hydration cache purge failed", exc_info=True)


def _entry_response(entry: HydrationEntry, meal_id: str | None, language: str) -> dict:
    return {
        "id": entry.id,
        "drink_id": entry.drink_id,
        # What the day feed shows: the localised catalog name, or the person's words.
        "name": localized_name_for_catalog_name(
            entry.drink_name_snapshot, language, entry.drink_id
        ),
        "volume_ml": entry.volume_ml,
        "credited_ml": entry.credited_ml,
        "protein_g": entry.protein_g,
        "carbs_g": entry.carbs_g,
        "fat_g": entry.fat_g,
        "fiber_g": entry.fiber_g,
        "sugar_g": entry.sugar_g,
        "kcal": entry.calories,
        "logged_as_caloric": meal_id is None,
        "logged_at": format_iso_utc(entry.logged_at),
        "meal_id": meal_id,
    }
