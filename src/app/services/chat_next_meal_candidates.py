"""Adapt meal-suggestions discover into chat next-meal cards."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from src.domain.model.chat import ChatMessage, ChatUserContext
from src.domain.ports.chat_discover_port import ChatDiscoverBatch, ChatDiscoverPort
from src.domain.services.chat.meal_slot import (
    meal_portion_type_for_slot,
    resolve_meal_slot,
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
    """5/min and card mapping. Waits for discover; does not invent calorie numbers."""

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
                "chat discover skipped: 5/min",
                extra={"user_id": user_id, "meal_slot": slot},
            )
            return NextMealCandidateResult(session_id=session_id, meal_slot=slot)
        try:
            batch = await self._discover.discover_meals(
                user_id=user_id,
                meal_type=slot,
                meal_portion_type=meal_portion_type_for_slot(slot),
                language=locale,
                calorie_target=_int_or_none(context.remaining_calories),
                protein_target=context.remaining_protein_g,
                carbs_target=context.remaining_carbs_g,
                fat_target=context.remaining_fat_g,
                session_id=session_id,
                count=DISCOVER_COUNT,
            )
        except Exception:
            logger.warning(
                "chat discover failed",
                extra={"user_id": user_id, "meal_slot": slot},
                exc_info=True,
            )
            return NextMealCandidateResult(session_id=session_id, meal_slot=slot)
        return NextMealCandidateResult(
            suggestions=map_discover_meals(batch.meals, slot),
            session_id=batch.session_id or session_id,
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
        emoji = meal.get("emoji")
        if isinstance(emoji, str) and emoji.strip():
            card["emoji"] = emoji.strip()
        cards.append(card)
        if len(cards) >= DISCOVER_COUNT:
            break
    return cards


def _int_or_none(value: float | int | None) -> int | None:
    number = _number_or_none(value)
    if number is None:
        return None
    return int(round(number))


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
    """Thin wrapper around SuggestionOrchestrationService.generate_discovery."""

    def __init__(self, service: Any) -> None:
        self._service = service

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
        return ChatDiscoverBatch(
            session_id=getattr(session, "id", None),
            meals=tuple(meals or ()),
        )
