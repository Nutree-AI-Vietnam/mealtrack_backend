"""Handler for recommendation slot skips."""

from typing import Any

from src.app.commands.meal_recommendation import SkipMealRecommendationSlotCommand
from src.app.events.base import EventHandler, handles
from src.domain.model.meal_recommendation import (
    PersistedMealRecommendationSlotMutationResult,
)


@handles(SkipMealRecommendationSlotCommand)
class SkipMealRecommendationSlotCommandHandler(
    EventHandler[
        SkipMealRecommendationSlotCommand,
        PersistedMealRecommendationSlotMutationResult,
    ]
):
    def __init__(self, uow=None, uow_factory: Any = None):
        self.uow_factory: Any = uow_factory or (lambda: uow)

    async def handle(
        self, command: SkipMealRecommendationSlotCommand
    ) -> PersistedMealRecommendationSlotMutationResult:
        async with self.uow_factory() as uow:
            return await uow.meal_recommendation_plans.skip_slot(
                user_id=command.user_id,
                plan_id=command.plan_id,
                slot_id=command.slot_id,
                request_id=command.request_id,
            )
