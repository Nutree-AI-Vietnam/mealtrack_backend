"""Next-meal cards from one chat structured recipe call."""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from src.app.services.discovery_meal_images import attach_food_images
from src.domain.model.chat import ChatMessage, ChatUserContext
from src.domain.ports.chat_discover_port import ChatDiscoverBatch, ChatDiscoverPort
from src.domain.ports.chat_next_meal_recipe_port import ChatNextMealRecipePort
from src.domain.services.chat.meal_slot import resolve_meal_slot

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


class MealMacroLookup(Protocol):
    async def calculate_meal_macros(self, ingredients: list[dict[str, Any]]) -> Any: ...


@dataclass(frozen=True, slots=True)
class NextMealCandidateResult:
    suggestions: list[dict[str, Any]] = field(default_factory=list)
    session_id: str | None = None
    meal_slot: str = "snack"


class ChatNextMealCandidates:
    """5/min. Recipes from chat structured output; calories from nutrition lookup."""

    def __init__(
        self,
        recipes: ChatNextMealRecipePort,
        nutrition_lookup: MealMacroLookup,
        *,
        model: str,
        max_per_minute: int = DISCOVER_MAX_PER_MINUTE,
    ) -> None:
        self._recipes = recipes
        self._nutrition_lookup = nutrition_lookup
        self._model = model
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
        del session_id
        slot = resolve_meal_slot(context.suggested_meal_slot, user_text)
        if not self._allow(user_id):
            logger.info(
                "chat next-meal recipes skipped: 5/min",
                extra={"user_id": user_id, "meal_slot": slot},
            )
            return NextMealCandidateResult(meal_slot=slot)
        try:
            meals = await self._recipes.generate_next_meal_recipes(
                model=self._model,
                locale=locale,
                slot=slot,
                user_message=user_text,
                remaining_calories=context.remaining_calories,
                remaining_protein_g=context.remaining_protein_g,
                remaining_carbs_g=context.remaining_carbs_g,
                remaining_fat_g=context.remaining_fat_g,
                allergies=list(context.allergies or []),
                dietary_preferences=list(context.dietary_preferences or []),
            )
        except Exception:
            logger.warning(
                "chat next-meal recipes failed",
                extra={"user_id": user_id, "meal_slot": slot},
                exc_info=True,
            )
            return NextMealCandidateResult(meal_slot=slot)
        suggestions = await map_chat_recipe_meals(
            meals,
            slot,
            self._nutrition_lookup,
        )
        return NextMealCandidateResult(suggestions=suggestions, meal_slot=slot)

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


async def map_chat_recipe_meals(
    meals: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    meal_type: str,
    nutrition_lookup: MealMacroLookup,
) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    for meal in meals:
        name = str(meal.get("name") or "").strip()
        ingredients = _recipe_ingredients(meal.get("ingredients"))
        steps = _recipe_steps(meal.get("recipe_steps"))
        if not name or not ingredients or not steps:
            continue
        try:
            macros = await nutrition_lookup.calculate_meal_macros(ingredients)
        except Exception:
            logger.warning(
                "chat next-meal nutrition lookup failed",
                extra={"meal_name": name},
                exc_info=True,
            )
            continue
        calories = _number_or_none(getattr(macros, "calories", None))
        if calories is None or calories <= 0:
            logger.warning(
                "chat next-meal zero calories dropped",
                extra={
                    "meal_name": name,
                    "ingredient_names": [item["name"] for item in ingredients],
                },
            )
            continue
        card: dict[str, Any] = {
            "id": f"chat_{uuid.uuid4().hex[:12]}",
            "name": name,
            "meal_type": meal_type,
            "calories": calories,
            "protein_g": _number_or_none(getattr(macros, "protein", None)),
            "carbs_g": _number_or_none(getattr(macros, "carbs", None)),
            "fat_g": _number_or_none(getattr(macros, "fat", None)),
            "ingredients": _ingredients_with_calories(ingredients, macros),
            "recipe_steps": steps,
        }
        english_name = str(meal.get("english_name") or "").strip()
        if english_name:
            card["english_name"] = english_name
        emoji = str(meal.get("emoji") or "").strip()
        if emoji:
            card["emoji"] = emoji
        prep = _number_or_none(meal.get("prep_time_minutes"))
        if prep is not None:
            card["prep_time_minutes"] = int(round(float(prep)))
        cards.append(card)
        if len(cards) >= DISCOVER_COUNT:
            break
    return cards


def _ingredients_with_calories(
    ingredients: list[dict[str, Any]], macros: Any
) -> list[dict[str, Any]]:
    breakdown = getattr(macros, "ingredients", None) or []
    if len(breakdown) != len(ingredients):
        return ingredients
    enriched: list[dict[str, Any]] = []
    for ing, ing_macro in zip(ingredients, breakdown, strict=False):
        item = dict(ing)
        cal = _number_or_none(getattr(ing_macro, "calories", None))
        if cal is not None and cal > 0:
            item["calories"] = cal
        enriched.append(item)
    return enriched


def _recipe_ingredients(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    items: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        amount = _number_or_none(item.get("amount"))
        unit = str(item.get("unit") or "").strip()
        if not name or amount is None or amount <= 0 or not unit:
            continue
        items.append({"name": name, "amount": amount, "unit": unit})
    return items


def _recipe_steps(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    steps: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        instruction = str(item.get("instruction") or "").strip()
        step = _number_or_none(item.get("step"))
        if not instruction or step is None:
            continue
        mapped: dict[str, Any] = {
            "step": int(step),
            "instruction": instruction,
        }
        duration = _number_or_none(item.get("duration_minutes"))
        if duration is not None:
            mapped["duration_minutes"] = int(duration)
        steps.append(mapped)
    return steps


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
