"""Sync-compatible workflow seam for meal image analysis."""

from collections.abc import Awaitable, Callable

from src.app.commands.meal.scan_by_url_command import ScanByUrlCommand
from src.app.commands.meal.upload_meal_image_immediately_command import (
    UploadMealImageImmediatelyCommand,
)
from src.app.graphs.meal_analyze.graph import (
    run_meal_analyze_graph,
    run_meal_analyze_graph_async,
)
from src.app.graphs.meal_analyze.runtime import MealAnalyzeRuntime
from src.app.services.food_reference_validation_service import (
    FoodReferenceValidationService,
)
from src.domain.model.meal import Meal

UploadLegacyHandler = Callable[[UploadMealImageImmediatelyCommand], Awaitable[Meal]]
ScanByUrlLegacyHandler = Callable[[ScanByUrlCommand], Awaitable[Meal]]


class MealAnalyzeWorkflow:
    """Run the meal analysis graph while preserving the current sync contract."""

    def __init__(
        self,
        *,
        food_reference_validation_service: FoodReferenceValidationService | None = None,
        fatsecret_validation_enabled: bool = False,
        graph_version: str = "v1",
    ):
        self._food_reference_validation_service = food_reference_validation_service
        self._fatsecret_validation_enabled = fatsecret_validation_enabled
        self._graph_version = graph_version

    async def run_uploaded(
        self,
        command: UploadMealImageImmediatelyCommand,
        legacy_handler: UploadLegacyHandler,
        *,
        runtime: MealAnalyzeRuntime | None = None,
    ) -> Meal:
        """Run graph flow for direct uploads while preserving the sync contract."""
        state = {
            "scan_mode": "meal_scan",
            "user_id": command.user_id,
            "target_date": command.target_date.isoformat()
            if command.target_date
            else None,
            "graph_version": self._graph_version,
        }
        if runtime is not None:
            self._configure_runtime(runtime)
            result = await run_meal_analyze_graph_async(state, runtime)
            return result["result"]

        run_meal_analyze_graph(state)
        meal = await legacy_handler(command)
        return await self._validate_if_enabled(meal)

    async def run_scan_by_url(
        self,
        command: ScanByUrlCommand,
        legacy_handler: ScanByUrlLegacyHandler,
        *,
        runtime: MealAnalyzeRuntime | None = None,
    ) -> Meal:
        """Run graph flow for URL scans while preserving the sync contract."""
        scan_mode = "food_label" if command.scan_mode == "food_label" else "meal_scan"
        state = {
            "scan_mode": scan_mode,
            "image_id": command.public_id.split("/")[-1],
            "user_id": command.user_id,
            "target_date": command.target_date.isoformat()
            if command.target_date
            else None,
            "graph_version": self._graph_version,
        }
        if runtime is not None:
            self._configure_runtime(runtime)
            result = await run_meal_analyze_graph_async(state, runtime)
            return result["result"]

        run_meal_analyze_graph(state)
        meal = await legacy_handler(command)
        if command.scan_mode == "food_label":
            return meal
        return await self._validate_if_enabled(meal)

    async def _validate_if_enabled(self, meal: Meal) -> Meal:
        if (
            not self._fatsecret_validation_enabled
            or self._food_reference_validation_service is None
        ):
            return meal
        report = await self._food_reference_validation_service.validate_meal(meal)
        return report.meal

    def _configure_runtime(self, runtime: MealAnalyzeRuntime) -> None:
        runtime.food_reference_validation_service = (
            self._food_reference_validation_service
        )
        runtime.fatsecret_validation_enabled = self._fatsecret_validation_enabled
