"""Copy a recipe's third-party image into the app's image store.

Video thumbnails (TikTok, Instagram) are signed URLs that expire within
days, so a saved recipe keeps its own copy. Failure is never fatal: the
recipe is saved with the original link or without an image.
"""

from __future__ import annotations

import asyncio
import io
import logging
from urllib.parse import urlsplit

from PIL import Image, ImageOps

from src.domain.exceptions.recipe_import_exceptions import RecipeImportError
from src.domain.ports.image_store_port import ImageStorePort
from src.infra.adapters.recipe_import.safe_fetch import SafeHttpFetcher

logger = logging.getLogger(__name__)

MAX_IMAGE_BYTES = 8 * 1024 * 1024
_MAX_DIMENSION = 1600
_JPEG_QUALITY = 85


class RecipeImageArchive:
    """``RecipeImageArchivePort`` over the safe fetcher and the image store."""

    def __init__(
        self,
        *,
        fetcher: SafeHttpFetcher,
        image_store: ImageStorePort,
        own_hosts: frozenset[str],
    ) -> None:
        self._fetcher = fetcher
        self._store = image_store
        self._own_hosts = own_hosts

    async def archive(self, image_url: str) -> str | None:
        host = (urlsplit(image_url).hostname or "").lower()
        if host in self._own_hosts:
            return image_url
        try:
            resource = await self._fetcher.get(
                image_url,
                max_bytes=MAX_IMAGE_BYTES,
                accept="image/avif,image/webp,image/jpeg,image/png,image/*;q=0.8",
                allowed_types=("image/",),
            )
            jpeg = await asyncio.to_thread(to_jpeg, resource.body)
            return await self._store.save_async(jpeg, "image/jpeg")
        except (
            RecipeImportError,
            OSError,
            ValueError,
            Image.DecompressionBombError,
        ) as exc:
            logger.info("Recipe image not archived: error=%s", type(exc).__name__)
            return None


def to_jpeg(data: bytes) -> bytes:
    """Upright RGB JPEG, at most 1600 px on the long edge; raises for non-images."""
    with Image.open(io.BytesIO(data)) as opened:
        opened.load()
        image = ImageOps.exif_transpose(opened) or opened
        if max(image.size) > _MAX_DIMENSION:
            image.thumbnail((_MAX_DIMENSION, _MAX_DIMENSION))
        rgb = image.convert("RGB")
    buffer = io.BytesIO()
    rgb.save(buffer, format="JPEG", quality=_JPEG_QUALITY)
    return buffer.getvalue()
