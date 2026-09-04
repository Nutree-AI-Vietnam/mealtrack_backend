"""Handler for logging a catalog meal with prefer-slot."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import date
from typing import Any

from src.api.exceptions import ConflictException, ResourceNotFoundException
from src.app.commands.meal_catalog.log_catalog_meal_command import (
    LogCatalogMealCommand,
)
from src.app.events.base import EventHandler, handles
from src.app.events.meal.meal_events import publish_meal_event
from src.app.services.catalog_meal_log_service import (
    CatalogMealLogService,
    LogCatalogMealResult,
)
from src.app.services.meal_translation_persistence import persist_meal_translation
from src.app.services.remaining_recommendation_recalculator import (
    RemainingRecommendationRecalculator,
)
from src.domain.ports.integration_event_publisher_port import (
    IntegrationEventPublisherPort,
)
from src.domain.services.meal_analysis.meal_translation_service import (
    MealTranslationService,
)

logger = logging.getLogger(__name__)


def catalog_log_fingerprint(
    catalog_meal_id: str,
    meal_date: date,
    meal_type: str | None = None,
) -> str:
    payload = {
        "catalog_meal_id": catalog_meal_id,
        "meal_date": meal_date.isoformat(),
        "meal_type": meal_type,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


@handles(LogCatalogMealCommand)
class LogCatalogMealCommandHandler(
    EventHandler[LogCatalogMealCommand, LogCatalogMealResult]
):
    def __init__(
        self,
        uow=None,
        uow_factory: Any = None,
        browse_service=None,
        *,
        log_service: CatalogMealLogService | None = None,
        meal_translation_service: MealTranslationService | None = None,
        event_publisher: IntegrationEventPublisherPort | None = None,
        event_bus: Any | None = None,
        environment: str = "development",
        recalculator: RemainingRecommendationRecalculator | None = None,
    ) -> None:
        self.uow_factory: Any = uow_factory or (lambda: uow)
        self.browse_service = browse_service
        self.log_service = log_service or CatalogMealLogService()
        self.meal_translation_service = meal_translation_service
        self.event_publisher = event_publisher
        self.event_bus = event_bus
        self.environment = environment
        self.recalculator = recalculator

    async def handle(self, command: LogCatalogMealCommand) -> LogCatalogMealResult:
        try:
            catalog_meal = await self.browse_service.get_meal(command.catalog_meal_id)
        except KeyError as exc:
            raise ResourceNotFoundException("Catalog meal not found") from exc

        write_started = time.perf_counter()
        result = await self._write(command, catalog_meal)
        write_ms = (time.perf_counter() - write_started) * 1000
        if not getattr(result.meal, "_is_replay", False):
            await publish_meal_event(
                self.event_publisher,
                result.meal,
                event_type="created",
                environment=self.environment,
                meal_date=command.meal_date,
                user_id=command.user_id,
                language=command.language or "en",
                event_bus=self.event_bus,
                source="catalog_meal_log",
            )
        try:
            await persist_meal_translation(
                self.meal_translation_service, result.meal, command.language
            )
        except Exception as exc:
            logger.warning("Failed to persist catalog meal translation: %s", exc)
        if self.recalculator is not None:
            try:
                await self.recalculator.recalculate(
                    user_id=command.user_id,
                    meal_date=command.meal_date,
                    logged_catalog_meal_id=command.catalog_meal_id,
                    logged_slot_id=result.slot_id,
                    request_id=command.request_id,
                )
            except Exception as exc:
                logger.warning("Failed to recalculate recommendations: %s", exc)
        logger.info(
            "catalog_log.timing meal_id=%s write_ms=%.0f",
            result.meal_id,
            write_ms,
        )
        return result

    async def _write(self, command, catalog_meal) -> LogCatalogMealResult:
        async with self.uow_factory() as uow:
            reservation = await uow.meal_write_operations.reserve(
                user_id=command.user_id,
                operation="catalog_meal_log",
                idempotency_key=command.request_id,
                request_fingerprint=catalog_log_fingerprint(
                    command.catalog_meal_id,
                    command.meal_date,
                    command.meal_type,
                ),
            )
            if reservation.state == "replay":
                return _result_from_replay(reservation.response, catalog_meal)
            if reservation.state == "fingerprint_conflict":
                raise ConflictException(
                    "Idempotency-Key was already used for a different request",
                    error_code="IDEMPOTENCY_KEY_REUSED",
                )
            if reservation.state == "in_progress":
                raise ConflictException(
                    "The same meal write is already in progress",
                    error_code="IDEMPOTENCY_IN_PROGRESS",
                )
            try:
                result = await self.log_service.execute(uow, command, catalog_meal)
                await uow.meal_write_operations.complete(
                    reservation,
                    target_meal_id=result.meal_id,
                    response=result.to_replay_payload(),
                )
                return result
            except Exception:
                await uow.meal_write_operations.release(reservation)
                raise


_REPLAY_REQUIRED_KEYS = (
    "meal_id",
    "catalog_meal_id",
    "logged_via",
    "meal_date",
    "meal_type",
)


def _invalid_replay() -> ConflictException:
    return ConflictException(
        "Catalog log replay is missing a stored meal",
        error_code="IDEMPOTENCY_REPLAY_INVALID",
    )


def _result_from_replay(payload: dict | None, catalog_meal) -> LogCatalogMealResult:
    if not isinstance(payload, dict) or any(
        not payload.get(key) for key in _REPLAY_REQUIRED_KEYS
    ):
        raise _invalid_replay()
    try:
        meal_date = date.fromisoformat(str(payload["meal_date"]))
    except (TypeError, ValueError) as exc:
        raise _invalid_replay() from exc
    meal = type(
        "ReplayMeal",
        (),
        {
            "meal_id": payload["meal_id"],
            "dish_name": getattr(catalog_meal, "name", None),
            "nutrition": None,
            "_is_replay": True,
        },
    )()
    return LogCatalogMealResult(
        meal_id=str(payload["meal_id"]),
        catalog_meal_id=str(payload["catalog_meal_id"]),
        logged_via=str(payload["logged_via"]),
        plan_id=payload.get("plan_id"),
        slot_id=payload.get("slot_id"),
        meal_date=meal_date,
        meal_type=str(payload["meal_type"]),
        meal=meal,  # type: ignore[arg-type]
    )
