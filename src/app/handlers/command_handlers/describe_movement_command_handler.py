"""Command handler that logs every activity in one sentence (Ghi nhanh)."""

import logging
import math
import time
from datetime import date, datetime, timedelta
from typing import Any

from src.api.exceptions import BusinessLogicException, ValidationException
from src.app.commands.movement import DescribeMovementCommand, LogMovementCommand
from src.app.events.base import EventHandler, handles
from src.app.events.movement.local_cache_invalidation import (
    invoke_local_movement_cache_invalidation_hook,
)
from src.app.events.movement.movement_created_event import MovementCreatedEvent
from src.app.handlers.command_handlers.log_movement_command_handler import (
    _movement_response,
    _validate_log_movement,
)
from src.app.services.describe_log_parser import clamp, parse_describe_sentence
from src.app.services.describe_log_replay import (
    MOVEMENT_DESCRIBE_OPERATION,
    complete,
    describe_key,
    lease_seconds_for,
    run_once,
)
from src.app.services.describe_movement_items import (
    DescribedActivity,
    normalize_activities,
)
from src.domain.model.ai.describe_log_contracts import ActivityLogParse
from src.domain.model.movement import MovementEntry
from src.domain.ports.integration_event_publisher_port import (
    IntegrationEventPublisherPort,
    require_event_publisher,
)
from src.domain.ports.meal_generation_service_port import MealGenerationServicePort
from src.domain.services.movement_catalog_service import active_kcal
from src.domain.services.prompts.system_prompts import SystemPrompts
from src.domain.utils.timezone_utils import (
    get_zone_info,
    noon_utc_for_date,
    resolve_user_timezone_async,
    utc_now,
)

logger = logging.getLogger(__name__)

# The app prices activities with 62 kg when it has no weight for the person.
DEFAULT_WEIGHT_KG = 62.0
WEIGHT_RANGE_KG = (30.0, 300.0)
# _validate_log_movement's bounds: at most 5000 kcal and 30 kcal a minute.
MAX_KCAL = 5000.0
MAX_KCAL_PER_MINUTE = 30.0


@handles(DescribeMovementCommand)
class DescribeMovementCommandHandler(EventHandler[DescribeMovementCommand, dict]):
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

    async def handle(self, cmd: DescribeMovementCommand) -> dict:
        key = describe_key(
            MOVEMENT_DESCRIBE_OPERATION, cmd.idempotency_key, cmd.request_fingerprint
        )
        return await run_once(
            self.uow_factory,
            cmd.user_id,
            key,
            lease_seconds=lease_seconds_for(self.ai_timeout_seconds),
            log=lambda reservation: self._log(cmd, reservation),
        )

    async def _log(self, cmd: DescribeMovementCommand, reservation: Any) -> dict:
        started = time.monotonic()
        log_date, logged_at, weight_kg = await self._prepare(cmd)
        parse = await parse_describe_sentence(
            self.meal_generation_service,
            sentence=cmd.text,
            system_prompt=SystemPrompts.get_activity_log_parsing_prompt(),
            schema=ActivityLogParse,
            purpose="describe_movement",
            timeout_seconds=self.ai_timeout_seconds,
        )
        described = normalize_activities(parse["items"])
        if not described.activities:
            raise BusinessLogicException(
                "No activity was recognised in the sentence",
                "DESCRIBE_NOTHING_RECOGNIZED",
            )
        commands = [
            _log_command(cmd, activity, weight_kg) for activity in described.activities
        ]
        for command in commands:
            _validate_log_movement(command)

        async with self.uow_factory() as uow:
            saved = [
                await uow.movement_entries.add(_entry(command, logged_at))
                for command in commands
            ]
            response = {"entries": [_movement_response(entry) for entry in saved]}
            if reservation is not None:
                # Same transaction as the entries: saved ⇔ reply stored.
                await complete(uow, reservation, response)

        await self._after_commit(cmd.user_id, log_date, saved)
        logger.info(
            "[DESCRIBE-LOG-COMPLETE] kind=movement items=%d unlisted=%d "
            "dropped=%d overflow=%d total_ms=%.0f",
            len(saved),
            sum(1 for activity in described.activities if activity.activity_id is None),
            described.dropped,
            described.overflow,
            (time.monotonic() - started) * 1000,
        )
        return response

    async def _prepare(
        self, cmd: DescribeMovementCommand
    ) -> tuple[date, datetime, float]:
        """Day, instant and weight for every entry, checked before the model runs."""
        async with self.uow_factory() as uow:
            user_tz = await resolve_user_timezone_async(
                cmd.user_id, uow, cmd.header_timezone
            )
            weight_kg = _usable_weight(cmd.weight_kg) or await _profile_weight(
                uow, cmd.user_id
            )
        now = utc_now()
        today = now.astimezone(get_zone_info(user_tz)).date()
        log_date = cmd.target_date or today
        if log_date > today + timedelta(days=1):
            raise ValidationException(
                "Movement date cannot be more than one day in the future",
                "INVALID_DATE",
            )
        logged_at = noon_utc_for_date(log_date, user_tz) if cmd.target_date else now
        return log_date, logged_at, weight_kg or DEFAULT_WEIGHT_KG

    async def _after_commit(
        self, user_id: str, log_date: date, saved: list[MovementEntry]
    ) -> None:
        """Publish and purge like POST /v1/movement/log; the entries are saved."""
        for entry in saved:
            event = MovementCreatedEvent(
                environment=self.environment,
                aggregate_id=entry.id,
                data={"user_id": user_id, "log_date": log_date.isoformat()},
            )
            try:
                await require_event_publisher(self.event_publisher).publish(
                    event.to_payload()
                )
            except Exception:
                logger.warning(
                    "Describe movement event publish failed aggregate_id=%s",
                    entry.id,
                    exc_info=True,
                )
        try:
            await invoke_local_movement_cache_invalidation_hook(user_id, log_date)
        except Exception:
            logger.warning("Describe movement cache purge failed", exc_info=True)


async def _profile_weight(uow: Any, user_id: str) -> float | None:
    try:
        profile = await uow.users.get_profile(user_id)
    except Exception:
        logger.warning("Describe movement could not read the profile weight")
        return None
    weight = getattr(profile, "weight_kg", None)
    return _usable_weight(weight) if isinstance(weight, (int, float)) else None


def _usable_weight(weight: float | None) -> float | None:
    """A positive weight clamped to 30-300 kg; anything else counts as missing."""
    if weight is None or not math.isfinite(weight) or weight <= 0:
        return None
    return clamp(float(weight), *WEIGHT_RANGE_KG)


def _log_command(
    cmd: DescribeMovementCommand, activity: DescribedActivity, weight_kg: float
) -> LogMovementCommand:
    kcal = active_kcal(activity.met, weight_kg, activity.duration_min)
    kcal = min(kcal, MAX_KCAL, activity.duration_min * MAX_KCAL_PER_MINUTE)
    return LogMovementCommand(
        user_id=cmd.user_id,
        activity_name=activity.name,
        duration_min=activity.duration_min,
        kcal_burned=kcal,
        intensity=activity.intensity,
        include_in_balance=True,
        activity_id=activity.activity_id,
        target_date=cmd.target_date,
        header_timezone=cmd.header_timezone,
    )


def _entry(command: LogMovementCommand, logged_at: datetime) -> MovementEntry:
    return MovementEntry(
        user_id=command.user_id,
        activity_id=command.activity_id,
        activity_name=command.activity_name,
        duration_min=command.duration_min,
        kcal_burned=command.kcal_burned,
        intensity=command.intensity,
        include_in_balance=command.include_in_balance,
        logged_at=logged_at,
    )
