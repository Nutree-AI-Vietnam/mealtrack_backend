"""Accept the current app's meal-photo upload and store it in R2."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, Query, UploadFile

from src.api.base_dependencies import get_image_store
from src.api.exceptions import (
    AuthenticationException,
    ExternalServiceException,
    ValidationException,
)
from src.domain.exceptions.meal_photo_upload import MealPhotoUploadError

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/direct-upload/{image_id}")
async def direct_upload(
    image_id: str,
    exp: int = Query(..., description="Unix expiry from the upload token"),
    sig: str = Query(..., description="HMAC from the upload token"),
    file: UploadFile = File(...),
    image_store=Depends(get_image_store),
):
    """Store one meal photo posted by the current app."""
    try:
        public_url = await image_store.store_signed_upload(
            image_id=image_id,
            expires_at=exp,
            signature=sig,
            content_type=file.content_type or "",
            body=await file.read(),
        )
    except MealPhotoUploadError as exc:
        if exc.error_code == "INVALID_UPLOAD_TICKET":
            raise AuthenticationException(
                message=exc.message,
                error_code=exc.error_code,
            ) from exc
        if exc.error_code == "IMAGE_UPLOAD_UNAVAILABLE":
            raise ExternalServiceException(
                message=exc.message,
                error_code=exc.error_code,
            ) from exc
        raise ValidationException(
            message=exc.message,
            error_code=exc.error_code,
        ) from exc

    logger.info("[DIRECT-UPLOAD] image_id=%s", image_id)
    return {
        "secure_url": public_url,
        "result": {"id": image_id, "variants": [public_url]},
    }
