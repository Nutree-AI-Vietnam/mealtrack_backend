"""Avatar endpoints under /v1/users: choose a character or colour, or upload a photo."""

import logging
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status

from src.api.dependencies.auth import get_current_user_id
from src.api.dependencies.event_bus import get_configured_event_bus
from src.api.exceptions import ValidationException
from src.api.middleware.rate_limit import limiter
from src.api.schemas.request.user_requests import UpdateAvatarRequest
from src.api.schemas.response.user_responses import AvatarResponse
from src.app.commands.user import UpdateUserAvatarCommand, UploadUserAvatarPhotoCommand
from src.domain.model.user.user_avatar import (
    AVATAR_PHOTO_CONTENT_TYPES,
    AVATAR_PHOTO_MAX_BYTES,
)
from src.domain.utils.avatar_image import (
    AvatarImageTooLargeError,
    UnsupportedAvatarImageError,
)

logger = logging.getLogger(__name__)
router = APIRouter()

_CONTENT_TYPE_ALIASES = {"image/jpg": "image/jpeg"}
_REJECTION_STATUS = {
    UnsupportedAvatarImageError.error_code: status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
    AvatarImageTooLargeError.error_code: status.HTTP_413_CONTENT_TOO_LARGE,
}
_UNSUPPORTED_MESSAGE = "Use a JPEG, PNG or WebP photo."


def _rejection(error_code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=_REJECTION_STATUS[error_code],
        detail={"error_code": error_code, "message": message, "details": {}},
    )


def _content_type(file: UploadFile) -> str:
    raw = (file.content_type or "").split(";", 1)[0].strip().lower()
    return _CONTENT_TYPE_ALIASES.get(raw, raw)


@router.patch("/avatar", response_model=AvatarResponse)
async def update_avatar(
    request: UpdateAvatarRequest,
    user_id: str = Depends(get_current_user_id),
    event_bus: Any = Depends(get_configured_event_bus),
):
    """
    Choose auto, a Nutree character, or an initials colour.

    - **kind**: `auto`, `character` or `initials`
    - **character**: character id, only with `character`
    - **color**: palette index 0-7, only with `initials`

    A previously uploaded photo is deleted once the change is saved.
    """
    command = UpdateUserAvatarCommand(
        user_id=user_id,
        kind=request.kind,
        character=request.character,
        color=request.color,
    )
    result = await event_bus.send(command)
    return AvatarResponse(**result)


@router.post("/avatar/photo", response_model=AvatarResponse)
@limiter.limit("10/hour")
async def upload_avatar_photo(
    request: Request,
    file: UploadFile = File(...),
    user_id: str = Depends(get_current_user_id),
    event_bus: Any = Depends(get_configured_event_bus),
):
    """
    Upload a photo as the avatar (multipart field `file`).

    JPEG, PNG or WebP up to 5 MB. The server re-encodes it as a JPEG of at
    most 1024 px with no EXIF, GPS or comment. Returns 415 for another type
    and 413 for a file or image that is too large.
    """
    if _content_type(file) not in AVATAR_PHOTO_CONTENT_TYPES:
        raise _rejection(UnsupportedAvatarImageError.error_code, _UNSUPPORTED_MESSAGE)

    contents = await file.read(AVATAR_PHOTO_MAX_BYTES + 1)
    if len(contents) > AVATAR_PHOTO_MAX_BYTES:
        raise _rejection(
            AvatarImageTooLargeError.error_code,
            f"The photo must be {AVATAR_PHOTO_MAX_BYTES // (1024 * 1024)} MB or smaller.",
        )

    command = UploadUserAvatarPhotoCommand(user_id=user_id, image_bytes=contents)
    try:
        result = await event_bus.send(command)
    except ValidationException as exc:
        if exc.error_code not in _REJECTION_STATUS:
            raise
        raise _rejection(exc.error_code, exc.message) from exc
    return AvatarResponse(**result)
