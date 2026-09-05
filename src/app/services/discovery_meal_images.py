"""Attach stock food photos to lightweight discover meals."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

logger = logging.getLogger(__name__)

IMAGE_FETCH_TIMEOUT_SECONDS = 3.0


async def attach_food_images(
    meals: Sequence[dict[str, Any]],
    search_fn: Callable[[str], Awaitable[Any]] | None,
    *,
    timeout: float = IMAGE_FETCH_TIMEOUT_SECONDS,
) -> list[dict[str, Any]]:
    """Copy meals and add image fields. Search failures leave meals unchanged."""
    copied = [dict(meal) for meal in meals]
    if search_fn is None or not copied:
        return copied

    async def safe_fetch(name: str) -> Any:
        if not name:
            return None
        try:
            return await asyncio.wait_for(search_fn(name), timeout=timeout)
        except Exception:
            logger.warning(
                "chat discover image fetch failed", extra={"food_name": name}
            )
            return None

    names = [
        str(meal.get("english_name") or meal.get("name") or "").strip()
        for meal in copied
    ]
    images = await asyncio.gather(*[safe_fetch(name) for name in names])
    for meal, image in zip(copied, images, strict=True):
        meal.update(_image_fields(image))
    return copied


def _image_fields(image: Any) -> dict[str, Any]:
    if image is None:
        return {}
    image_url = getattr(image, "image_url", None) or getattr(image, "url", None)
    if not isinstance(image_url, str) or not image_url.strip():
        return {}
    thumbnail = getattr(image, "thumbnail_url", None) or image_url
    fields: dict[str, Any] = {
        "image_url": image_url.strip(),
        "thumbnail_url": str(thumbnail).strip() if thumbnail else image_url.strip(),
    }
    source = getattr(image, "source", None)
    if isinstance(source, str) and source.strip():
        fields["image_source"] = source.strip()
    photographer = getattr(image, "photographer", None)
    if isinstance(photographer, str) and photographer.strip():
        fields["photographer"] = photographer.strip()
    photographer_url = getattr(image, "photographer_url", None)
    if isinstance(photographer_url, str) and photographer_url.strip():
        fields["photographer_url"] = photographer_url.strip()
    download = getattr(image, "download_location", None)
    if isinstance(download, str) and download.strip():
        fields["unsplash_download_location"] = download.strip()
    confidence = getattr(image, "confidence", None)
    if isinstance(confidence, (int, float)) and not isinstance(confidence, bool):
        fields["image_confidence"] = float(confidence)
    return fields
