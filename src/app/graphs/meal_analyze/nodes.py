"""Nodes for the meal image analysis graph."""

import logging

from src.api.exceptions import ValidationException
from src.app.commands.meal.scan_by_url_command import ScanByUrlCommand
from src.app.commands.meal.upload_meal_image_immediately_command import (
    UploadMealImageImmediatelyCommand,
)
from src.app.events.meal.meal_events import MealCreatedEvent
from src.app.graphs.meal_analyze.quality_gate import (
    DEFAULT_GRAPH_VERSION,
    normalize_scan_mode,
)
from src.app.graphs.meal_analyze.runtime import AcquiredImage, MealAnalyzeRuntime
from src.app.graphs.meal_analyze.state import MealAnalyzeGraphState
from src.app.services.food_label_localizer import localize_food_label_display
from src.domain.constants import MealDefaults
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
from src.domain.services.meal_type_determination_service import (
    determine_meal_type_from_timestamp,
)
from src.domain.strategies.meal_analysis_strategy import (
    AnalysisStrategyFactory,
    FoodLabelImageAnalysisStrategy,
)
from src.domain.utils.image_compression import to_compressed_cloudinary_url
from src.domain.utils.timezone_utils import (
    get_zone_info,
    is_valid_timezone,
    noon_utc_for_date,
    utc_now,
)

logger = logging.getLogger(__name__)


def prepare_input(state: MealAnalyzeGraphState) -> MealAnalyzeGraphState:
    """Attach graph metadata before later workflow nodes run."""
    return {
        "graph_version": state.get("graph_version") or DEFAULT_GRAPH_VERSION,
        "prepared": True,
    }


def select_mode(state: MealAnalyzeGraphState) -> MealAnalyzeGraphState:
    """Normalize the scan mode without changing behavior."""
    return {"selected_mode": normalize_scan_mode(state)}


async def acquire_image(
    state: MealAnalyzeGraphState,
    runtime: MealAnalyzeRuntime,
) -> MealAnalyzeGraphState:
    """Acquire image bytes while keeping raw payloads out of graph state."""
    command = runtime.command
    if isinstance(command, UploadMealImageImmediatelyCommand):
        return await _acquire_uploaded_image(command, runtime)
    if isinstance(command, ScanByUrlCommand):
        return await _acquire_scan_by_url_image(command, runtime)
    raise TypeError(f"Unsupported meal analyze command: {type(command).__name__}")


async def _acquire_uploaded_image(
    command: UploadMealImageImmediatelyCommand,
    runtime: MealAnalyzeRuntime,
) -> MealAnalyzeGraphState:
    if runtime.image_store is None:
        raise RuntimeError("Image store dependency is required for upload acquisition")

    image_id = runtime.image_id_factory()
    image_url = await runtime.image_store.save_async(
        command.file_contents,
        command.content_type,
        image_id,
    )
    if not image_url or not image_url.startswith("https://"):
        raise RuntimeError("Cloudinary upload failed - invalid URL returned")

    runtime.acquired_image = AcquiredImage(
        image_id=image_id,
        image_url=image_url,
        persisted_image_id=image_id,
        persisted_image_url=image_url,
        source_bytes=command.file_contents,
        analysis_bytes=command.file_contents,
        content_type=command.content_type,
        content_kind="meal_image",
    )
    return {
        "image_id": image_id,
        "content_kind": "meal_image",
        "image_size_bytes": len(command.file_contents),
    }


async def _acquire_scan_by_url_image(
    command: ScanByUrlCommand,
    runtime: MealAnalyzeRuntime,
) -> MealAnalyzeGraphState:
    if runtime.download_image_bytes is None:
        raise RuntimeError(
            "Image downloader dependency is required for URL acquisition"
        )

    is_food_label = command.scan_mode == "food_label"
    source_url = command.image_url
    source_public_id = command.public_id
    persisted_image_id = command.public_id.split("/")[-1]
    persisted_image_url = command.image_url
    if is_food_label and command.label_crop_image_url:
        source_url = command.label_crop_image_url
        source_public_id = command.label_crop_public_id or command.public_id

    download_url = (
        source_url if is_food_label else to_compressed_cloudinary_url(source_url)
    )
    raw_bytes = await runtime.download_image_bytes(download_url)
    analysis_bytes = (
        raw_bytes
        if (is_food_label or len(raw_bytes) <= 200 * 1024)
        else runtime.compress_image(raw_bytes)
    )
    content_kind = "food_label_image" if is_food_label else "meal_image"
    image_id = source_public_id.split("/")[-1]

    runtime.acquired_image = AcquiredImage(
        image_id=image_id,
        image_url=source_url,
        persisted_image_id=persisted_image_id,
        persisted_image_url=persisted_image_url,
        source_bytes=raw_bytes,
        analysis_bytes=analysis_bytes,
        content_type="image/jpeg",
        content_kind=content_kind,
    )
    return {
        "image_id": image_id,
        "content_kind": content_kind,
        "image_size_bytes": len(raw_bytes),
    }


async def schedule_value_insights(
    state: MealAnalyzeGraphState,
    runtime: MealAnalyzeRuntime,
) -> MealAnalyzeGraphState:
    """Schedule profile-aware meal insight generation as a post-persist step."""
    source = "meal_analyze_graph"
    if runtime.saved_meal is None:
        return {
            "meal_value_insight_scheduled": False,
            "meal_value_insight_source": source,
        }

    try:
        scheduled = runtime.meal_value_insight_scheduler(
            runtime.meal_value_insight_task_manager,
            runtime.saved_meal,
            language=runtime.command.language,
            cache_service=runtime.meal_value_insight_cache,
            ai_manager=runtime.meal_value_insight_ai_manager,
            event_bus=runtime.event_bus,
            user_id=runtime.command.user_id,
            source=source,
        )
    except Exception as exc:
        logger.info(
            "meal_value_insights.graph_schedule_failed meal_id=%s user_id=%s error=%s",
            runtime.saved_meal.meal_id,
            runtime.command.user_id,
            type(exc).__name__,
        )
        scheduled = False

    runtime.saved_meal._meal_value_insight_scheduled = scheduled
    return {
        "meal_value_insight_scheduled": scheduled,
        "meal_value_insight_source": source,
    }


def complete(state: MealAnalyzeGraphState) -> MealAnalyzeGraphState:
    """Mark scaffold execution complete."""
    return {"completed": True}


async def analyze_vision(
    state: MealAnalyzeGraphState,
    runtime: MealAnalyzeRuntime,
) -> MealAnalyzeGraphState:
    """Run configured vision analysis against acquired bytes."""
    if runtime.acquired_image is None:
        raise RuntimeError("Image must be acquired before vision analysis")
    if runtime.vision_service is None:
        raise RuntimeError("Vision service dependency is required for analysis")

    command = runtime.command
    image_bytes = runtime.acquired_image.analysis_bytes
    if isinstance(command, ScanByUrlCommand) and command.scan_mode == "food_label":
        strategy = FoodLabelImageAnalysisStrategy(crop_metadata=command.crop_metadata)
        runtime.vision_result = await runtime.vision_service.analyze_with_strategy(
            image_bytes,
            strategy,
        )
    elif command.user_description:
        strategy = AnalysisStrategyFactory.create_user_context_strategy(
            command.user_description,
            language=command.language,
        )
        runtime.vision_result = await _run_vision_with_retry(
            runtime,
            lambda: _analyze_and_validate_locale(
                runtime,
                lambda: runtime.vision_service.analyze_with_strategy(
                    image_bytes,
                    strategy,
                ),
            ),
        )
    else:
        runtime.vision_result = await _run_vision_with_retry(
            runtime,
            lambda: _analyze_and_validate_locale(
                runtime,
                lambda: runtime.vision_service.analyze(
                    image_bytes,
                    language=command.language,
                ),
            ),
        )

    return {"vision_analyzed": True}


async def _analyze_and_validate_locale(runtime: MealAnalyzeRuntime, operation):
    """Run one vision attempt and reject incomplete localized display data."""
    result = await operation()
    command = runtime.command
    if (
        command.language == "en"
        or not runtime.gpt_parser
        or not isinstance(result, dict)
    ):
        runtime.localization = None
        return result

    structured_data = (
        result.get("structured_data") if isinstance(result, dict) else None
    )
    if isinstance(structured_data, dict) and structured_data.get("is_food") is False:
        runtime.localization = None
        return result
    runtime.localization = parse_meal_response_localization(
        structured_data,
        command.language,
    )
    return result


async def _run_vision_with_retry(
    runtime: MealAnalyzeRuntime,
    operation,
) -> dict:
    max_attempts = max(1, runtime.max_vision_attempts)
    last_error: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            return await operation()
        except Exception as exc:
            last_error = exc
            if isinstance(exc, MealResponseLocalizationError) or (
                isinstance(exc, AIVisionError)
                and exc.kind
                in (
                    AIVisionFailureKind.schema_validation,
                    AIVisionFailureKind.json_parse,
                    AIVisionFailureKind.no_food,
                )
            ):
                raise
            if attempt == max_attempts:
                raise
            logger.warning(
                "[MEAL-ANALYZE-GRAPH] vision retry attempt=%d/%d failed: %s",
                attempt,
                max_attempts,
                exc,
            )

    if last_error:
        raise last_error
    raise RuntimeError("Vision analysis failed without a captured exception.")


async def parse_nutrition(
    state: MealAnalyzeGraphState,
    runtime: MealAnalyzeRuntime,
) -> MealAnalyzeGraphState:
    """Parse vision output into safe domain data held by runtime."""
    if runtime.vision_result is None:
        raise RuntimeError("Vision result must exist before parsing")
    if runtime.gpt_parser is None:
        raise RuntimeError("Parser dependency is required for nutrition parsing")

    is_food_label = (
        isinstance(runtime.command, ScanByUrlCommand)
        and runtime.command.scan_mode == "food_label"
    )
    if is_food_label:
        runtime.nutrition = runtime.gpt_parser.parse_food_label_to_nutrition(
            runtime.vision_result
        )
        runtime.label_metadata = runtime.gpt_parser.parse_food_label_metadata(
            runtime.vision_result
        )
        if not runtime.label_metadata.get("is_food_label", True):
            raise ValidationException(
                message=(
                    "Nutrition Facts label could not be read. "
                    "Please retake the label photo and try again."
                ),
                error_code="NOT_FOOD_LABEL_IMAGE",
            )
        runtime.nutrition, runtime.label_metadata = await localize_food_label_display(
            nutrition=runtime.nutrition,
            metadata=runtime.label_metadata,
            language=runtime.command.language,
            translation_service=runtime.text_translation_service,
        )
        return {"nutrition_parsed": True}

    if not runtime.gpt_parser.parse_is_food(runtime.vision_result):
        raise ValidationException(
            message=(
                "Image does not appear to contain food. "
                "Please take a photo of food and try again."
            ),
            error_code="NOT_FOOD_IMAGE",
        )

    nutrition = runtime.gpt_parser.parse_to_nutrition(runtime.vision_result)
    has_food = (
        nutrition
        and nutrition.food_items
        and len(nutrition.food_items) > 0
        and nutrition.calories > 0
    )
    if not has_food:
        raise ValidationException(
            message=(
                "No edible food detected in the image. "
                "Please take a photo of food and try again."
            ),
            error_code="NOT_FOOD_IMAGE",
        )
    runtime.nutrition = nutrition
    return {"nutrition_parsed": True}


async def maybe_validate_reference(
    state: MealAnalyzeGraphState,
    runtime: MealAnalyzeRuntime,
) -> MealAnalyzeGraphState:
    """Run optional nutrition reference validation for meal scans only."""
    if not runtime.fatsecret_validation_enabled:
        return {"reference_validated": False}
    if runtime.food_reference_validation_service is None:
        return {"reference_validated": False}
    if (
        isinstance(runtime.command, ScanByUrlCommand)
        and runtime.command.scan_mode == "food_label"
    ):
        return {"reference_validated": False}

    # Full meal validation happens after the domain object exists.
    return {"reference_validation_pending": True}


async def persist_meal(
    state: MealAnalyzeGraphState,
    runtime: MealAnalyzeRuntime,
) -> MealAnalyzeGraphState:
    """Persist a READY meal and return the canonical response source."""
    if runtime.acquired_image is None:
        raise RuntimeError("Image must be acquired before meal persistence")
    if runtime.nutrition is None:
        raise RuntimeError("Nutrition must be parsed before meal persistence")
    if runtime.gpt_parser is None or runtime.uow is None:
        raise RuntimeError("Parser and UoW dependencies are required for persistence")

    command = runtime.command
    meal_datetime, meal_date, meal_type = await _resolve_meal_datetime(runtime)
    runtime.meal_date = meal_date

    is_food_label = (
        isinstance(command, ScanByUrlCommand) and command.scan_mode == "food_label"
    )
    meal = _build_ready_meal(
        runtime=runtime,
        meal_datetime=meal_datetime,
        meal_type=meal_type,
        is_food_label=is_food_label,
    )

    if (
        state.get("reference_validation_pending")
        and runtime.food_reference_validation_service is not None
    ):
        meal = await runtime.food_reference_validation_service.validate_meal(meal)

    if not is_food_label:
        meal = persist_meal_response_localization(meal, runtime.localization)

    async with runtime.uow as uow:
        saved_meal = await uow.meals.save(meal)
        await uow.commit()

    if runtime.event_publisher is not None and runtime.meal_date is not None:
        try:
            event = MealCreatedEvent(
                environment=runtime.environment,
                aggregate_id=saved_meal.meal_id,
                data={
                    "user_id": runtime.command.user_id,
                    "meal_id": saved_meal.meal_id,
                    "meal_date": runtime.meal_date.isoformat(),
                },
            )
            await runtime.event_publisher.publish(event.to_payload())
        except Exception as exc:
            logging.getLogger(__name__).error(
                "Failed to publish meal created event: %s", exc
            )

    runtime.saved_meal = saved_meal
    return {
        "meal_id": saved_meal.meal_id,
        "result": saved_meal,
        "cache_invalidated": True,
    }


async def invalidate_cache(
    state: MealAnalyzeGraphState,
    runtime: MealAnalyzeRuntime,
) -> MealAnalyzeGraphState:
    """Confirm the transactional event created by ``persist_meal``."""
    if state.get("cache_invalidated"):
        return {"cache_invalidated": True}
    return {"cache_invalidated": True}


async def _resolve_meal_datetime(runtime: MealAnalyzeRuntime):
    command = runtime.command
    async with runtime.uow as uow:
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
    meal_type = determine_meal_type_from_timestamp(meal_datetime.astimezone(zone_info))
    return meal_datetime, meal_date, meal_type


def _build_ready_meal(
    *,
    runtime: MealAnalyzeRuntime,
    meal_datetime,
    meal_type: str,
    is_food_label: bool,
) -> Meal:
    acquired = runtime.acquired_image
    command = runtime.command
    if acquired is None or runtime.gpt_parser is None or runtime.vision_result is None:
        raise RuntimeError("Meal build dependencies are incomplete")

    image_format = "png" if acquired.content_type == "image/png" else "jpeg"
    dish_name = MealDefaults.UNNAMED_FOOD_NAME
    source = "food_label" if is_food_label else "scanner"
    if not is_food_label:
        dish_name = (
            runtime.gpt_parser.parse_dish_name(runtime.vision_result) or "Unknown dish"
        )

    return Meal(
        meal_id=runtime.meal_id_factory(),
        user_id=command.user_id,
        status=MealStatus.READY,
        created_at=meal_datetime,
        meal_type=meal_type,
        image=MealImage(
            image_id=acquired.persisted_image_id,
            format=image_format,
            size_bytes=len(acquired.source_bytes),
            url=acquired.persisted_image_url,
        ),
        source=source,
        dish_name=dish_name,
        emoji=None
        if is_food_label
        else runtime.gpt_parser.parse_emoji(runtime.vision_result),
        ready_at=utc_now(),
        raw_gpt_json=runtime.gpt_parser.extract_raw_json(runtime.vision_result),
        food_label_metadata=runtime.label_metadata if is_food_label else None,
        nutrition=runtime.nutrition,
    )
