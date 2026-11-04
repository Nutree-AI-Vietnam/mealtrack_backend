"""Admin endpoints for a catalog supplement's photo."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from src.api.base_dependencies import get_image_store
from src.api.dependencies.auth import require_admin_or_local
from src.api.routes.v1.admin_supplements import catalog_errors, review_item_response
from src.api.schemas.response.admin_supplement_responses import (
    AdminSupplementReviewItem,
)
from src.app.services.supplement_product_photo import SupplementPhotoStorageError
from src.bootstrap.supplement_catalog import (
    remove_supplement_photo,
    set_supplement_photo,
)
from src.domain.model.user.user_avatar import (
    AVATAR_PHOTO_CONTENT_TYPES,
    AVATAR_PHOTO_MAX_BYTES,
)
from src.domain.ports.image_store_port import ImageStorePort
from src.domain.utils.avatar_image import (
    AvatarImageRejectedError,
    AvatarImageTooLargeError,
)

router = APIRouter(prefix="/v1/admin/supplements", tags=["Admin Supplements"])

_ALIASES = {"image/jpg": "image/jpeg"}


@router.post("/{product_id}/image", response_model=AdminSupplementReviewItem)
async def upload_admin_supplement_image(
    product_id: str,
    file: UploadFile = File(...),
    _admin: str = Depends(require_admin_or_local),
    image_store: ImageStorePort = Depends(get_image_store),
) -> AdminSupplementReviewItem:
    """Set a catalog product's photo (multipart field ``file``).

    JPEG, PNG or WebP up to 5 MB, stored as a JPEG of at most 1024 px with no
    metadata; the old photo is deleted. A scan takes a photo once approved.
    Returns 415 for another type and 413 for a file or image that is too large.
    """
    content_type = (file.content_type or "").split(";", 1)[0].strip().lower()
    if _ALIASES.get(content_type, content_type) not in AVATAR_PHOTO_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Use a JPEG, PNG or WebP photo",
        )
    contents = await file.read(AVATAR_PHOTO_MAX_BYTES + 1)
    if len(contents) > AVATAR_PHOTO_MAX_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="The photo must be 5 MB or smaller",
        )
    try:
        with catalog_errors():
            item = await set_supplement_photo(product_id, contents, image_store)
    except AvatarImageRejectedError as error:
        too_large = isinstance(error, AvatarImageTooLargeError)
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE
            if too_large
            else status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=str(error),
        ) from error
    except SupplementPhotoStorageError as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(error)
        ) from error
    return review_item_response(item)


@router.delete("/{product_id}/image", response_model=AdminSupplementReviewItem)
async def delete_admin_supplement_image(
    product_id: str,
    _admin: str = Depends(require_admin_or_local),
    image_store: ImageStorePort = Depends(get_image_store),
) -> AdminSupplementReviewItem:
    """Take the photo off a catalog product; the app shows the plain mark again."""
    with catalog_errors():
        item = await remove_supplement_photo(product_id, image_store)
    return review_item_response(item)
