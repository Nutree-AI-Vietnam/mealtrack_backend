"""Handler for scan-by-url: download Cloudinary image → compress → AI bytes path."""

import asyncio
import logging
import time
from typing import Any
from uuid import uuid4

from src.api.exceptions import ValidationException
from src.app.commands.meal.scan_by_url_command import ScanByUrlCommand
from src.app.events.base import EventHandler, handles
from src.app.events.meal.meal_events import publish_meal_event
from src.app.graphs.meal_analyze.runtime import MealAnalyzeRuntime
from src.app.services.food_label_localizer import localize_food_label_display
from src.app.services.meal_analyze_workflow import MealAnalyzeWorkflow
from src.domain.constants import MealDefaults
from src.domain.model.meal import Meal, MealImage, MealStatus
from src.domain.model.meal.meal_response_localization import (
    parse_meal_response_localization,
    persist_meal_response_localization,
)
from src.domain.parsers.vision_response_parser import (
    VisionResponseParser as GPTResponseParser,
)
from src.domain.ports.async_unit_of_work_port import AsyncUnitOfWorkPort
from src.domain.ports.integration_event_publisher_port import (
    IntegrationEventPublisherPort,
)
from src.domain.ports.vision_ai_service_port import VisionAIServicePort
from src.domain.services.meal_analysis.meal_translation_service import (
    MealTranslationService,
)
from src.domain.services.meal_type_determination_service import (
    determine_meal_type_from_timestamp,
)
from src.domain.strategies.meal_analysis_strategy import (
    FoodLabelImageAnalysisStrategy,
)
from src.domain.utils.image_compression import (
    compress_image,
    to_compressed_cloudinary_url,
)
from src.domain.utils.timezone_utils import (
    get_zone_info,
    is_valid_timezone,
    noon_utc_for_date,
    utc_now,
)
from src.observability import capture_message, distribution_metric, increment_metric

logger = logging.getLogger(__name__)


@handles(ScanByUrlCommand)
class ScanByUrlCommandHandler(EventHandler[ScanByUrlCommand, Meal]):
    """Download Cloudinary image → compress → AI bytes path → persist Meal."""

    def __init__(
        self,
        uow: AsyncUnitOfWorkPort | None = None,
        event_bus: Any = None,
        vision_service: VisionAIServicePort = None,
        gpt_parser: GPTResponseParser = None,
        meal_translation_service: MealTranslationService | None = None,
        text_translation_service: Any | None = None,
        event_publisher: IntegrationEventPublisherPort | None = None,
        environment: str = "development",
        meal_analyze_workflow: MealAnalyzeWorkflow | None = None,
        meal_analyze_graph_enabled: bool = False,
        download_image_bytes: Any | None = None,
        uow_factory=None,
    ):
        self.uow = uow
        self.uow_factory = uow_factory if uow_factory is not None else (lambda: uow)
        self.event_bus = event_bus
        self.vision_service = vision_service
        self.gpt_parser = gpt_parser
        self.meal_translation_service = meal_translation_service
        self.text_translation_service = text_translation_service
        self.event_publisher = event_publisher
        self.environment = environment
        self.meal_analyze_workflow = meal_analyze_workflow
        self.meal_analyze_graph_enabled = meal_analyze_graph_enabled
        self._download_image_bytes_fn = download_image_bytes

    def _record_food_label_metric(
        self,
        name: str,
        *,
        reason: str | None = None,
        elapsed_ms: float | None = None,
    ) -> None:
        attributes = {"component": "food_label_scan"}
        if reason:
            attributes["reason"] = reason
        increment_metric(name, attributes=attributes)
        if elapsed_ms is not None:
            distribution_metric(
                "food_label.scan_latency_ms",
                elapsed_ms,
                unit="millisecond",
                attributes=attributes,
            )

    def _capture_rejected_scan(
        self,
        *,
        image_id: str,
        image_url: str,
        reason: str,
    ) -> None:
        capture_message(
            "meal_scan.image_rejected",
            level="warning",
            context={
                "component": "meal_scan",
                "operation": "scan_by_url",
                "ai_purpose": "meal_scan",
                "image_id": image_id,
                "image_url": image_url,
                "rejection_reason": reason,
            },
        )

    async def _download_image_bytes(self, image_url: str) -> bytes:
        if self._download_image_bytes_fn is not None:
            return await self._download_image_bytes_fn(image_url)
        import httpx

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(image_url)
            resp.raise_for_status()
            return resp.content

    async def _analyze_food_label_image_with_ai(
        self,
        image_bytes: bytes,
        *,
        crop_metadata: dict | None,
    ) -> dict[str, Any]:
        self._record_food_label_metric("food_label.image_ai_attempt")
        try:
            strategy = FoodLabelImageAnalysisStrategy(crop_metadata=crop_metadata)
            vision_result = await self.vision_service.analyze_with_strategy(
                image_bytes,
                strategy,
            )
            structured_data = vision_result.get("structured_data")
            if not isinstance(structured_data, dict):
                raise ValidationException(
                    message="Nutrition Facts label could not be read.",
                    error_code="NOT_FOOD_LABEL_IMAGE",
                )
            if not structured_data.get("is_food_label", True):
                raise ValidationException(
                    message="Nutrition Facts label could not be read.",
                    error_code="NOT_FOOD_LABEL_IMAGE",
                )
            self._record_food_label_metric("food_label.image_ai_success")
            return {"structured_data": structured_data}
        except Exception as exc:
            logger.warning("[FOOD-LABEL-IMAGE-AI] failed: %s", exc)
            self._record_food_label_metric(
                "food_label.image_ai_failure",
                reason=exc.__class__.__name__,
            )
            raise

    async def _handle_legacy_scan_by_url(self, command: ScanByUrlCommand) -> Meal:
        if not all([self.vision_service, self.gpt_parser]):
            raise RuntimeError("Required dependencies not configured")

        start = time.time()
        image_id = command.public_id.split("/")[-1]

        try:
            # For meal scans, fetch edge-compressed Cloudinary URL to avoid local PIL resizing
            download_url = (
                to_compressed_cloudinary_url(command.image_url)
                if command.scan_mode != "food_label"
                else command.image_url
            )
            raw_bytes = await self._download_image_bytes(download_url)
            image_bytes: bytes | None = None
            if command.scan_mode != "food_label":
                image_bytes = raw_bytes
                if len(image_bytes) > 200 * 1024:
                    image_bytes = await asyncio.to_thread(compress_image, raw_bytes)
                logger.info(
                    "[SCAN-BY-URL] image_id=%s raw=%d compressed=%d bytes",
                    image_id,
                    len(raw_bytes),
                    len(image_bytes),
                )
            else:
                logger.info(
                    "[SCAN-BY-URL-FOOD-LABEL] image_id=%s raw=%d bytes",
                    image_id,
                    len(raw_bytes),
                )

            # Determine timezone-aware datetime
            async with self.uow_factory() as uow:
                user_timezone = await uow.users.get_user_timezone(command.user_id)
            if not user_timezone or not is_valid_timezone(user_timezone):
                user_timezone = "UTC"

            now = utc_now()
            meal_date = command.target_date if command.target_date else now.date()
            if command.target_date and command.target_date != now.date():
                meal_datetime = noon_utc_for_date(meal_date, user_timezone)
            else:
                meal_datetime = now

            zone_info = get_zone_info(user_timezone)
            meal_type = determine_meal_type_from_timestamp(
                meal_datetime.astimezone(zone_info)
            )

            # Vision analysis via bytes path (never sends URL to the AI provider)
            if command.scan_mode == "food_label":
                label_image_bytes = raw_bytes
                if command.label_crop_image_url:
                    label_image_bytes = await self._download_image_bytes(
                        command.label_crop_image_url
                    )
                vision_result = await self._analyze_food_label_image_with_ai(
                    label_image_bytes,
                    crop_metadata=command.crop_metadata,
                )
            elif command.user_description:
                if image_bytes is None:
                    raise RuntimeError("Image bytes unavailable for scan-by-url")
                from src.domain.strategies.meal_analysis_strategy import (
                    AnalysisStrategyFactory,
                )

                strategy = AnalysisStrategyFactory.create_user_context_strategy(
                    command.user_description,
                    language=command.language,
                )
                vision_result = await self.vision_service.analyze_with_strategy(
                    image_bytes, strategy
                )
            else:
                if image_bytes is None:
                    raise RuntimeError("Image bytes unavailable for scan-by-url")
                vision_result = await self.vision_service.analyze(
                    image_bytes,
                    language=command.language,
                )

            vision_elapsed = time.time() - start

            localization = None
            if command.scan_mode != "food_label" and command.language != "en":
                structured_data = (
                    vision_result.get("structured_data")
                    if isinstance(vision_result, dict)
                    else None
                )
                if not (
                    isinstance(structured_data, dict)
                    and structured_data.get("is_food") is False
                ):
                    localization = parse_meal_response_localization(
                        structured_data,
                        command.language,
                    )

            if command.scan_mode == "food_label":
                nutrition = self.gpt_parser.parse_food_label_to_nutrition(vision_result)
                label_metadata = self.gpt_parser.parse_food_label_metadata(
                    vision_result
                )
                if not label_metadata.get("is_food_label", True):
                    raise ValidationException(
                        message=(
                            "Nutrition Facts label could not be read. "
                            "Please retake the label photo and try again."
                        ),
                        error_code="NOT_FOOD_LABEL_IMAGE",
                    )
                nutrition, label_metadata = await localize_food_label_display(
                    nutrition=nutrition,
                    metadata=label_metadata,
                    language=command.language,
                    translation_service=self.text_translation_service,
                )

                meal = Meal(
                    meal_id=str(uuid4()),
                    user_id=command.user_id,
                    status=MealStatus.READY,
                    created_at=meal_datetime,
                    meal_type=meal_type,
                    image=MealImage(
                        image_id=image_id,
                        format="jpeg",
                        size_bytes=len(raw_bytes),
                        url=command.image_url,
                    ),
                    source="food_label",
                    dish_name=MealDefaults.UNNAMED_FOOD_NAME,
                    ready_at=utc_now(),
                    raw_gpt_json=self.gpt_parser.extract_raw_json(vision_result),
                    food_label_metadata=label_metadata,
                    nutrition=nutrition,
                )

                async with self.uow_factory() as uow:
                    saved_meal = await uow.meals.save(meal)
                    await uow.commit()

                if meal_date is not None:
                    await publish_meal_event(
                        self.event_publisher,
                        saved_meal,
                        event_type="created",
                        environment=self.environment,
                        meal_date=meal_date,
                        language=command.language,
                        event_bus=self.event_bus,
                        source="scan_food_label",
                    )

                logger.info(
                    "[SCAN-BY-URL-FOOD-LABEL-COMPLETE] meal=%s vision=%.2fs total=%.2fs",
                    saved_meal.meal_id,
                    vision_elapsed,
                    time.time() - start,
                )

                return saved_meal

            if not self.gpt_parser.parse_is_food(vision_result):
                self._capture_rejected_scan(
                    image_id=image_id,
                    image_url=command.image_url,
                    reason="parser_not_food",
                )
                raise ValidationException(
                    message=(
                        "Image does not appear to contain food. "
                        "Please take a photo of food and try again."
                    ),
                    error_code="NOT_FOOD_IMAGE",
                )

            nutrition = self.gpt_parser.parse_to_nutrition(vision_result)
            dish_name = self.gpt_parser.parse_dish_name(vision_result)

            has_food = (
                nutrition
                and nutrition.food_items
                and len(nutrition.food_items) > 0
                and nutrition.calories > 0
            )
            if not has_food:
                self._capture_rejected_scan(
                    image_id=image_id,
                    image_url=command.image_url,
                    reason="nutrition_empty_or_zero_calorie",
                )
                raise ValidationException(
                    message=(
                        "No edible food detected in the image. "
                        "Please take a photo of food and try again."
                    ),
                    error_code="NOT_FOOD_IMAGE",
                )

            meal = Meal(
                meal_id=str(uuid4()),
                user_id=command.user_id,
                status=MealStatus.READY,
                created_at=meal_datetime,
                meal_type=meal_type,
                image=MealImage(
                    image_id=image_id,
                    format="jpeg",
                    size_bytes=len(raw_bytes),
                    url=command.image_url,
                ),
                source="scanner",
                dish_name=dish_name or "Unknown dish",
                emoji=self.gpt_parser.parse_emoji(vision_result),
                ready_at=utc_now(),
                raw_gpt_json=self.gpt_parser.extract_raw_json(vision_result),
                nutrition=nutrition,
            )
            meal = persist_meal_response_localization(meal, localization)

            async with self.uow_factory() as uow:
                saved_meal = await uow.meals.save(meal)
                await uow.commit()

            if meal_date is not None:
                await publish_meal_event(
                    self.event_publisher,
                    saved_meal,
                    event_type="created",
                    environment=self.environment,
                    meal_date=meal_date,
                    language=command.language,
                    event_bus=self.event_bus,
                    source="scan_by_url",
                )

            logger.info(
                "[SCAN-BY-URL-COMPLETE] meal=%s vision=%.2fs total=%.2fs",
                saved_meal.meal_id,
                vision_elapsed,
                time.time() - start,
            )

            return saved_meal

        except Exception:
            raise

    async def handle(self, command: ScanByUrlCommand) -> Meal:
        if not all([self.vision_service, self.gpt_parser]):
            raise RuntimeError("Required dependencies not configured")

        if self.meal_analyze_graph_enabled:
            workflow = self.meal_analyze_workflow or MealAnalyzeWorkflow()
            return await workflow.run_scan_by_url(
                command,
                self._handle_legacy_scan_by_url,
                runtime=MealAnalyzeRuntime(
                    command=command,
                    download_image_bytes=self._download_image_bytes,
                    compress_image=compress_image,
                    vision_service=self.vision_service,
                    gpt_parser=self.gpt_parser,
                    uow=self.uow_factory(),
                    event_publisher=self.event_publisher,
                    environment=self.environment,
                    event_bus=self.event_bus,
                    meal_translation_service=self.meal_translation_service,
                    text_translation_service=self.text_translation_service,
                ),
            )

        return await self._handle_legacy_scan_by_url(command)
