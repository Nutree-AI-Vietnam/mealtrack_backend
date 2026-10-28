"""Write a user's avatar, and delete stored images no avatar points to.

Images are deleted only after the row change commits, so a failed delete never
undoes a saved avatar or an account deletion. An image left behind stays public
at an unguessable URL, so a delete that keeps failing is logged with its id for
removal by hand.
"""

import asyncio
import logging
from collections.abc import Callable

from src.api.exceptions import ResourceNotFoundException
from src.domain.exceptions.user_exceptions import UserNotFoundError
from src.domain.model.user.user_avatar import UserAvatar
from src.domain.ports.async_unit_of_work_port import AsyncUnitOfWorkPort
from src.domain.ports.image_store_port import ImageStorePort

logger = logging.getLogger(__name__)

AVATAR_IMAGE_DELETE_ATTEMPTS = 3
AVATAR_IMAGE_DELETE_RETRY_SECONDS = 0.25


async def store_user_avatar(
    uow_factory: Callable[[], AsyncUnitOfWorkPort],
    user_id: str,
    avatar: UserAvatar,
) -> str | None:
    """Commit the avatar and return the image id the previous one stored."""
    async with uow_factory() as uow:
        try:
            previous_image_id = await uow.users.update_user_avatar(user_id, avatar)
        except UserNotFoundError as exc:
            raise ResourceNotFoundException(
                message="User not found", error_code="USER_NOT_FOUND"
            ) from exc
        await uow.commit()
    return previous_image_id


async def delete_avatar_image(
    image_store: ImageStorePort | None,
    image_id: str,
    *,
    reason: str,
    retry_seconds: float = AVATAR_IMAGE_DELETE_RETRY_SECONDS,
) -> bool:
    """Delete one stored avatar image, trying up to three times.

    Returns True once the store reports it gone (a missing image counts).
    `reason` says why the image is unused, for the manual-removal log.
    """
    if image_store is None:
        logger.error(
            "Avatar image left in storage, no image store configured: "
            "image_id=%s reason=%s",
            image_id,
            reason,
        )
        return False

    for attempt in range(1, AVATAR_IMAGE_DELETE_ATTEMPTS + 1):
        try:
            if await image_store.delete_async(image_id):
                return True
            failure = "store reported failure"
        except Exception as exc:
            failure = type(exc).__name__
        logger.warning(
            "Avatar image delete attempt %d/%d failed: image_id=%s error=%s",
            attempt,
            AVATAR_IMAGE_DELETE_ATTEMPTS,
            image_id,
            failure,
        )
        if attempt < AVATAR_IMAGE_DELETE_ATTEMPTS and retry_seconds > 0:
            await asyncio.sleep(retry_seconds * attempt)

    logger.error(
        "Avatar image could not be deleted; remove it by hand: image_id=%s reason=%s",
        image_id,
        reason,
    )
    return False
