"""Generate a chat meal-card photo with Cloudflare Workers AI."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from src.domain.model.meal_discovery.food_image import FoodImageResult
from src.domain.services.meal_image_cache.name_canonicalizer import slug

logger = logging.getLogger(__name__)

CHAT_MEAL_IMAGE_TIMEOUT_SECONDS = 35
# A stored photo is a HEAD and returns in well under this. A new Workers AI
# image takes much longer, so the chat stream does not wait for it.
CHAT_MEAL_PHOTO_LIVE_WAIT_SECONDS = 2.0
_IMAGE_SIZE = "1024x1024"


def build_chat_meal_image_prompt(meal: dict[str, Any]) -> str:
    """Photorealistic prompt from the English dish name and its ingredients."""
    name = _meal_name(meal)
    ingredients: list[str] = []
    for item in meal.get("ingredients") or []:
        if isinstance(item, dict):
            label = str(item.get("name") or "").strip()
        elif isinstance(item, str):
            label = item.strip()
        else:
            label = ""
        if label:
            ingredients.append(label)
        if len(ingredients) >= 8:
            break
    ingredient_sentence = (
        f" Visible ingredients: {', '.join(ingredients)}." if ingredients else ""
    )
    return (
        f"Photorealistic editorial food photo of {name}."
        f"{ingredient_sentence} Single plated serving, natural daylight, clean table, "
        "appetizing realistic texture, no people, no packaging, no text, no logo, no watermark."
    )


def chat_meal_image_id(name: str) -> str:
    """Stable Cloudflare Images id so the same dish is not generated twice."""
    return f"chat-meals/{slug(name)[:80]}"


class ChatMealImageGenerator:
    """Turn a meal card into a stored Cloudflare image URL."""

    def __init__(
        self,
        generator: Any,
        *,
        existing_image: Callable[[str], Awaitable[str | None]] | None = None,
    ) -> None:
        self._generator = generator
        self._existing_image = existing_image

    async def __call__(self, meal: dict[str, Any]) -> FoodImageResult | None:
        name = _meal_name(meal)
        if not name:
            return None
        image_id = _image_id_for(name)
        existing = await self._lookup_existing(image_id)
        if existing:
            return _result(existing)
        try:
            url = await self._generator.generate_url(
                build_chat_meal_image_prompt(meal),
                image_id=image_id,
                quality="medium",
                size=_IMAGE_SIZE,
                output_format="jpeg",
            )
        except Exception:
            existing = await self._lookup_existing(image_id)
            if existing:
                return _result(existing)
            raise
        if not isinstance(url, str) or not url.strip():
            return None
        logger.info("chat meal photo generated", extra={"meal": name})
        return _result(url.strip())

    async def _lookup_existing(self, image_id: str | None) -> str | None:
        if self._existing_image is None or not image_id:
            return None
        try:
            found = await self._existing_image(image_id)
        except Exception:
            logger.info(
                "chat meal photo cache lookup failed", extra={"image_id": image_id}
            )
            return None
        if isinstance(found, str) and found.strip():
            return found.strip()
        return None


def _meal_name(meal: dict[str, Any]) -> str:
    return str(meal.get("english_name") or meal.get("name") or "").strip()


def _image_id_for(name: str) -> str:
    try:
        return chat_meal_image_id(name)
    except ValueError:
        return f"chat-meals/{uuid.uuid4()}"


def _result(url: str) -> FoodImageResult:
    return FoodImageResult(
        url=url,
        thumbnail_url=url,
        source="cloudflare",
        confidence=1.0,
    )
