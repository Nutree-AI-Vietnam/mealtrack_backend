"""Handler for making an uploaded photo the avatar."""

import asyncio
import logging
import uuid
from collections.abc import Callable
from typing import Any

from src.api.exceptions import ExternalServiceException, ValidationException
from src.app.commands.user.upload_user_avatar_photo_command import (
    UploadUserAvatarPhotoCommand,
)
from src.app.events.base import EventHandler, handles
from src.app.services.user_avatar_writes import delete_avatar_image, store_user_avatar
from src.domain.model.user.user_avatar import UserAvatar
from src.domain.ports.async_unit_of_work_port import AsyncUnitOfWorkPort
from src.domain.ports.image_store_port import ImageStorePort
from src.domain.utils.avatar_image import (
    AVATAR_IMAGE_CONTENT_TYPE,
    AvatarImageRejectedError,
    AvatarImageTooLargeError,
    normalize_avatar_image,
)

logger = logging.getLogger(__name__)


@handles(UploadUserAvatarPhotoCommand)
class UploadUserAvatarPhotoCommandHandler(
    EventHandler[UploadUserAvatarPhotoCommand, dict[str, Any]]
):
    """Re-encode, store under a new id, point the avatar at it, then clean up.

    The previous photo is deleted after commit. If the row update fails, the
    new image is deleted instead, so no stored image is left without an owner.
    """

    def __init__(
        self,
        uow_factory: Callable[[], AsyncUnitOfWorkPort],
        image_store: ImageStorePort,
    ):
        self.uow_factory = uow_factory
        self.image_store = image_store

    async def handle(self, command: UploadUserAvatarPhotoCommand) -> dict[str, Any]:
        try:
            jpeg = await asyncio.to_thread(normalize_avatar_image, command.image_bytes)
        except AvatarImageRejectedError as exc:
            message = (
                "This photo is too large. Use a smaller photo."
                if isinstance(exc, AvatarImageTooLargeError)
                else "Use a JPEG, PNG or WebP photo."
            )
            raise ValidationException(
                message=message, error_code=exc.error_code
            ) from exc

        image_id = str(uuid.uuid4())
        try:
            image_url = await self.image_store.save_async(
                jpeg, AVATAR_IMAGE_CONTENT_TYPE, image_id
            )
        except Exception as exc:
            logger.warning(
                "Avatar photo could not be stored: error_type=%s", type(exc).__name__
            )
            raise ExternalServiceException(
                message="The photo could not be saved. Try again.",
                error_code="AVATAR_STORAGE_UNAVAILABLE",
            ) from exc

        try:
            avatar = UserAvatar.photo(image_id, image_url)
            previous_image_id = await store_user_avatar(
                self.uow_factory, command.user_id, avatar
            )
        except Exception:
            await delete_avatar_image(
                self.image_store, image_id, reason="avatar update failed"
            )
            raise

        if previous_image_id and previous_image_id != image_id:
            await delete_avatar_image(
                self.image_store, previous_image_id, reason="avatar replaced"
            )
        logger.info("Stored avatar photo for user %s", command.user_id)
        return avatar.to_public_dict()
