"""
GetFoodDetailsQueryHandler - Individual handler file.
Auto-extracted for better maintainability.
"""

from typing import Any

from src.api.exceptions import ResourceNotFoundException
from src.app.events.base import EventHandler, handles
from src.app.queries.food.get_food_details_query import GetFoodDetailsQuery
from src.domain.exceptions.food_exceptions import FoodNotFoundError
from src.domain.services.food_mapping_service import FoodMappingService


@handles(GetFoodDetailsQuery)
class GetFoodDetailsQueryHandler(EventHandler[GetFoodDetailsQuery, dict[str, Any]]):
    """Handler for getting food details by FDC ID."""

    def __init__(
        self, food_data_service, cache_service, mapping_service: FoodMappingService
    ):
        self.food_data_service = food_data_service
        self.cache_service = cache_service
        self.mapping_service = mapping_service

    async def handle(self, event: GetFoodDetailsQuery) -> dict[str, Any]:
        cached = await self.cache_service.get_cached_food(event.fdc_id)
        if cached is not None:
            return self.mapping_service.map_food_details(cached)

        try:
            raw = await self.food_data_service.get_food_details(event.fdc_id)
        except FoodNotFoundError as exc:
            raise ResourceNotFoundException(
                message="Food not found",
                error_code="FOOD_NOT_FOUND",
            ) from exc
        await self.cache_service.cache_food(event.fdc_id, raw)
        return self.mapping_service.map_food_details(raw)
