"""Next-meal cards from Discover, with a deterministic allergy gate."""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from src.app.services.discovery_meal_images import attach_food_images
from src.domain.model.chat import ChatMessage, ChatUserContext
from src.domain.ports.chat_discover_port import ChatDiscoverBatch, ChatDiscoverPort
from src.domain.services.chat.meal_slot import resolve_meal_slot
from src.domain.services.chat.next_meal_targets import next_meal_discover_targets
from src.domain.services.chat.policy import filter_meals_for_allergies

_OPTIONAL_CARD_STRINGS = (
    "english_name",
    "emoji",
    "thumbnail_url",
    "image_url",
    "image_source",
    "photographer",
    "photographer_url",
    "unsplash_download_location",
)

logger = logging.getLogger(__name__)

DISCOVER_COUNT = 3
DISCOVER_MAX_PER_MINUTE = 5


@dataclass(frozen=True, slots=True)
class NextMealCandidateResult:
    suggestions: list[dict[str, Any]] = field(default_factory=list)
    session_id: str | None = None
    meal_slot: str = "snack"


class ChatNextMealCandidates:
    """5/min. Cards come from Discover; Luna only writes the surrounding prose."""

    def __init__(
        self,
        discover: ChatDiscoverPort,
        *,
        max_per_minute: int = DISCOVER_MAX_PER_MINUTE,
    ) -> None:
        self._discover = discover
        self._max_per_minute = max_per_minute
        self._hits: dict[str, list[float]] = {}

    async def fetch(
        self,
        *,
        user_id: str,
        context: ChatUserContext,
        user_text: str,
        locale: str,
        session_id: str | None,
    ) -> NextMealCandidateResult:
        slot = resolve_meal_slot(context.suggested_meal_slot, user_text)
        if not self._allow(user_id):
            logger.info(
                "chat next-meal discover skipped: 5/min",
                extra={"user_id": user_id, "meal_slot": slot},
            )
            return NextMealCandidateResult(meal_slot=slot)
        targets = next_meal_discover_targets(
            meal_slot=slot,
            remaining_calories=context.remaining_calories,
            remaining_protein_g=context.remaining_protein_g,
            remaining_carbs_g=context.remaining_carbs_g,
            remaining_fat_g=context.remaining_fat_g,
            daily_target_calories=context.target_calories,
        )
        try:
            batch = await self._discover.discover_meals(
                user_id=user_id,
                meal_type=slot,
                meal_portion_type="snack" if slot == "snack" else "main",
                language=locale,
                calorie_target=targets.calorie_target,
                protein_target=targets.protein_target,
                carbs_target=targets.carbs_target,
                fat_target=targets.fat_target,
                session_id=session_id,
                count=DISCOVER_COUNT,
            )
        except Exception:
            logger.warning(
                "chat next-meal discover failed",
                extra={"user_id": user_id, "meal_slot": slot},
                exc_info=True,
            )
            return NextMealCandidateResult(meal_slot=slot)
        safe = filter_meals_for_allergies(
            list(batch.meals),
            context.allergies or [],
        )
        suggestions = map_discover_meals(safe, slot)
        return NextMealCandidateResult(
            suggestions=suggestions,
            session_id=batch.session_id,
            meal_slot=slot,
        )

    def _allow(self, user_id: str) -> bool:
        now = time.monotonic()
        window = self._hits.setdefault(user_id, [])
        window[:] = [stamp for stamp in window if now - stamp < 60]
        if len(window) >= self._max_per_minute:
            return False
        window.append(now)
        return True


def last_discover_session_id(messages: list[ChatMessage]) -> str | None:
    for message in reversed(messages):
        session_id = message.discover_session_id()
        if session_id:
            return session_id
    return None


def map_discover_meals(
    meals: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    meal_type: str,
) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    for meal in meals:
        name = str(meal.get("name") or "").strip()
        calories = _number_or_none(meal.get("calories"))
        if not name or calories is None:
            continue
        card: dict[str, Any] = {
            "id": meal.get("id"),
            "name": name,
            "meal_type": meal_type,
            "calories": calories,
            "protein_g": _number_or_none(meal.get("protein_g", meal.get("protein"))),
            "carbs_g": _number_or_none(meal.get("carbs_g", meal.get("carbs"))),
            "fat_g": _number_or_none(meal.get("fat_g", meal.get("fat"))),
        }
        english_name = str(meal.get("english_name") or "").strip()
        if english_name:
            card["english_name"] = english_name
        confidence = _number_or_none(meal.get("image_confidence"))
        if confidence is not None:
            card["image_confidence"] = confidence
        for key in _OPTIONAL_CARD_STRINGS:
            if key == "english_name":
                continue
            value = meal.get(key)
            if isinstance(value, str) and value.strip():
                card[key] = value.strip()
        cards.append(card)
        if len(cards) >= DISCOVER_COUNT:
            break
    return cards


def _number_or_none(value: Any) -> float | int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    if number.is_integer():
        return int(number)
    return number


class SuggestionChatDiscoverAdapter(ChatDiscoverPort):
    """Discover meals, then attach the same food photos Discover uses."""

    def __init__(
        self,
        service: Any,
        image_search: Callable[[str], Awaitable[Any]] | None = None,
    ) -> None:
        self._service = service
        self._image_search = image_search

    async def discover_meals(
        self,
        *,
        user_id: str,
        meal_type: str,
        meal_portion_type: str,
        language: str,
        calorie_target: int | None,
        protein_target: float | None,
        carbs_target: float | None,
        fat_target: float | None,
        session_id: str | None,
        count: int,
    ) -> ChatDiscoverBatch:
        session, meals = await self._service.generate_discovery(
            user_id=user_id,
            meal_type=meal_type,
            meal_portion_type=meal_portion_type,
            ingredients=[],
            session_id=session_id,
            language=language,
            calorie_target_override=calorie_target,
            protein_target=protein_target,
            carbs_target=carbs_target,
            fat_target=fat_target,
            count=count,
        )
        enriched = await attach_food_images(tuple(meals or ()), self._image_search)
        return ChatDiscoverBatch(
            session_id=getattr(session, "id", None),
            meals=tuple(enriched),
        )
