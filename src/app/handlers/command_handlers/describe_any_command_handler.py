"""The + panel's Ghi nhanh: one sentence saves food, drinks, activities and doses.

Read first, with no transaction open: one model call splits the sentence,
supplements are matched against the person's list and the catalog, and the
food goes through the parse-text pipeline (a second model call with its
FatSecret and serving lookups). Then one unit of work saves the meal, the
drinks, the activities and the doses and stores the reply with the ids it
created, so either everything is saved or nothing is. Publishing and cache
purges follow the commit.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any
from uuid import uuid4

from src.api.exceptions import ValidationException
from src.app.commands.describe_any import DescribeAnyCommand
from src.app.commands.meal.create_manual_meal_command import CreateManualMealCommand
from src.app.commands.meal.parse_meal_text_command import ParseMealTextCommand
from src.app.events.base import EventHandler, handles
from src.app.handlers.command_handlers.create_manual_meal_command_handler import (
    CreateManualMealCommandHandler,
)
from src.app.handlers.command_handlers.describe_hydration_command_handler import (
    DrinkMoment,
    drink_log_moment,
    publish_hydration_writes,
    save_described_drink,
)
from src.app.handlers.command_handlers.describe_movement_command_handler import (
    DEFAULT_WEIGHT_KG,
    described_movement_entry,
    movement_log_moment,
    movement_weight,
    publish_movement_writes,
)
from src.app.handlers.command_handlers.supplement_command_handlers import (
    SupplementWrite,
)
from src.app.services.describe_any_items import (
    ACTIVITY,
    DRINK,
    FOOD,
    NOT_REQUESTED,
    SUPPLEMENT,
    DescribedSupplement,
    SentencePlan,
    dish_name,
    meal_items,
    meal_type_at,
    plan_sentence,
)
from src.app.services.describe_any_reply import SavedSentence
from src.app.services.describe_any_supplements import (
    SupplementShelf,
    find_product,
    load_shelf,
)
from src.app.services.describe_log_parser import parse_describe_sentence
from src.app.services.describe_log_replay import (
    LOG_DESCRIBE_OPERATION,
    complete,
    describe_key,
    lease_seconds_for,
    run_once,
)
from src.app.services.describe_unlisted_estimates import (
    UNLISTED_ESTIMATE_TIMEOUT_SECONDS,
    fill_unlisted_estimates,
)
from src.domain.exceptions.ai_exceptions import AIOutputValidationError
from src.domain.model.ai.describe_any_contracts import DescribeAnyParse
from src.domain.model.supplement import SupplementProduct
from src.domain.model.supplement.describe_estimate import estimated_product
from src.domain.ports.integration_event_publisher_port import (
    IntegrationEventPublisherPort,
)
from src.domain.ports.meal_generation_service_port import MealGenerationServicePort
from src.domain.services.prompts.system_prompts import SystemPrompts
from src.domain.utils.timezone_utils import (
    get_zone_info,
    log_moment,
    resolve_user_timezone_async,
    utc_now,
)

logger = logging.getLogger(__name__)

# Supplement estimates make the reply longer than a water or activity one.
DESCRIBE_ANY_MAX_TOKENS = 3000
NOT_RECOGNIZED = "not_recognized"
# Inside the dose rule's 366 days, whatever the hour of a backdated noon stamp.
_MAX_BACKDATE = timedelta(days=365)
_MEAL_SOURCE = "prompt"  # what the app sends for a typed meal


@dataclass
class _Read:
    user_tz: str
    plan: SentencePlan
    # The day asked for; None for the person's today, so every kind keeps
    # its real time instead of a backdated noon stamp.
    target_date: date | None = None
    weight_kg: float = DEFAULT_WEIGHT_KG
    supplements: list[tuple[DescribedSupplement, SupplementProduct | None]] = field(
        default_factory=list
    )
    food: Any = None  # ParseMealTextResponseDto with at least one item


@handles(DescribeAnyCommand)
class DescribeAnyCommandHandler(EventHandler[DescribeAnyCommand, dict]):
    def __init__(
        self,
        uow_factory: Any,
        meal_generation_service: MealGenerationServicePort,
        event_bus: Any,
        meal_writer: CreateManualMealCommandHandler,
        supplement_writer: SupplementWrite,
        supplements_available: Callable[[], Awaitable[bool]],
        event_publisher: IntegrationEventPublisherPort | None = None,
        environment: str = "development",
        ai_timeout_seconds: float = 10.0,
        food_timeout_seconds: float = 10.0,
    ):
        self.uow_factory = uow_factory
        self.meal_generation_service = meal_generation_service
        self.event_bus = event_bus
        self.meal_writer = meal_writer
        self.supplement_writer = supplement_writer
        self.supplements_available = supplements_available
        self.event_publisher = event_publisher
        self.environment = environment
        self.ai_timeout_seconds = ai_timeout_seconds
        self.food_timeout_seconds = food_timeout_seconds

    async def handle(self, cmd: DescribeAnyCommand) -> dict:
        key = describe_key(
            LOG_DESCRIBE_OPERATION, cmd.idempotency_key, cmd.request_fingerprint
        )
        return await run_once(
            self.uow_factory,
            cmd.user_id,
            key,
            lease_seconds=lease_seconds_for(
                self.ai_timeout_seconds
                + UNLISTED_ESTIMATE_TIMEOUT_SECONDS
                # parse-text may be asked twice (see _read_food)
                + 2 * self.food_timeout_seconds
            ),
            log=lambda reservation: self._log(cmd, reservation),
        )

    async def _log(self, cmd: DescribeAnyCommand, reservation: Any) -> dict:
        started = time.monotonic()
        now = utc_now()
        read = await self._read(cmd, now)
        async with self.uow_factory() as uow:
            saved = await self._save(uow, cmd, read, now)
            reply = saved.reply(cmd.user_id, cmd.language, read.plan.skipped)
            if reservation is not None:
                # Same transaction as every entry: saved ⇔ reply stored.
                await complete(uow, reservation, reply, saved.created())
        await self._after_commit(cmd, saved)
        logger.info(
            "[DESCRIBE-LOG-COMPLETE] kind=any food=%d drinks=%d activities=%d "
            "doses=%d estimated=%d skipped=%d dropped=%d total_ms=%.0f",
            1 if saved.meal else 0,
            len(saved.drinks),
            len(saved.activities),
            len(saved.doses),
            sum(1 for _, product, _ in saved.doses if product.estimated),
            len(read.plan.skipped),
            read.plan.dropped,
            (time.monotonic() - started) * 1000,
        )
        return reply

    # --- read: no transaction open ------------------------------------------

    async def _read(self, cmd: DescribeAnyCommand, now: datetime) -> _Read:
        supplements_on = SUPPLEMENT in cmd.kinds and await self.supplements_available()
        async with self.uow_factory() as uow:
            user_tz = await resolve_user_timezone_async(
                cmd.user_id, uow, cmd.header_timezone
            )
            weight_kg = (
                await movement_weight(uow, cmd.user_id, None)
                if ACTIVITY in cmd.kinds
                else DEFAULT_WEIGHT_KG
            )
            shelf = (
                await load_shelf(uow, cmd.user_id)
                if supplements_on
                else SupplementShelf()
            )
        target_date = _sentence_day(cmd.target_date, user_tz, now)
        parse = await parse_describe_sentence(
            self.meal_generation_service,
            sentence=cmd.text,
            system_prompt=SystemPrompts.get_describe_any_parsing_prompt(),
            schema=DescribeAnyParse,
            purpose="describe_any",
            timeout_seconds=self.ai_timeout_seconds,
            model_purpose="describe_log",
            context=shelf.block(),
            max_tokens=DESCRIBE_ANY_MAX_TOKENS,
        )
        await fill_unlisted_estimates(
            self.meal_generation_service,
            activities=(parse.get("activities") or []) if ACTIVITY in cmd.kinds else [],
            drinks=(parse.get("drinks") or []) if DRINK in cmd.kinds else [],
            timeout_seconds=min(
                self.ai_timeout_seconds, UNLISTED_ESTIMATE_TIMEOUT_SECONDS
            ),
            model_purpose="describe_log",
        )
        read = _Read(user_tz, plan_sentence(parse, cmd.kinds, supplements_on))
        read.target_date, read.weight_kg = target_date, weight_kg
        if read.plan.supplements:
            await self._match_supplements(cmd, read, shelf)
        if read.plan.food_text:
            read.food = await self._read_food(cmd, read.plan)
        return read

    async def _match_supplements(
        self, cmd: DescribeAnyCommand, read: _Read, shelf: SupplementShelf
    ) -> None:
        async with self.uow_factory() as uow:
            for item in read.plan.supplements:
                product = await find_product(uow, cmd.user_id, item, shelf)
                if product is not None and product.macro_bearing:
                    # A protein powder found in the catalog carries calories.
                    if FOOD in cmd.kinds:
                        read.plan.add_food(item.words)
                    else:
                        read.plan.skip(FOOD, item.name, NOT_REQUESTED)
                    continue
                read.supplements.append((item, product))

    async def _read_food(self, cmd: DescribeAnyCommand, plan: SentencePlan) -> Any:
        command = ParseMealTextCommand(
            text=plan.food_text or "", language=cmd.language, user_id=cmd.user_id
        )
        try:
            parsed = await self.event_bus.send(command)
        except AIOutputValidationError:
            # One food without a usable estimate fails the whole parse. Ask
            # once more; if it fails again, keep the sentence's other kinds
            # and report the food as skipped rather than lose everything.
            try:
                parsed = await self.event_bus.send(command)
            except AIOutputValidationError:
                logger.warning("[DESCRIBE-LOG-FOOD-UNREADABLE] after_retry=1")
                plan.skip(FOOD, plan.food_text or "", NOT_RECOGNIZED)
                return None
        if not parsed.items:
            plan.skip(FOOD, plan.food_text or "", NOT_RECOGNIZED)
            return None
        return parsed

    # --- save: one unit of work ----------------------------------------------

    async def _save(
        self, uow: Any, cmd: DescribeAnyCommand, read: _Read, now: datetime
    ) -> SavedSentence:
        target = read.target_date
        saved = SavedSentence(
            target or now.astimezone(get_zone_info(read.user_tz)).date()
        )
        if read.food is not None:
            saved.meal, saved.meal_day = await self.meal_writer.save_prepared_meal(
                uow, self._meal_command(cmd, read, now), now
            )
        moment = DrinkMoment(*drink_log_moment(target, read.user_tz, now))
        for drink in read.plan.drinks:
            saved.drinks.append(
                await save_described_drink(
                    uow, cmd.user_id, drink, moment, self.environment
                )
            )
        _, active_at = movement_log_moment(target, read.user_tz, now)
        for activity in read.plan.activities:
            entry = described_movement_entry(
                cmd.user_id, activity, read.weight_kg, active_at
            )
            saved.activities.append(await uow.movement_entries.add(entry))
        # A dose is never in the future: tomorrow's sentence logs it now.
        taken_at = min(log_moment(target, read.user_tz, now), now)
        for item, product in read.supplements:
            saved.doses.append(await self._save_dose(uow, cmd, item, product, taken_at))
        return saved

    def _meal_command(
        self, cmd: DescribeAnyCommand, read: _Read, now: datetime
    ) -> CreateManualMealCommand:
        local_time = (
            cmd.local_time or now.astimezone(get_zone_info(read.user_tz)).time()
        )
        return CreateManualMealCommand(
            user_id=cmd.user_id,
            items=meal_items(read.food.items),
            dish_name=dish_name(read.food.items),
            meal_type=meal_type_at(local_time),
            target_date=read.target_date,
            source=_MEAL_SOURCE,
            emoji=read.food.emoji,
            nutrition_contract_version=2,
            language=cmd.language,
        )

    async def _save_dose(
        self,
        uow: Any,
        cmd: DescribeAnyCommand,
        item: DescribedSupplement,
        product: SupplementProduct | None,
        taken_at: datetime,
    ) -> tuple[Any, SupplementProduct, list[Any]]:
        if product is None:
            # Never added and not in the catalog: the person's own estimate.
            product = estimated_product(
                product_id=str(uuid4()),
                owner_user_id=cmd.user_id,
                name=item.name,
                form=item.form,
                rows=item.rows,
            )
            await uow.supplements.add_product(product)
        dose = await self.supplement_writer.save_dose(
            uow,
            user_id=cmd.user_id,
            product=product,
            intake_id=str(uuid4()),
            units=item.units,
            taken_at=taken_at,
            header_timezone=cmd.header_timezone,
        )
        day_doses = await uow.supplements.intakes_between(
            cmd.user_id, dose.intake.local_date, dose.intake.local_date
        )
        return (
            dose,
            product,
            [one for one in day_doses if one.product_id == product.id],
        )

    # --- after commit ---------------------------------------------------------

    async def _after_commit(
        self, cmd: DescribeAnyCommand, saved: SavedSentence
    ) -> None:
        """Publish and purge like each kind's own route; everything is saved."""
        work: list[Awaitable[None]] = []
        if saved.meal is not None:
            # The meal's own day: it can pass midnight after the drinks did.
            work.append(
                self.meal_writer.publish_created(
                    saved.meal,
                    saved.meal_day or saved.day,
                    cmd.language,
                    source="describe_any",
                )
            )
        if saved.drinks:
            work.append(
                publish_hydration_writes(
                    self.event_publisher,
                    cmd.user_id,
                    saved.day,
                    [drink.event for drink in saved.drinks],
                )
            )
        if saved.activities:
            work.append(
                publish_movement_writes(
                    self.event_publisher,
                    self.environment,
                    cmd.user_id,
                    saved.day,
                    saved.activities,
                )
            )
        for day in sorted({dose.intake.local_date for dose, _, _ in saved.doses}):
            work.append(self.supplement_writer._purge(cmd.user_id, day))
        for result in await asyncio.gather(*work, return_exceptions=True):
            if isinstance(result, Exception):
                logger.warning("Describe any publish or purge failed", exc_info=result)


def _sentence_day(target_date: date | None, user_tz: str, now: datetime) -> date | None:
    """The day to log, checked once for every kind: at most tomorrow (the water
    and activity rule) and within the last year (the dose rule). Today is None."""
    today = now.astimezone(get_zone_info(user_tz)).date()
    if target_date is None or target_date == today:
        return None
    if target_date > today + timedelta(days=1):
        raise ValidationException(
            "Log date cannot be more than one day in the future", "INVALID_DATE"
        )
    if target_date < today - _MAX_BACKDATE:
        raise ValidationException(
            "Log date must be within the last year", "INVALID_DATE"
        )
    return target_date
