"""
Handler for immediate meal image upload and analysis.
"""

import logging
import time
from typing import Any
from uuid import uuid4

from src.api.exceptions import ValidationException
from src.app.commands.meal import UploadMealImageImmediatelyCommand
from src.app.events.base import EventHandler, handles
from src.app.events.meal.meal_events import MealCreatedEvent
from src.app.graphs.meal_analyze.runtime import MealAnalyzeRuntime
from src.app.services.meal_analyze_workflow import MealAnalyzeWorkflow
from src.domain.exceptions.ai_exceptions import (
    AIVisionError,
    AIVisionFailureKind,
    MealResponseLocalizationError,
)
from src.domain.model.meal import Meal, MealImage, MealStatus
from src.domain.model.meal.meal_response_localization import (
    parse_meal_response_localization,
    persist_meal_response_localization,
)
from src.domain.parsers.vision_response_parser import (
    VisionResponseParser as GPTResponseParser,
)
from src.domain.ports.async_unit_of_work_port import AsyncUnitOfWorkPort
from src.domain.ports.cache_port import CachePort
from src.domain.ports.image_store_port import ImageStorePort
from src.domain.ports.integration_event_publisher_port import (
    IntegrationEventPublisherPort,
)
from src.domain.ports.meal_insight_ai_port import MealInsightAIPort
from src.domain.ports.vision_ai_service_port import VisionAIServicePort
from src.domain.services.meal_analysis.fast_path_policy import MealAnalyzeFastPathPolicy
from src.domain.services.meal_analysis.meal_translation_service import (
    MealTranslationService,
)
from src.domain.services.meal_type_determination_service import (
    determine_meal_type_from_timestamp,
)
from src.domain.utils.timezone_utils import (
    get_zone_info,
    is_valid_timezone,
    noon_utc_for_date,
    utc_now,
)
from src.infra.config.settings import get_settings
from src.observability import capture_message, distribution_metric, increment_metric

logger = logging.getLogger(__name__)


@handles(UploadMealImageImmediatelyCommand)
class UploadMealImageImmediatelyHandler(
    EventHandler[UploadMealImageImmediatelyCommand, Meal]
):
    """Handler for immediate meal image upload and analysis."""

    def __init__(
        self,
        uow: AsyncUnitOfWorkPort,
        event_bus: Any,
        image_store: ImageStorePort = None,
        vision_service: VisionAIServicePort = None,
        gpt_parser: GPTResponseParser = None,
        meal_translation_service: MealTranslationService | None = None,
        fast_path_policy: MealAnalyzeFastPathPolicy | None = None,
        event_publisher: IntegrationEventPublisherPort | None = None,
        environment: str = "development",
        meal_value_insight_task_manager: Any | None = None,
        meal_value_insight_cache: CachePort | None = None,
        meal_value_insight_ai_manager: MealInsightAIPort | None = None,
        meal_analyze_workflow: MealAnalyzeWorkflow | None = None,
        meal_analyze_graph_enabled: bool = False,
    ):
        self.uow = uow
        self.event_bus = event_bus
        self.event_publisher = event_publisher
        self.environment = environment
        self.image_store = image_store
        self.vision_service = vision_service
        self.gpt_parser = gpt_parser
        self.meal_translation_service = meal_translation_service
        self.meal_value_insight_task_manager = meal_value_insight_task_manager
        self.meal_value_insight_cache = meal_value_insight_cache
        self.meal_value_insight_ai_manager = meal_value_insight_ai_manager
        self.meal_analyze_workflow = meal_analyze_workflow
        self.meal_analyze_graph_enabled = meal_analyze_graph_enabled
        if fast_path_policy is None:
            self._fast_path_policy = MealAnalyzeFastPathPolicy.from_settings(
                get_settings()
            )
        else:
            self._fast_path_policy = fast_path_policy

    async def _run_vision_analysis(
        self, command: UploadMealImageImmediatelyCommand, meal_id: str
    ) -> Any:
        max_attempts = max(1, self._fast_path_policy.max_attempts)
        last_error: Exception | None = None

        for attempt in range(1, max_attempts + 1):
            try:
                if command.user_description:
                    from src.domain.strategies.meal_analysis_strategy import (
                        AnalysisStrategyFactory,
                    )

                    logger.info(
                        f"[PHASE-1-START] meal={meal_id} | "
                        f"vision analysis with user context | "
                        f"attempt={attempt}/{max_attempts}"
                    )
                    strategy = AnalysisStrategyFactory.create_user_context_strategy(
                        command.user_description,
                        language=command.language,
                    )
                    result = await self.vision_service.analyze_with_strategy(
                        command.file_contents, strategy
                    )
                    self._validate_localized_vision_result(result, command.language)
                    return result

                logger.info(
                    f"[PHASE-1-START] meal={meal_id} | "
                    f"vision analysis | attempt={attempt}/{max_attempts}"
                )
                result = await self.vision_service.analyze(
                    command.file_contents,
                    language=command.language,
                )
                self._validate_localized_vision_result(result, command.language)
                return result
            except Exception as e:
                last_error = e
                # Deterministic failures (schema/parse/no-food) cannot be fixed by retrying
                # the same provider chain — skip outer retry and surface immediately
                if isinstance(e, MealResponseLocalizationError) or (
                    isinstance(e, AIVisionError)
                    and e.kind
                    in (
                        AIVisionFailureKind.schema_validation,
                        AIVisionFailureKind.json_parse,
                        AIVisionFailureKind.no_food,
                    )
                ):
                    failure_kind = (
                        e.kind.value
                        if isinstance(e, AIVisionError)
                        else "localization_validation"
                    )
                    logger.warning(
                        "[PHASE-1-NO-RETRY] meal=%s kind=%s attempt=%d/%d",
                        meal_id,
                        failure_kind,
                        attempt,
                        max_attempts,
                    )
                    raise
                logger.warning(
                    f"[PHASE-1-RETRY] meal={meal_id} | "
                    f"attempt={attempt}/{max_attempts} failed: {e}"
                )
                if attempt == max_attempts:
                    raise

        if last_error:
            raise last_error
        raise RuntimeError("Vision analysis failed without a captured exception.")

    @staticmethod
    def _validate_localized_vision_result(
        result: Any,
        language: str,
    ) -> None:
        """Reject incomplete same-call locale output before meal persistence."""
        if language == "en" or not isinstance(result, dict):
            return
        structured_data = (
            result.get("structured_data") if isinstance(result, dict) else None
        )
        if (
            isinstance(structured_data, dict)
            and structured_data.get("is_food") is False
        ):
            return
        parse_meal_response_localization(
            structured_data,
            language,
        )

    def _validate_cloudinary_url(self, url: str) -> bool:
        """Validate that the Cloudinary response is a valid HTTPS URL."""
        return url is not None and url.startswith("https://")

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
                "operation": "upload_meal_image_immediate",
                "ai_purpose": "meal_scan",
                "image_id": image_id,
                "image_url": image_url,
                "rejection_reason": reason,
            },
        )

    async def _handle_parallel_upload(
        self,
        command: UploadMealImageImmediatelyCommand,
    ) -> Meal:
        """Upload image first, verify success, then create meal record."""
        # Step 1: Generate image ID upfront
        image_id = str(uuid4())

        # Step 2: Upload to Cloudinary FIRST (before any DB operations)
        logger.info(f"[UPLOAD-START] image_id={image_id}")
        start = time.time()

        try:
            image_url = await self.image_store.save_async(
                command.file_contents,
                command.content_type,
                image_id,
            )
        except Exception as e:
            raise RuntimeError(f"Cloudinary upload failed: {e}") from e

        # Step 3: Verify we got a valid URL back
        if not self._validate_cloudinary_url(image_url):
            raise RuntimeError("Cloudinary upload failed - invalid URL returned")

        upload_elapsed = time.time() - start
        logger.info(
            f"[UPLOAD-COMPLETE] image_id={image_id} | elapsed={upload_elapsed:.2f}s"
        )

        # Step 4: Run AI analysis
        logger.info(f"[ANALYSIS-START] image_id={image_id}")
        analysis_start = time.time()

        try:
            analysis_result = await self._run_vision_analysis(command, image_id)
        except Exception as exc:
            # Image uploaded but analysis failed - acceptable orphan in Cloudinary
            if (
                isinstance(exc, AIVisionError)
                and exc.kind == AIVisionFailureKind.no_food
            ):
                self._capture_rejected_scan(
                    image_id=image_id,
                    image_url=image_url,
                    reason="vision_no_food",
                )
            increment_metric(
                "ai.vision.request.count",
                attributes={"status": "failure", "ai_purpose": "meal_scan"},
            )
            raise

        analysis_elapsed = time.time() - analysis_start
        increment_metric(
            "ai.vision.request.count",
            attributes={"status": "success", "ai_purpose": "meal_scan"},
        )
        distribution_metric(
            "ai.vision.request.duration_ms",
            analysis_elapsed * 1000,
            unit="millisecond",
            attributes={"ai_purpose": "meal_scan", "status": "success"},
        )
        logger.info(
            f"[ANALYSIS-COMPLETE] image_id={image_id} | elapsed={analysis_elapsed:.2f}s"
        )

        # Step 5: Parse nutrition and validate food detected.
        if not self.gpt_parser.parse_is_food(analysis_result):
            self._capture_rejected_scan(
                image_id=image_id,
                image_url=image_url,
                reason="parser_not_food",
            )
            raise ValidationException(
                message=(
                    "Image does not appear to contain food. "
                    "Please take a photo of food and try again."
                ),
                error_code="NOT_FOOD_IMAGE",
            )

        nutrition = self.gpt_parser.parse_to_nutrition(analysis_result)
        dish_name = self.gpt_parser.parse_dish_name(analysis_result)

        has_food = (
            nutrition
            and nutrition.food_items
            and len(nutrition.food_items) > 0
            and nutrition.calories > 0
        )
        if not has_food:
            self._capture_rejected_scan(
                image_id=image_id,
                image_url=image_url,
                reason="nutrition_empty_or_zero_calorie",
            )
            raise ValidationException(
                message=(
                    "No edible food detected in the image. "
                    "Please take a photo of food and try again."
                ),
                error_code="NOT_FOOD_IMAGE",
            )
        structured_data = (
            analysis_result.get("structured_data")
            if isinstance(analysis_result, dict)
            else None
        )
        localization = parse_meal_response_localization(
            structured_data,
            command.language,
            expected_food_count=len(nutrition.food_items or []),
        )

        # Step 6: NOW create meal record with verified image URL
        async with self.uow as uow:
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
            local_datetime = meal_datetime.astimezone(zone_info)
            meal_type = determine_meal_type_from_timestamp(local_datetime)

            meal = Meal(
                meal_id=str(uuid4()),
                user_id=command.user_id,
                status=MealStatus.READY,
                created_at=meal_datetime,
                meal_type=meal_type,
                image=MealImage(
                    image_id=image_id,
                    format="jpeg" if "jpeg" in command.content_type else "png",
                    size_bytes=len(command.file_contents),
                    url=image_url,
                ),
                source="scanner",
                dish_name=dish_name or "Unknown dish",
                emoji=self.gpt_parser.parse_emoji(analysis_result),
                ready_at=utc_now(),
                raw_gpt_json=self.gpt_parser.extract_raw_json(analysis_result),
                nutrition=nutrition,
            )
            meal = persist_meal_response_localization(meal, localization)

            saved_meal = await uow.meals.save(meal)
            await uow.commit()

        if self.event_publisher is not None and meal_date is not None:
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

        total_elapsed = time.time() - start
        logger.info(
            f"[MEAL-CREATED] meal={saved_meal.meal_id} | "
            f"image_id={image_id} | "
            f"total_elapsed={total_elapsed:.2f}s"
        )

        return saved_meal

    async def handle(self, command: UploadMealImageImmediatelyCommand) -> Meal:
        """Handle immediate meal image upload and analysis."""
        if command.scan_mode == "food_label":
            raise ValidationException(
                message=(
                    "Food-label scans require the scan-by-url image flow. "
                    "Use /v1/meals/food-label/scan-by-url."
                ),
                error_code="INVALID_SCAN_MODE",
                details={"scan_mode": command.scan_mode},
            )

        if not all([self.image_store, self.vision_service, self.gpt_parser]):
            raise RuntimeError("Required dependencies not configured")

        if self.meal_analyze_graph_enabled:
            workflow = self.meal_analyze_workflow or MealAnalyzeWorkflow()
            return await workflow.run_uploaded(
                command,
                self._handle_parallel_upload,
                runtime=MealAnalyzeRuntime(
                    command=command,
                    image_store=self.image_store,
                    vision_service=self.vision_service,
                    gpt_parser=self.gpt_parser,
                    uow=self.uow,
                    event_publisher=self.event_publisher,
                    environment=self.environment,
                    meal_value_insight_task_manager=self.meal_value_insight_task_manager,
                    meal_value_insight_cache=self.meal_value_insight_cache,
                    meal_value_insight_ai_manager=self.meal_value_insight_ai_manager,
                    event_bus=self.event_bus,
                    meal_translation_service=self.meal_translation_service,
                    max_vision_attempts=max(1, self._fast_path_policy.max_attempts),
                ),
            )

        return await self._handle_parallel_upload(command)
