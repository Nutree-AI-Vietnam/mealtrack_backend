"""Handler for recommendation slot swaps."""

from typing import Any

from src.app.commands.meal_recommendation import SwapMealRecommendationSlotCommand
from src.app.events.base import EventHandler, handles
from src.domain.model.meal_recommendation import (
    MealRecommendationAlternative,
    PersistedMealRecommendationSlotMutationResult,
)
from src.domain.services.meal_recommendation.three_day_plan_optimizer import (
    ThreeDayPlanOptimizer,
)


@handles(SwapMealRecommendationSlotCommand)
class SwapMealRecommendationSlotCommandHandler(
    EventHandler[
        SwapMealRecommendationSlotCommand,
        PersistedMealRecommendationSlotMutationResult,
    ]
):
    def __init__(
        self,
        uow=None,
        uow_factory: Any = None,
        optimizer: ThreeDayPlanOptimizer | None = None,
        catalog_snapshot_service=None,
        history_projector=None,
    ):
        self.uow_factory: Any = uow_factory or (lambda: uow)
        self.optimizer = optimizer or ThreeDayPlanOptimizer()
        self.catalog_snapshot_service = catalog_snapshot_service
        self.history_projector = history_projector

    async def handle(
        self, command: SwapMealRecommendationSlotCommand
    ) -> PersistedMealRecommendationSlotMutationResult:
        async with self.uow_factory() as uow:
            replenishment: tuple[MealRecommendationAlternative, ...] = ()
            context_loader = getattr(
                uow.meal_recommendation_plans,
                "get_slot_replenishment_context",
                None,
            )
            context = (
                await context_loader(
                    user_id=command.user_id,
                    plan_id=command.plan_id,
                    slot_id=command.slot_id,
                )
                if context_loader
                else None
            )
            if (
                context
                and not any(candidate.seen_at is None for candidate in context[0].alternatives)
                and self.catalog_snapshot_service
                and self.history_projector
            ):
                slot, seen_ids, other_selected_ids, timezone = context
                snapshot = await self.catalog_snapshot_service.get_snapshot(uow)
                affinity = await self.history_projector.build_affinity(
                    uow,
                    user_id=command.user_id,
                    start_date=slot.slot_date,
                    timezone=timezone,
                )
                excluded_ids = set(seen_ids) | set(other_selected_ids)
                excluded_ids.add(slot.catalog_meal_id)
                result = self.optimizer.select_slot_replenishment(
                    list(snapshot.meals),
                    meal_type=slot.meal_type,
                    target_calories=slot.target_calories,
                    excluded_catalog_meal_ids=excluded_ids,
                    affinity=affinity,
                    ingredient_statistics=snapshot.ingredient_statistics,
                )
                if not hasattr(result, "message"):
                    replenishment = result
            return await uow.meal_recommendation_plans.swap_slot(
                user_id=command.user_id,
                plan_id=command.plan_id,
                slot_id=command.slot_id,
                request_id=command.request_id,
                expected_version=command.expected_selection_version,
                alternative_catalog_meal_id=command.alternative_catalog_meal_id,
                reason=command.reason,
                replenishment_alternatives=replenishment,
            )
