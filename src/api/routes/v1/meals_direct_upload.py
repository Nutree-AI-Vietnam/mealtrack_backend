"""Accept a meal photo at a locally signed URL and store it in R2."""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, File, Query, UploadFile

from src.api.exceptions import (
    AuthenticationException,
    ExternalServiceException,
    ValidationException,
)
from src.api.routes.v1.meals_analyze import MAX_FILE_SIZE
from src.infra.adapters.meal_photo_upload import ticket_is_valid
from src.infra.adapters.r2_object_store import R2ObjectStore, R2UploadError
from src.infra.config.settings import get_settings

logger = logging.getLogger(__name__)
router = APIRouter()

_ALLOWED_CONTENT_TYPES = {
    "image/jpeg": "image/jpeg",
    "image/jpg": "image/jpeg",
    "image/png": "image/png",
    "image/webp": "image/webp",
    "image/heic": "image/heic",
    "image/heif": "image/heic",
}


@router.post("/direct-upload/{image_id}")
async def direct_upload(
    image_id: str,
    exp: int = Query(..., description="Unix expiry from the upload token"),
    sig: str = Query(..., description="HMAC from the upload token"),
    file: UploadFile = File(...),
):
    """Store one meal photo. The signature is the authorization."""
    try:
        parsed_id = str(uuid.UUID(image_id))
    except ValueError as exc:
        raise ValidationException(
            message="image_id must be a UUID",
            error_code="INVALID_IMAGE_ID",
        ) from exc

    settings = get_settings()
    if not ticket_is_valid(settings.R2_SECRET_ACCESS_KEY, parsed_id, exp, sig):
        raise AuthenticationException(
            message="Upload link is invalid or expired",
            error_code="INVALID_UPLOAD_TICKET",
        )

    content_type = _ALLOWED_CONTENT_TYPES.get(
        (file.content_type or "").split(";")[0].strip().lower()
    )
    if content_type is None:
        raise ValidationException(
            message="Unsupported image type",
            error_code="INVALID_FILE_TYPE",
        )

    contents = await file.read()
    if not contents:
        raise ValidationException(
            message="Image file is empty",
            error_code="EMPTY_FILE",
        )
    if len(contents) > MAX_FILE_SIZE:
        raise ValidationException(
            message=f"File size exceeds maximum allowed ({MAX_FILE_SIZE // (1024 * 1024)} MB)",
            error_code="FILE_TOO_LARGE",
            details={"size": len(contents), "max_size": MAX_FILE_SIZE},
        )

    store = R2ObjectStore(
        account_id=settings.CLOUDFLARE_ACCOUNT_ID,
        access_key_id=settings.R2_ACCESS_KEY_ID,
        secret_access_key=settings.R2_SECRET_ACCESS_KEY,
        bucket=settings.R2_BUCKET,
        public_base_url=settings.R2_PUBLIC_BASE_URL,
    )
    try:
        public_url = await store.put(
            f"mealtrack/{parsed_id}",
            contents,
            content_type,
        )
    except R2UploadError as exc:
        raise ExternalServiceException(
            message="Image upload is temporarily unavailable. Please try again.",
            error_code="IMAGE_UPLOAD_UNAVAILABLE",
        ) from exc

    logger.info("[DIRECT-UPLOAD] image_id=%s bytes=%s", parsed_id, len(contents))
    return {
        "secure_url": public_url,
        "result": {"id": parsed_id, "variants": [public_url]},
    }
