"""Attach stock food photos to lightweight discover meals."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

logger = logging.getLogger(__name__)

IMAGE_FETCH_TIMEOUT_SECONDS = 3.0
# Workers AI image generation is slower than a stock-photo search. The coach
# text is already on screen; this only bounds the later photo update.
IMAGE_GENERATE_TIMEOUT_SECONDS = 40.0


async def attach_food_images(
    meals: Sequence[dict[str, Any]],
    search_fn: Callable[[str], Awaitable[Any]] | None,
    *,
    timeout: float = IMAGE_FETCH_TIMEOUT_SECONDS,
    generate_fn: Callable[[dict[str, Any]], Awaitable[Any]] | None = None,
    generate_timeout: float = IMAGE_GENERATE_TIMEOUT_SECONDS,
) -> list[dict[str, Any]]:
    """Copy meals and add image fields. A miss leaves that meal unchanged."""
    copied = [dict(meal) for meal in meals]
    if not copied or (search_fn is None and generate_fn is None):
        return copied

    async def safe_fetch(name: str) -> Any:
        if search_fn is None or not name:
            return None
        try:
            return await asyncio.wait_for(search_fn(name), timeout=timeout)
        except Exception:
            logger.warning(
                "chat discover image fetch failed", extra={"food_name": name}
            )
            return None

    async def resolve(meal: dict[str, Any]) -> Any:
        if _has_image(meal):
            return None
        name = str(meal.get("english_name") or meal.get("name") or "").strip()
        if generate_fn is not None:
            try:
                generated = await asyncio.wait_for(
                    generate_fn(meal), timeout=generate_timeout
                )
            except Exception:
                logger.warning(
                    "chat meal image generation failed",
                    extra={"food_name": name},
                )
                generated = None
            if generated is not None:
                return generated
        return await safe_fetch(name)

    images = await asyncio.gather(*(resolve(meal) for meal in copied))
    for meal, image in zip(copied, images, strict=True):
        meal.update(_image_fields(image))
    return copied


def _has_image(meal: dict[str, Any]) -> bool:
    image_url = meal.get("image_url")
    return isinstance(image_url, str) and bool(image_url.strip())


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
