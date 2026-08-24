"""Runtime-bound dependencies for meal analysis graph nodes."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from uuid import uuid4

from src.app.commands.meal.scan_by_url_command import ScanByUrlCommand
from src.app.commands.meal.upload_meal_image_immediately_command import (
    UploadMealImageImmediatelyCommand,
)
from src.app.services.food_reference_validation_service import (
    FoodReferenceValidationService,
)
from src.app.services.meal_value_insight_scheduler import (
    MealInsightEventBus,
    MealInsightTaskScheduler,
    schedule_value_insight_generation,
)
from src.domain.model.meal import Meal
from src.domain.model.meal.meal_response_localization import MealResponseLocalization
from src.domain.ports.cache_port import CachePort
from src.domain.ports.image_store_port import ImageStorePort
from src.domain.ports.integration_event_publisher_port import (
    IntegrationEventPublisherPort,
)
from src.domain.ports.meal_insight_ai_port import MealInsightAIPort
from src.domain.ports.vision_ai_service_port import VisionAIServicePort
from src.domain.services.meal_analysis.meal_translation_service import (
    MealTranslationService,
)
from src.domain.utils.image_compression import compress_image as default_compress_image


@dataclass
class AcquiredImage:
    """Image payload kept out of graph state."""

    image_id: str
    image_url: str
    persisted_image_id: str
    persisted_image_url: str
    source_bytes: bytes
    analysis_bytes: bytes
    content_type: str
    content_kind: str


@dataclass
class MealAnalyzeRuntime:
    """Per-invocation dependencies and sensitive payloads for graph execution."""

    command: UploadMealImageImmediatelyCommand | ScanByUrlCommand
    image_store: ImageStorePort | None = None
    download_image_bytes: Callable[[str], Awaitable[bytes]] | None = None
    compress_image: Callable[[bytes], bytes] = default_compress_image
    image_id_factory: Callable[[], str] = field(
        default_factory=lambda: lambda: str(uuid4())
    )
    meal_id_factory: Callable[[], str] = field(
        default_factory=lambda: lambda: str(uuid4())
    )
    vision_service: VisionAIServicePort | None = None
    gpt_parser: Any | None = None
    uow: Any | None = None
    event_publisher: IntegrationEventPublisherPort | None = None
    environment: str = "development"
    meal_value_insight_task_manager: MealInsightTaskScheduler | None = None
    meal_value_insight_cache: CachePort | None = None
    meal_value_insight_ai_manager: MealInsightAIPort | None = None
    event_bus: MealInsightEventBus | None = None
    meal_value_insight_scheduler: Callable[..., bool] = (
        schedule_value_insight_generation
    )
    meal_translation_service: MealTranslationService | None = None
    text_translation_service: Any | None = None
    food_reference_validation_service: FoodReferenceValidationService | None = None
    fatsecret_validation_enabled: bool = False
    max_vision_attempts: int = 1
    acquired_image: AcquiredImage | None = None
    vision_result: dict[str, Any] | None = None
    localization: MealResponseLocalization | None = None
    nutrition: Any | None = None
    label_metadata: dict[str, Any] | None = None
    meal_date: date | None = None
    saved_meal: Meal | None = None

    def has_analysis_dependencies(self) -> bool:
        """Return whether the graph can run beyond image acquisition."""
        return all([self.vision_service, self.gpt_parser, self.uow])
