"""
Command handler for creating manual meals from selected foods with nutrition data.
All items must provide their own nutrition (via custom_nutrition).
"""

import logging
import time
from typing import Any
from uuid import uuid4

from sqlalchemy.exc import IntegrityError

from src.api.exceptions import ConflictException, ValidationException
from src.app.commands.meal.create_manual_meal_command import CreateManualMealCommand
from src.app.events.base import EventHandler
from src.app.events.meal.meal_events import publish_meal_event
from src.app.handlers.command_handlers.client_resource_id import (
    assert_unique_command_item_ids,
    resolve_client_meal_id,
)
from src.app.services.manual_meal_nutrition_resolver import (
    ManualMealNutritionResolver,
)
from src.domain.model.meal import Meal, MealImage, MealStatus
from src.domain.ports.async_unit_of_work_port import AsyncUnitOfWorkPort
from src.domain.ports.integration_event_publisher_port import (
    IntegrationEventPublisherPort,
)
from src.domain.ports.meal_repository_port import MealRepositoryPort
from src.domain.ports.provider_budget_port import ProviderBudgetPort
from src.domain.services.nutrition_calculation_service import (
    NutritionCalculationService,
)
from src.domain.utils.timezone_utils import (
    noon_utc_for_date,
    resolve_user_timezone_async,
    utc_now,
)
from src.observability import distribution_metric

logger = logging.getLogger(__name__)


class CreateManualMealCommandHandler(EventHandler[CreateManualMealCommand, Any]):
    def __init__(
        self,
        uow: AsyncUnitOfWorkPort,
        event_publisher: IntegrationEventPublisherPort | None = None,
        event_bus: Any | None = None,
        meal_repository: MealRepositoryPort | None = None,
        nutrition_service: NutritionCalculationService | None = None,
        nutrition_resolver: ManualMealNutritionResolver | None = None,
        provider=None,
        provider_budget: ProviderBudgetPort | None = None,
        provider_rpm: int | None = None,
        uow_factory=None,
        environment: str = "development",
    ):
        self.uow = uow
        self.event_publisher = event_publisher
        self.event_bus = event_bus
        self.environment = environment
        self.meal_repository = meal_repository
        self.uow_factory = uow_factory
        self.nutrition_service = nutrition_service or NutritionCalculationService()
        self.nutrition_resolver = nutrition_resolver or ManualMealNutritionResolver(
            provider=provider,
            provider_budget=provider_budget,
            provider_rpm=provider_rpm,
            uow_factory=uow_factory,
        )

    async def handle(self, event: CreateManualMealCommand):
        if event.nutrition_contract_version == 2 and self.uow_factory is not None:
            return await self._handle_v2(event)
        # Use provided meal_repository for legacy callers while preserving the
        # same committed-write publication contract as the UoW path.
        if self.meal_repository:
            saved_meal = await self._process_meal(event, self.meal_repository, uow=None)
            meal_date = (saved_meal.created_at or utc_now()).date()
            await publish_meal_event(
                self.event_publisher,
                saved_meal,
                event_type="created",
                environment=self.environment,
                meal_date=meal_date,
                language=event.language,
                event_bus=self.event_bus,
                source="manual_meal_legacy_repository",
            )
            return saved_meal
        else:
            _t_start = time.perf_counter()

            _t_db_start = time.perf_counter()
            async with self.uow as uow:
                reservation = await self._reserve_v2_write(event, uow)
                cache_event_needed = False
                if reservation and reservation.state == "replay":
                    saved_meal = await uow.meals.find_by_id(reservation.target_meal_id)
                    if saved_meal is None:
                        raise ValidationException(
                            "Idempotent meal result is no longer available",
                            error_code="IDEMPOTENCY_RESULT_UNAVAILABLE",
                        )
                    meal_date = (saved_meal.created_at or utc_now()).date()
                else:
                    try:
                        saved_meal, meal_date = await self._process_meal(
                            event, uow.meals, uow=uow
                        )
                        cache_event_needed = True
                        if reservation:
                            await uow.meal_write_operations.complete(
                                reservation,
                                target_meal_id=saved_meal.meal_id,
                                response={"meal_id": saved_meal.meal_id},
                            )
                    except Exception:
                        if reservation:
                            await uow.meal_write_operations.release(reservation)
                        raise
            if cache_event_needed:
                await publish_meal_event(
                    self.event_publisher,
                    saved_meal,
                    event_type="created",
                    environment=self.environment,
                    meal_date=meal_date,
                    language=event.language,
                    event_bus=self.event_bus,
                    source="manual_meal_v2",
                )
            _db_ms = (time.perf_counter() - _t_db_start) * 1000

            # Queue consumer work remains asynchronous after publication succeeds.
            _t_cache_start = time.perf_counter()
            _cache_ms = (time.perf_counter() - _t_cache_start) * 1000

            _total_ms = (time.perf_counter() - _t_start) * 1000
            distribution_metric(
                "meal.manual_save.db_ms",
                _db_ms,
                unit="millisecond",
                attributes={"component": "manual_meal"},
            )
            distribution_metric(
                "meal.manual_save.cache_ms",
                _cache_ms,
                unit="millisecond",
                attributes={"component": "manual_meal"},
            )
            logger.info(
                "manual_save handler timing: user=%s db_ms=%.1f cache_ms=%.1f total_ms=%.1f",
                event.user_id,
                _db_ms,
                _cache_ms,
                _total_ms,
            )
            return saved_meal

    async def _handle_v2(self, event: CreateManualMealCommand):
        """Reserve briefly, then persist prepared nutrition atomically.

        Parse/scan flows already produce portion nutrition for custom items.
        Re-resolving those items here interprets their per-portion values as
        per-100g density and can reject an otherwise valid meal. Older clients
        that send only source identities still use the resolver compatibility
        path until they adopt the prepared-nutrition payload.
        """
        reservation = await self._reserve_v2_write_short(event)
        if reservation and reservation.state == "replay":
            async with self.uow_factory() as uow:
                saved_meal = await uow.meals.find_by_id(reservation.target_meal_id)
            if saved_meal is None:
                raise ValidationException(
                    "Idempotent meal result is no longer available",
                    error_code="IDEMPOTENCY_RESULT_UNAVAILABLE",
                )
            return saved_meal

        try:
            items_needing_resolution = [
                item for item in event.items if not self._is_prepared_nutrition(item)
            ]
            if items_needing_resolution:
                async with self.uow_factory() as resolve_uow:
                    resolved_source_items = await self.nutrition_resolver.resolve_items(
                        items_needing_resolution,
                        resolve_uow.food_references,
                        contract_version=2,
                    )
                resolved_items = list(event.items)
                source_indexes = (
                    index
                    for index, item in enumerate(event.items)
                    if not self._is_prepared_nutrition(item)
                )
                for index, resolved_item in zip(
                    source_indexes, resolved_source_items, strict=True
                ):
                    resolved_items[index] = resolved_item
            else:
                resolved_items = list(event.items)

            resolved_items = [
                ManualMealNutritionResolver.ensure_source_snapshot(item)
                for item in resolved_items
            ]

            async with self.uow as uow:
                if items_needing_resolution:
                    await self.nutrition_resolver.revalidate_local_items(
                        resolved_source_items, uow.food_references
                    )
                saved_meal, meal_date = await self._process_meal(
                    event,
                    uow.meals,
                    uow=uow,
                    resolved_items=resolved_items,
                    revalidate_local=False,
                )
                await uow.meal_write_operations.complete(
                    reservation,
                    target_meal_id=saved_meal.meal_id,
                    response={"meal_id": saved_meal.meal_id},
                )
            await publish_meal_event(
                self.event_publisher,
                saved_meal,
                event_type="created",
                environment=self.environment,
                meal_date=meal_date,
                language=event.language,
                event_bus=self.event_bus,
                source="manual_meal",
            )

            return saved_meal
        except ValueError as exc:
            await self._release_v2_write(reservation)
            logger.warning("Validation error creating manual meal: %s", str(exc))
            raise ValidationException(str(exc)) from None
        except Exception:
            await self._release_v2_write(reservation)
            raise

    @staticmethod
    def _is_prepared_nutrition(item: Any) -> bool:
        """Recognize only the versioned nutrition payload as save-ready."""
        return (
            str(item.nutrition_contract_version or "") == "2"
            and item.custom_nutrition is not None
            and (item.origin == "custom" or item.source_snapshot is not None)
        )

    async def _reserve_v2_write_short(self, event):
        async with self.uow_factory() as uow:
            return await self._reserve_v2_write(event, uow)

    async def _release_v2_write(self, reservation):
        async with self.uow_factory() as uow:
            await uow.meal_write_operations.release(reservation)

    async def _reserve_v2_write(self, event, uow):
        if event.nutrition_contract_version != 2:
            return None
        if not event.idempotency_key or not event.request_fingerprint:
            raise ValidationException(
                "v2 manual saves require idempotency metadata",
                error_code="IDEMPOTENCY_KEY_REQUIRED",
            )
        reservation = await uow.meal_write_operations.reserve(
            user_id=event.user_id,
            operation="create_manual_meal",
            idempotency_key=event.idempotency_key,
            request_fingerprint=event.request_fingerprint,
        )
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
        return reservation

    async def _process_meal(
        self,
        event: CreateManualMealCommand,
        meal_repo,
        uow=None,
        resolved_items=None,
        revalidate_local=True,
    ):
        items = resolved_items or event.items
        assert_unique_command_item_ids(items)
        if event.nutrition_contract_version == 2 and resolved_items is None:
            if uow is None or not getattr(uow, "food_references", None):
                raise ValueError(
                    "v2 manual saves require a database reference resolver"
                )
            items = await self.nutrition_resolver.resolve_items(
                items,
                uow.food_references,
                contract_version=event.nutrition_contract_version,
            )
            if revalidate_local:
                await self.nutrition_resolver.revalidate_local_items(
                    items, uow.food_references
                )
        nutrition, _ = self.nutrition_service.aggregate_from_command_items(items)

        # Determine the meal date and datetime
        now = utc_now()
        meal_date = event.target_date if event.target_date else now.date()
        if event.target_date and event.target_date != now.date():
            # Past/future date: use noon in user's local timezone to avoid
            # created_at falling into the wrong date after UTC conversion
            if uow is not None:
                user_tz = await resolve_user_timezone_async(event.user_id, uow)
            else:
                async with self.uow as _uow:
                    user_tz = await resolve_user_timezone_async(event.user_id, _uow)
            meal_datetime = noon_utc_for_date(meal_date, user_tz)
        else:
            # Today or no date — use actual current time
            meal_datetime = now

        # Determine source: use explicit source if provided, otherwise infer
        source = event.source
        if not source:
            has_custom = any(item.custom_nutrition is not None for item in items)
            if has_custom:
                source = "food_search"
            else:
                source = "manual"

        meal_id = await resolve_client_meal_id(
            requested_meal_id=event.meal_id,
            user_id=event.user_id,
            meal_repo=meal_repo,
        )

        meal = Meal(
            meal_id=meal_id,
            user_id=event.user_id,
            status=MealStatus.READY,
            created_at=meal_datetime,
            image=MealImage(
                image_id=str(uuid4()),
                format="jpeg",
                size_bytes=1,
                url=None,
            ),
            dish_name=event.dish_name,
            emoji=event.emoji,
            nutrition=nutrition,
            ready_at=meal_datetime,
            meal_type=event.meal_type,
            source=source,
        )

        try:
            saved_meal = await meal_repo.insert(meal)
        except IntegrityError as exc:
            raise ConflictException(
                "A client-supplied id is already in use",
                error_code="CLIENT_RESOURCE_ID_CONFLICT",
            ) from exc
        if uow is None:
            return saved_meal
        return saved_meal, meal_date
