"""POST /v1/meals/scan-by-url — analyze a Cloudinary-hosted meal image via bytes path."""

import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Body, Depends, Request, status
from pydantic import BaseModel

from src.api.base_dependencies import (
    get_async_food_reference_repository,
)
from src.api.dependencies.auth import get_current_user_id
from src.api.dependencies.event_bus import get_configured_event_bus
from src.api.exceptions import ValidationException, handle_exception
from src.api.mappers.meal_mapper import MealMapper
from src.api.middleware.accept_language import get_request_language
from src.api.routes.v1.meals_route_helpers import (
    load_food_reference_display_projections,
)
from src.api.schemas.response import DetailedMealResponse
from src.app.commands.meal.scan_by_url_command import ScanByUrlCommand
from src.domain.exceptions.ai_exceptions import MealResponseLocalizationError
from src.domain.services.prompts.input_sanitizer import sanitize_user_description

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/meals", tags=["Meals"])

_ALLOWED_HOST = "res.cloudinary.com"


class ScanByUrlRequest(BaseModel):
    image_url: str
    image_id: str
    user_description: str | None = None
    target_date: str | None = None
    scan_mode: str = "scanner"


class FoodLabelScanByUrlRequest(BaseModel):
    image_url: str
    image_id: str
    label_crop_image_url: str | None = None
    label_crop_image_id: str | None = None
    target_date: str | None = None
    crop_metadata: dict[str, Any] | None = None


def _validate_cloudinary_url(image_url: str, image_id: str) -> None:
    if not image_url.startswith(f"https://{_ALLOWED_HOST}/"):
        raise ValidationException(
            message="image_url must be a Cloudinary res URL",
            error_code="INVALID_IMAGE_URL",
            details={"url": image_url},
        )

    if image_id not in image_url:
        raise ValidationException(
            message="image_id does not match image_url",
            error_code="IMAGE_ID_URL_MISMATCH",
            details={"image_id": image_id},
        )


def _parse_target_date(target_date: str | None):
    if not target_date:
        return None
    try:
        return datetime.strptime(target_date, "%Y-%m-%d").date()
    except ValueError as e:
        raise ValidationException(
            message="Invalid date format. Use YYYY-MM-DD format.",
            error_code="INVALID_DATE_FORMAT",
            details={"date": target_date},
        ) from e


async def _scan_by_url(
    *,
    request: Request,
    user_id: str,
    event_bus: Any,
    image_url: str,
    image_id: str,
    target_date: str | None,
    user_description: str | None,
    scan_mode: str,
    food_reference_repository=None,
    label_crop_image_url: str | None = None,
    label_crop_image_id: str | None = None,
    crop_metadata: dict[str, Any] | None = None,
) -> DetailedMealResponse:
    _validate_cloudinary_url(image_url, image_id)
    if bool(label_crop_image_url) != bool(label_crop_image_id):
        raise ValidationException(
            message="label_crop_image_url and label_crop_image_id must be supplied together",
            error_code="INVALID_LABEL_CROP_IMAGE",
            details={
                "has_url": bool(label_crop_image_url),
                "has_id": bool(label_crop_image_id),
            },
        )
    label_crop_public_id = None
    if label_crop_image_url and label_crop_image_id:
        _validate_cloudinary_url(label_crop_image_url, label_crop_image_id)
        label_crop_public_id = f"mealtrack/{label_crop_image_id}"

    parsed_target_date = _parse_target_date(target_date)
    sanitized_description = (
        sanitize_user_description(user_description) if user_description else None
    )
    language = get_request_language(request)
    public_id = f"mealtrack/{image_id}"

    command = ScanByUrlCommand(
        user_id=user_id,
        image_url=image_url,
        public_id=public_id,
        user_description=sanitized_description,
        target_date=parsed_target_date,
        language=language,
        scan_mode=scan_mode,
        label_crop_image_url=label_crop_image_url,
        label_crop_public_id=label_crop_public_id,
        crop_metadata=crop_metadata,
    )

    try:
        meal = await event_bus.send(command)
    except MealResponseLocalizationError as e:
        raise handle_exception(e) from e
    except (RuntimeError, ValueError) as e:
        logger.warning("[SCAN-BY-URL] food not detected: %s", e)
        raise ValidationException(
            message="Could not identify food in the image. Please try again with a food photo.",
            error_code="NOT_FOOD_IMAGE",
            details={"error_message": str(e)},
        ) from e

    if meal.status.value == "FAILED":
        error_message = meal.error_message or "Analysis failed"
        raise ValidationException(
            message=f"Failed to analyze meal image: {error_message}",
            error_code="FAILED_TO_ANALYZE_MEAL_IMAGE",
            details={"error_message": error_message},
        )

    response_image_url = getattr(getattr(meal, "image", None), "url", None)
    # Prefer the just-validated request URL if persistence dropped it.
    if not response_image_url:
        response_image_url = image_url
    display_projections = await load_food_reference_display_projections(
        meal, food_reference_repository, language=language
    )
    return MealMapper.to_detailed_response(
        meal,
        response_image_url,
        target_language=language,
        display_name_by_food_reference=display_projections,
    )


@router.post(
    "/scan-by-url",
    status_code=status.HTTP_200_OK,
    response_model=DetailedMealResponse,
)
async def scan_meal_by_url(
    request: Request,
    body: ScanByUrlRequest = Body(...),
    user_id: str = Depends(get_current_user_id),
    event_bus: Any = Depends(get_configured_event_bus),
    food_reference_repository=Depends(get_async_food_reference_repository),
):
    """Analyze a meal image already uploaded to Cloudinary via the safe bytes-download path."""
    try:
        if body.scan_mode != "scanner":
            raise ValidationException(
                message="Use /v1/meals/food-label/scan-by-url for Nutrition Facts labels.",
                error_code="INVALID_SCAN_MODE",
                details={"scan_mode": body.scan_mode},
            )

        return await _scan_by_url(
            request=request,
            user_id=user_id,
            image_url=body.image_url,
            image_id=body.image_id,
            target_date=body.target_date,
            user_description=body.user_description,
            scan_mode="scanner",
            event_bus=event_bus,
            food_reference_repository=food_reference_repository,
        )

    except ValidationException:
        raise
    except Exception as e:
        raise handle_exception(e) from e


@router.post(
    "/food-label/scan-by-url",
    status_code=status.HTTP_200_OK,
    response_model=DetailedMealResponse,
)
async def scan_food_label_by_url(
    request: Request,
    body: FoodLabelScanByUrlRequest = Body(...),
    user_id: str = Depends(get_current_user_id),
    event_bus: Any = Depends(get_configured_event_bus),
    food_reference_repository=Depends(get_async_food_reference_repository),
):
    """Analyze a Cloudinary-hosted Nutrition Facts label."""
    try:
        return await _scan_by_url(
            request=request,
            user_id=user_id,
            image_url=body.image_url,
            image_id=body.image_id,
            target_date=body.target_date,
            user_description=None,
            scan_mode="food_label",
            label_crop_image_url=body.label_crop_image_url,
            label_crop_image_id=body.label_crop_image_id,
            crop_metadata=body.crop_metadata,
            event_bus=event_bus,
            food_reference_repository=food_reference_repository,
        )
    except ValidationException:
        raise
    except Exception as e:
        raise handle_exception(e) from e
