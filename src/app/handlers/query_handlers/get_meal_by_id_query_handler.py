"""
GetMealByIdQueryHandler - Individual handler file.
Auto-extracted for better maintainability.
"""

import logging
from uuid import UUID, uuid4

from src.api.exceptions import AuthorizationException, ResourceNotFoundException
from src.app.events.base import EventHandler, handles
from src.app.queries.meal import GetMealByIdQuery
from src.domain.model.hydration import HydrationEntry
from src.domain.model.meal import Meal, MealImage, MealStatus
from src.domain.model.meal_projection import MealProjection
from src.domain.model.nutrition.macros import Macros
from src.domain.model.nutrition.nutrition import Nutrition
from src.infra.database.uow_async import AsyncUnitOfWork

logger = logging.getLogger(__name__)


@handles(GetMealByIdQuery)
class GetMealByIdQueryHandler(EventHandler[GetMealByIdQuery, Meal]):
    """Handler for retrieving a meal by ID."""

    def __init__(self):
        pass

    def _hydration_entry_to_meal(self, entry: HydrationEntry) -> Meal:
        UUID(entry.id)
        return Meal(
            meal_id=entry.id,
            user_id=entry.user_id,
            status=MealStatus.READY,
            created_at=entry.logged_at,
            ready_at=entry.logged_at,
            image=MealImage(
                image_id=str(uuid4()),
                format="jpeg",
                size_bytes=1,
                url=entry.image_url,
            ),
            dish_name=entry.drink_name_snapshot,
            meal_type="hydration",
            source=entry.source,
            quantity=entry.credited_ml,
            nutrition=Nutrition(
                macros=Macros(
                    protein=entry.protein_g,
                    carbs=entry.carbs_g,
                    fat=entry.fat_g,
                    fiber=entry.fiber_g,
                    sugar=entry.sugar_g,
                ),
                food_items=None,
            ),
        )

    async def handle(self, query: GetMealByIdQuery) -> Meal:
        """Get meal by ID."""
        # Use fresh AsyncUnitOfWork to get current data
        async with AsyncUnitOfWork() as uow:
            meal = await uow.meals.find_by_id(
                query.meal_id, projection=MealProjection.FULL_WITH_TRANSLATIONS
            )

            if not meal:
                if query.user_id:
                    hydration_entry = (
                        await uow.hydration_entries.find_by_id_or_legacy_meal_id(
                            query.user_id,
                            query.meal_id,
                        )
                    )
                    if hydration_entry is not None:
                        try:
                            return self._hydration_entry_to_meal(hydration_entry)
                        except ValueError:
                            logger.debug(
                                "Hydration entry id cannot be represented as a meal id: %s",
                                hydration_entry.id,
                            )
                raise ResourceNotFoundException(
                    f"Meal with ID {query.meal_id} not found"
                )

            # Check ownership if user_id provided
            if query.user_id and meal.user_id != query.user_id:
                raise AuthorizationException(
                    "You do not have permission to access this meal"
                )

            return meal
