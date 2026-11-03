"""Store, swap and remove the photo of a catalog supplement.

A new photo is stored before the row points at it, and an old one is deleted
only after the change commits. When a write fails, the new photo is deleted
only once the row is known not to point at it: an image left in storage is
better than a row pointing at a missing one. Photo URLs are public, so
uploads are re-encoded the way avatars are: upright pixels only, at most
1024 px, as a JPEG with no metadata.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, replace

from src.domain.model.supplement import SupplementProductNotFoundError
from src.domain.model.supplement.catalog_review import require_photo_target
from src.domain.ports.image_store_port import ImageStorePort
from src.domain.ports.supplement_catalog_port import ReviewItem, SupplementCatalogPort
from src.domain.utils.avatar_image import (
    AVATAR_IMAGE_CONTENT_TYPE,
    normalize_avatar_image,
)

logger = logging.getLogger(__name__)

PHOTO_DELETE_ATTEMPTS = 3
PHOTO_DELETE_RETRY_SECONDS = 0.25


class SupplementPhotoStorageError(RuntimeError):
    """The image store did not take the photo."""


@dataclass(frozen=True)
class StoredPhoto:
    image_id: str
    image_url: str


async def check_photo_target(catalog: SupplementCatalogPort, product_id: str) -> None:
    """Refuse a missing product or one outside the catalog before any upload."""
    found = await catalog.review_item(product_id)
    if found is None:
        raise SupplementProductNotFoundError(product_id)
    require_photo_target(found.product)


async def store_photo(image_store: ImageStorePort, data: bytes) -> StoredPhoto:
    """Re-encode and store an upload under a new id.

    Raises ``AvatarImageRejectedError`` for a file that is not a readable
    JPEG, PNG or WebP, or has too many pixels.
    """
    jpeg = await asyncio.to_thread(normalize_avatar_image, data)
    image_id = str(uuid.uuid4())
    try:
        image_url = await image_store.save_async(
            jpeg, AVATAR_IMAGE_CONTENT_TYPE, image_id
        )
    except Exception as error:
        logger.warning(
            "Supplement photo could not be stored: error_type=%s",
            type(error).__name__,
        )
        # The store may have kept it before failing (a timeout on the reply).
        await delete_photo(image_store, image_id, reason="store failed")
        raise SupplementPhotoStorageError("the photo could not be saved") from error
    return StoredPhoto(image_id, image_url)


async def point_at_photo(
    catalog: SupplementCatalogPort, product_id: str, photo: StoredPhoto | None
) -> tuple[ReviewItem, str | None]:
    """Point a catalog product at ``photo`` (``None`` removes it).

    Runs in the caller's transaction; returns the product as changed and the
    image id it pointed at before, for the caller to delete after commit.
    Removing works on any product, so a row rejected after it took a photo
    can still give it up.
    """
    found = await catalog.review_item(product_id)
    if found is None:
        raise SupplementProductNotFoundError(product_id)
    if photo is not None:
        require_photo_target(found.product)
    previous = found.product.image_id
    image_id = photo.image_id if photo else None
    image_url = photo.image_url if photo else None
    if previous != image_id:
        await catalog.set_image(product_id, image_id, image_url)
    product = replace(found.product, image_id=image_id, image_url=image_url)
    return replace(found, product=product), previous


async def delete_photo(
    image_store: ImageStorePort,
    image_id: str,
    *,
    reason: str,
    retry_seconds: float = PHOTO_DELETE_RETRY_SECONDS,
) -> bool:
    """Delete a photo no product points at, trying up to three times.

    A delete that keeps failing is logged with the id for removal by hand.
    """
    failure = "not tried"
    for attempt in range(1, PHOTO_DELETE_ATTEMPTS + 1):
        try:
            if await image_store.delete_async(image_id):
                return True
            failure = "store reported failure"
        except Exception as error:
            failure = type(error).__name__
        if attempt < PHOTO_DELETE_ATTEMPTS and retry_seconds > 0:
            await asyncio.sleep(retry_seconds * attempt)
    log_left_in_storage(image_id, reason=reason, error=failure)
    return False


def log_left_in_storage(image_id: str, *, reason: str, error: str) -> None:
    logger.error(
        "Supplement photo left in storage; remove it by hand: "
        "image_id=%s reason=%s error=%s",
        image_id,
        reason,
        error,
    )
