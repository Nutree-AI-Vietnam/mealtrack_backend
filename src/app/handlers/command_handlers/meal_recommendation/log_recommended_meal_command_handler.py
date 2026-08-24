"""Handler for logging recommended meals through normal meal persistence."""

import logging
from collections.abc import Coroutine
from typing import Any

from src.app.commands.meal_recommendation import LogRecommendedMealCommand
from src.app.events.base import EventHandler, handles
from src.app.events.meal.meal_events import MealCreatedEvent
from src.app.services.background_job_scheduler import schedule_background_job
from src.app.services.meal_translation_persistence import persist_meal_translation
from src.app.services.recommended_meal_materialization_service import (
    RecommendedMealMaterializationService,
)
from src.domain.model.meal import Meal
from src.domain.model.meal_recommendation import (
    PersistedMealRecommendationSlotMutationResult,
)
from src.domain.ports.integration_event_publisher_port import (
    IntegrationEventPublisherPort,
)
from src.domain.services.meal_analysis.meal_translation_service import (
    MealTranslationService,
)

logger = logging.getLogger(__name__)


@handles(LogRecommendedMealCommand)
class LogRecommendedMealCommandHandler(
    EventHandler[
        LogRecommendedMealCommand,
        PersistedMealRecommendationSlotMutationResult,
    ]
):
    def __init__(
        self,
        uow,
        materializer: RecommendedMealMaterializationService | None = None,
        meal_translation_service: MealTranslationService | None = None,
        event_publisher: IntegrationEventPublisherPort | None = None,
        environment: str = "development",
        task_manager=None,
    ):
        self.uow = uow
        self.materializer = materializer or RecommendedMealMaterializationService()
        self.meal_translation_service = meal_translation_service
        self.event_publisher = event_publisher
        self.environment = environment
        self.task_manager = task_manager

    async def handle(
        self, command: LogRecommendedMealCommand
    ) -> PersistedMealRecommendationSlotMutationResult:
        saved_meal: Meal | None = None
        meal_date = None

        async with self.uow as uow:
            plan, slot, replayed = await uow.meal_recommendation_plans.claim_slot_log(
                user_id=command.user_id,
                plan_id=command.plan_id,
                slot_id=command.slot_id,
                request_id=command.request_id,
            )
            if replayed:
                result = PersistedMealRecommendationSlotMutationResult(
                    plan_id=plan.id,
                    user_id=plan.user_id,
                    slot=slot,
                )
            else:
                meal = await self.materializer.materialize(uow, plan=plan, slot=slot)
                result = await uow.meal_recommendation_plans.finalize_slot_logged(
                    user_id=command.user_id,
                    plan_id=command.plan_id,
                    slot_id=command.slot_id,
                    request_id=command.request_id,
                    meal_id=meal.meal_id,
                )
                saved_meal = meal
                meal_date = slot.slot_date

        if (
            saved_meal is not None
            and self.event_publisher is not None
            and meal_date is not None
        ):
            try:
                event = MealCreatedEvent(
                    environment=self.environment,
                    aggregate_id=saved_meal.meal_id,
                    data={
                        "user_id": command.user_id,
                        "meal_id": saved_meal.meal_id,
                        "meal_date": meal_date.isoformat(),
                    },
                )
                await self.event_publisher.publish(event.to_payload())
            except Exception as exc:
                logger.error("Failed to publish meal created event: %s", exc)

        # meal_translation uses its own DB session; parent meal must be committed first.
        if saved_meal is not None:
            await self._defer(
                f"recommendation-log-translation:{saved_meal.meal_id}",
                persist_meal_translation(
                    self.meal_translation_service, saved_meal, command.language
                ),
            )

        return result

    async def _defer(self, name: str, coro: Coroutine[Any, Any, Any]) -> None:
        schedule_background_job(self.task_manager, name, coro, logger=logger)
