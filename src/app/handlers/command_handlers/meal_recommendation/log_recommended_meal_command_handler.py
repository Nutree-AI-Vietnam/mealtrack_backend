"""Handler for logging recommended meals through normal meal persistence."""

import logging
from typing import Any

from src.app.commands.meal_recommendation import LogRecommendedMealCommand
from src.app.events.base import EventHandler, handles
from src.app.events.meal.meal_events import publish_meal_event
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
        uow=None,
        uow_factory: Any = None,
        materializer: RecommendedMealMaterializationService | None = None,
        meal_translation_service: Any | None = None,
        event_publisher: IntegrationEventPublisherPort | None = None,
        event_bus: Any | None = None,
        environment: str = "development",
    ):
        self.uow_factory: Any = uow_factory or (lambda: uow)
        self.materializer = materializer or RecommendedMealMaterializationService()
        self.event_publisher = event_publisher
        self.event_bus = event_bus
        self.environment = environment

    async def handle(
        self, command: LogRecommendedMealCommand
    ) -> PersistedMealRecommendationSlotMutationResult:
        saved_meal: Meal | None = None
        meal_date = None

        async with self.uow_factory() as uow:
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

        if saved_meal is not None and meal_date is not None:
            await publish_meal_event(
                self.event_publisher,
                saved_meal,
                event_type="created",
                environment=self.environment,
                meal_date=meal_date,
                user_id=command.user_id,
                language=command.language,
                event_bus=self.event_bus,
                source="recommended_meal_log",
            )

        return result
