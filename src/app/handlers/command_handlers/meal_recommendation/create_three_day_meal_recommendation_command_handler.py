"""Handler for durable three-day meal recommendation creation."""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from src.app.commands.meal_recommendation import (
    CreateThreeDayMealRecommendationCommand,
)
from src.app.events.base import EventHandler, handles
from src.app.services.meal_recommendation_history_projector import (
    MealRecommendationHistoryProjector,
)
from src.domain.exceptions.meal_recommendation_exceptions import (
    MealRecommendationCatalogUnavailableError,
    MealRecommendationIdempotencyConflictError,
    MealRecommendationInsufficientCatalogError,
    MealRecommendationPersistenceConflictError,
)
from src.domain.model.meal_recommendation import (
    MealRecommendationInsufficiency,
    PersistedMealRecommendationCandidate,
    PersistedMealRecommendationPlan,
    PersistedMealRecommendationSlot,
)
from src.domain.services.meal_recommendation.three_day_plan_optimizer import (
    ThreeDayPlanOptimizer,
)

logger = logging.getLogger(__name__)


@handles(CreateThreeDayMealRecommendationCommand)
class CreateThreeDayMealRecommendationCommandHandler(
    EventHandler[
        CreateThreeDayMealRecommendationCommand,
        PersistedMealRecommendationPlan,
    ]
):
    """Create an active durable plan from the active catalog release."""

    def __init__(
        self,
        uow=None,
        uow_factory: Any = None,
        optimizer: ThreeDayPlanOptimizer | None = None,
        history_projector: MealRecommendationHistoryProjector | None = None,
        catalog_snapshot_service=None,
    ):
        self.uow_factory: Any = uow_factory or (lambda: uow)
        self.optimizer = optimizer or ThreeDayPlanOptimizer()
        self.history_projector = history_projector or MealRecommendationHistoryProjector()
        self.catalog_snapshot_service = catalog_snapshot_service

    async def handle(
        self,
        command: CreateThreeDayMealRecommendationCommand,
    ) -> PersistedMealRecommendationPlan:
        fingerprint = _request_fingerprint(command)
        async with self.uow_factory() as uow:
            await uow.meal_recommendation_plans.lock_generation_for_user(
                user_id=command.user_id
            )
            existing = await uow.meal_recommendation_plans.get_by_idempotency_key(
                user_id=command.user_id,
                operation=command.operation,
                idempotency_key=command.idempotency_key,
            )
            if existing is not None:
                if existing.request_fingerprint != fingerprint:
                    raise MealRecommendationIdempotencyConflictError
                return existing

            if self.catalog_snapshot_service is not None:
                snapshot = await self.catalog_snapshot_service.get_snapshot(uow)
                catalog_meals = list(snapshot.meals)
                ingredient_statistics = snapshot.ingredient_statistics
            else:
                catalog_meals = await uow.catalog_recipes.list_active_meals()
                ingredient_statistics = None
            if not catalog_meals:
                raise MealRecommendationCatalogUnavailableError

            affinity = await self.history_projector.build_affinity(
                uow,
                user_id=command.user_id,
                start_date=command.start_date,
                timezone=command.timezone,
            )
            result = self.optimizer.build_plan(
                catalog_meals,
                user_id=command.user_id,
                daily_calories=command.daily_calories,
                affinity=affinity,
                ingredient_statistics=ingredient_statistics,
            )
            if isinstance(result, MealRecommendationInsufficiency):
                logger.warning(
                    "meal_recommendation_insufficient_catalog "
                    "reason=%s required=%s available=%s message=%s",
                    result.reason,
                    result.required,
                    result.available,
                    result.message,
                )
                raise MealRecommendationInsufficientCatalogError(result.message)

            plan = _to_persisted_plan(command, fingerprint, result)
            try:
                return await uow.meal_recommendation_plans.save_new_active_plan(plan)
            except MealRecommendationPersistenceConflictError:
                replay = await uow.meal_recommendation_plans.get_by_idempotency_key(
                    user_id=command.user_id,
                    operation=command.operation,
                    idempotency_key=command.idempotency_key,
                )
                if replay is None:
                    raise
                if replay.request_fingerprint != fingerprint:
                    raise MealRecommendationIdempotencyConflictError from None
                return replay


def _to_persisted_plan(
    command: CreateThreeDayMealRecommendationCommand,
    fingerprint: str,
    plan,
) -> PersistedMealRecommendationPlan:
    slots: list[PersistedMealRecommendationSlot] = []
    batch_id = str(uuid.uuid4())
    for position, slot in enumerate(plan.slots):
        slot_id = str(uuid.uuid4())
        selected_id = batch_id if position == 0 else str(uuid.uuid4())
        selected = PersistedMealRecommendationCandidate(
            id=selected_id,
            slot_id=slot_id,
            recommendation_date=command.start_date + timedelta(days=slot.day_index),
            meal_type=slot.meal_type,
            catalog_meal_id=slot.catalog_meal.id,
            candidate_rank=0,
            is_selected=True,
            score=_decimal_score(slot.score),
            selection_version=1,
            seen_at=datetime.now(UTC),
            catalog_meal=slot.catalog_meal,
        )
        alternatives = tuple(
            PersistedMealRecommendationCandidate(
                id=str(uuid.uuid4()),
                slot_id=slot_id,
                recommendation_date=command.start_date + timedelta(days=slot.day_index),
                meal_type=slot.meal_type,
                catalog_meal_id=alternative.catalog_meal.id,
                candidate_rank=alternative_position + 1,
                is_selected=False,
                score=_decimal_score(alternative.score),
                selection_version=1,
                seen_at=None,
                catalog_meal=alternative.catalog_meal,
            )
            for alternative_position, alternative in enumerate(
                plan.alternatives[(slot.day_index, slot.meal_type)]
            )
        )
        slots.append(
            PersistedMealRecommendationSlot(
                id=slot_id,
                slot_date=command.start_date + timedelta(days=slot.day_index),
                day_index=slot.day_index,
                meal_type=slot.meal_type,
                catalog_meal_id=slot.catalog_meal.id,
                target_calories=slot.target_calories,
                score=slot.score,
                position=position,
                selected=selected,
                alternatives=alternatives,
            )
        )
    return PersistedMealRecommendationPlan(
        id=batch_id,
        user_id=command.user_id,
        status="active",
        timezone=command.timezone,
        start_date=command.start_date,
        daily_calories=command.daily_calories,
        operation=command.operation,
        idempotency_key=command.idempotency_key,
        request_fingerprint=fingerprint,
        slots=tuple(slots),
    )


def _decimal_score(value: float):
    from decimal import Decimal

    return Decimal(str(value))


def _request_fingerprint(command: CreateThreeDayMealRecommendationCommand) -> str:
    payload = {
        "daily_calories": command.daily_calories,
        "operation": command.operation,
        "start_date": command.start_date.isoformat(),
        "timezone": command.timezone,
        "user_id": command.user_id,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
