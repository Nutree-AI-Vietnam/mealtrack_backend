"""Accept the current app's meal-photo upload and store it in R2."""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import APIRouter, Depends, File, Query, Request, Response, UploadFile
from fastapi.routing import APIRoute

from src.api.base_dependencies import get_image_store
from src.api.exceptions import (
    AuthenticationException,
    ExternalServiceException,
    MealTrackException,
    ValidationException,
)
from src.api.middleware.rate_limit import get_ip_only_rate_limit_key, limiter
from src.domain.exceptions.meal_photo_upload import MealPhotoUploadError
from src.domain.ports.image_store_port import ImageStorePort

logger = logging.getLogger(__name__)

# Uploads per sender IP. A bad ticket is refused before it is counted.
DIRECT_UPLOAD_RATE_LIMIT = "30/minute"


@limiter.shared_limit(
    DIRECT_UPLOAD_RATE_LIMIT,
    scope="meal_photo_direct_upload",
    key_func=get_ip_only_rate_limit_key,
)
async def _count_upload(request: Request) -> None:
    """Count one upload against its sender IP; raises when over the limit."""


class _TicketFirstRoute(APIRoute):
    """Check the upload ticket and the IP limit before FastAPI reads the body.

    FastAPI parses a multipart body, spooling the photo, before it resolves
    dependencies or calls the endpoint, so these checks wrap its handler.
    """

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handle = super().get_route_handler()

        async def check_then_handle(request: Request) -> Response:
            _check_ticket(_image_store(request), request)
            await _count_upload(request)
            return await handle(request)

        return check_then_handle


router = APIRouter(route_class=_TicketFirstRoute)


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
        raise _api_error(exc) from exc

    logger.info("[DIRECT-UPLOAD] image_id=%s", image_id)
    return {
        "secure_url": public_url,
        "result": {"id": image_id, "variants": [public_url]},
    }


def _image_store(request: Request) -> ImageStorePort:
    """The store Depends(get_image_store) resolves to, overrides included."""
    overrides = request.app.dependency_overrides
    return overrides.get(get_image_store, get_image_store)()


def _check_ticket(image_store: ImageStorePort, request: Request) -> None:
    try:
        expires_at = int(request.query_params.get("exp", ""))
    except ValueError:
        # A missing or malformed exp is refused as an expired ticket.
        expires_at = 0
    try:
        image_store.verify_upload_ticket(
            request.path_params["image_id"],
            expires_at,
            request.query_params.get("sig", ""),
        )
    except MealPhotoUploadError as exc:
        raise _api_error(exc) from exc


def _api_error(exc: MealPhotoUploadError) -> MealTrackException:
    if exc.error_code == "INVALID_UPLOAD_TICKET":
        return AuthenticationException(message=exc.message, error_code=exc.error_code)
    if exc.error_code == "IMAGE_UPLOAD_UNAVAILABLE":
        return ExternalServiceException(message=exc.message, error_code=exc.error_code)
    return ValidationException(message=exc.message, error_code=exc.error_code)
