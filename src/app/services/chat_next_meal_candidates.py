"""Next-meal cards from Discover, with a deterministic allergy gate."""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from src.app.services.discovery_meal_images import attach_food_images
from src.domain.model.chat import ChatMessage, ChatUserContext
from src.domain.model.nutrition.micros import Micros
from src.domain.ports.chat_discover_port import ChatDiscoverBatch, ChatDiscoverPort
from src.domain.ports.chat_next_meal_recipe_port import ChatNextMealRecipePort
from src.domain.services.chat.meal_slot import resolve_meal_slot
from src.domain.services.chat.next_meal_targets import next_meal_discover_targets
from src.domain.services.chat.policy import filter_meals_for_allergies
from src.domain.services.nrf_score import nrf_coverage, nrf_quality

_OPTIONAL_CARD_STRINGS = (
    "english_name",
    "emoji",
    "thumbnail_url",
    "image_url",
    "image_source",
    "photographer",
    "photographer_url",
    "unsplash_download_location",
    "prep_time_minutes",
)


logger = logging.getLogger(__name__)

DISCOVER_COUNT = 1
DISCOVER_MAX_PER_MINUTE = 5


@dataclass(frozen=True, slots=True)
class NextMealCandidateResult:
    suggestions: list[dict[str, Any]] = field(default_factory=list)
    session_id: str | None = None
    meal_slot: str = "snack"


class ChatNextMealCandidates:
    """5/min. Cards come from Discover or direct recipe generator with photos."""

    def __init__(
        self,
        discover: ChatDiscoverPort | None = None,
        *,
        recipe_generator: ChatNextMealRecipePort | None = None,
        image_search: Callable[[str], Awaitable[Any]] | None = None,
        model: str = "gpt-5.6-luna",
        max_per_minute: int = DISCOVER_MAX_PER_MINUTE,
    ) -> None:
        self._discover = discover
        self._recipe_generator = recipe_generator
        self._image_search = image_search
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
        slot: str | None = None,
    ) -> NextMealCandidateResult:
        slot = slot or resolve_meal_slot(context.suggested_meal_slot, user_text)
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

        # 1. Direct AI recipe generator with food images (Primary: guarantees complete recipe & ingredients are immediately ready in chat)
        if self._recipe_generator is not None:
            try:
                recipes = await self._recipe_generator.generate_next_meal_recipes(
                    model=self._model,
                    locale=locale,
                    slot=slot,
                    user_message=user_text,
                    remaining_calories=context.remaining_calories,
                    remaining_protein_g=context.remaining_protein_g,
                    remaining_carbs_g=context.remaining_carbs_g,
                    remaining_fat_g=context.remaining_fat_g,
                    allergies=context.allergies or [],
                    dietary_preferences=context.dietary_preferences or [],
                )
                if recipes:
                    safe = filter_meals_for_allergies(
                        recipes[:DISCOVER_COUNT],
                        context.allergies or [],
                    )
                    enriched = await attach_food_images(safe, self._image_search)
                    suggestions = map_discover_meals(enriched, slot)
                    return NextMealCandidateResult(
                        suggestions=suggestions,
                        session_id=None,
                        meal_slot=slot,
                    )
            except Exception:
                logger.warning(
                    "chat next-meal direct recipe generator failed",
                    extra={"user_id": user_id, "meal_slot": slot},
                    exc_info=True,
                )

        # 2. Try discover if configured (fallback when recipe generator is not configured or failed)
        if self._discover is not None:
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
                if batch.meals:
                    safe = filter_meals_for_allergies(
                        list(batch.meals),
                        context.allergies or [],
                    )
                    suggestions = map_discover_meals(safe[:DISCOVER_COUNT], slot)
                    return NextMealCandidateResult(
                        suggestions=suggestions,
                        session_id=batch.session_id,
                        meal_slot=slot,
                    )
                return NextMealCandidateResult(
                    suggestions=[],
                    session_id=batch.session_id,
                    meal_slot=slot,
                )
            except Exception:
                logger.warning(
                    "chat next-meal discover failed",
                    extra={"user_id": user_id, "meal_slot": slot},
                    exc_info=True,
                )

        # 3. If generator was configured but generator and discover failed, use safe fallback with full recipe
        if self._recipe_generator is not None:
            fallback_meals = _fallback_meals_for_slot(
                slot, locale, targets.calorie_target
            )
            safe_fallbacks = filter_meals_for_allergies(
                fallback_meals[:DISCOVER_COUNT], context.allergies or []
            )
            if safe_fallbacks:
                enriched = await attach_food_images(safe_fallbacks, self._image_search)
                suggestions = map_discover_meals(enriched, slot)
                return NextMealCandidateResult(
                    suggestions=suggestions,
                    session_id=None,
                    meal_slot=slot,
                )

        return NextMealCandidateResult(meal_slot=slot)

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
        fiber_g = _number_or_none(meal.get("fiber_g", meal.get("fiber")))
        if fiber_g is not None:
            card["fiber_g"] = fiber_g

        # Extract and compute micronutrient score & coverage
        raw_micros = meal.get("micros")
        micros_dict: dict[str, float] = {}
        if isinstance(raw_micros, dict):
            for k, v in raw_micros.items():
                num = _number_or_none(v)
                if num is not None and num >= 0:
                    micros_dict[k] = float(num)
        elif hasattr(raw_micros, "model_dump"):
            dumped = raw_micros.model_dump()
            for k, v in dumped.items():
                num = _number_or_none(v)
                if num is not None and num >= 0:
                    micros_dict[k] = float(num)

        micros_obj = Micros.from_dict(micros_dict) if micros_dict else None
        coverage = nrf_coverage(micros_obj)
        card["nrf_coverage"] = coverage
        if coverage >= 1 and micros_obj is not None:
            protein_val = float(card.get("protein_g") or 0.0)
            fiber_val = float(card.get("fiber_g") or 0.0)
            card["nrf_quality"] = nrf_quality(protein_val, fiber_val, micros_obj)
            card["micros"] = micros_obj.to_dict()
        else:
            card["nrf_quality"] = None
            if micros_dict:
                card["micros"] = micros_dict

        english_name = str(meal.get("english_name") or "").strip()
        if english_name:
            card["english_name"] = english_name
        confidence = _number_or_none(meal.get("image_confidence"))
        if confidence is not None:
            card["image_confidence"] = confidence
        prep_time = _number_or_none(meal.get("prep_time_minutes"))
        if prep_time is not None:
            card["prep_time_minutes"] = int(prep_time)
        for key in _OPTIONAL_CARD_STRINGS:
            if key in ("english_name", "prep_time_minutes"):
                continue

            value = meal.get(key)
            if isinstance(value, str) and value.strip():
                card[key] = value.strip()

        # Preserve complete recipe details (ingredients and recipe steps) when available
        raw_steps = meal.get("recipe_steps")
        if isinstance(raw_steps, (list, tuple)) and raw_steps:
            card["recipe_steps"] = list(raw_steps)
            raw_ings = meal.get("ingredients")
            if isinstance(raw_ings, (list, tuple)) and raw_ings:
                card["ingredients"] = list(raw_ings)

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


def _fallback_meals_for_slot(
    slot: str, locale: str, target_calories: int | None
) -> list[dict[str, Any]]:
    """Safe fallback meals with complete macros, ingredients, and recipe steps."""
    is_vi = "vi" in (locale or "").casefold()
    target_cals = target_calories or (
        350 if slot == "breakfast" else 550 if slot in ("lunch", "dinner") else 200
    )

    if slot == "breakfast":
        return [
            {
                "name": "Yến mạch nấu sữa hạnh nhân và chuối"
                if is_vi
                else "Oatmeal with Almond Milk and Banana",
                "english_name": "Oatmeal with Almond Milk and Banana",
                "emoji": "🥣",
                "calories": min(target_cals, 380),
                "protein_g": 14.0,
                "carbs_g": 62.0,
                "fat_g": 8.0,
                "fiber_g": 7.0,
                "micros": {
                    "calcium": 180.0,
                    "iron": 2.5,
                    "potassium": 450.0,
                    "magnesium": 85.0,
                    "sodium": 75.0,
                    "vitamin_c": 10.0,
                },
                "prep_time_minutes": 10,
                "ingredients": [
                    {"name": "rolled oats", "amount": 60, "unit": "g"},
                    {"name": "unsweetened almond milk", "amount": 200, "unit": "ml"},
                    {"name": "banana", "amount": 100, "unit": "g"},
                    {"name": "chia seeds", "amount": 10, "unit": "g"},
                ],
                "recipe_steps": [
                    {
                        "step": 1,
                        "instruction": "Đun sữa hạnh nhân ấm trên chảo nhỏ, cho yến mạch vào khuấy đều trong 5 phút."
                        if is_vi
                        else "Warm almond milk in a small pan, add oats and stir for 5 minutes.",
                        "duration_minutes": 5,
                    },
                    {
                        "step": 2,
                        "instruction": "Đổ ra bát, xếp chuối cắt lát và rắc hạt chia lên trên trước khi dùng."
                        if is_vi
                        else "Pour into a bowl, top with sliced banana and chia seeds.",
                        "duration_minutes": 5,
                    },
                ],
            },
            {
                "name": "Trứng ốp la bánh mì nguyên cám"
                if is_vi
                else "Sunny-side up Eggs with Whole Wheat Toast",
                "english_name": "Sunny-side up Eggs with Whole Wheat Toast",
                "emoji": "🍳",
                "calories": min(target_cals, 360),
                "protein_g": 20.0,
                "carbs_g": 35.0,
                "fat_g": 14.0,
                "fiber_g": 4.5,
                "micros": {
                    "vitamin_a": 160.0,
                    "vitamin_c": 15.0,
                    "calcium": 70.0,
                    "iron": 2.8,
                    "potassium": 320.0,
                    "sodium": 280.0,
                    "saturated_fat": 3.5,
                },
                "prep_time_minutes": 10,
                "ingredients": [
                    {"name": "eggs", "amount": 2, "unit": "piece"},
                    {"name": "whole wheat bread", "amount": 60, "unit": "g"},
                    {"name": "cherry tomatoes", "amount": 80, "unit": "g"},
                    {"name": "olive oil", "amount": 5, "unit": "ml"},
                ],
                "recipe_steps": [
                    {
                        "step": 1,
                        "instruction": "Nướng giòn bánh mì nguyên cám."
                        if is_vi
                        else "Toast the whole wheat bread.",
                        "duration_minutes": 3,
                    },
                    {
                        "step": 2,
                        "instruction": "Chiên 2 quả trứng ốp la với dầu ô liu, ăn kèm cà chua bi tươi."
                        if is_vi
                        else "Fry 2 eggs sunny-side up with olive oil, serve with cherry tomatoes.",
                        "duration_minutes": 7,
                    },
                ],
            },
        ]

    if slot in ("lunch", "dinner"):
        return [
            {
                "name": "Ức gà áp chảo sốt tiêu và gạo lứt"
                if is_vi
                else "Pan-seared Chicken Breast with Brown Rice",
                "english_name": "Pan-seared Chicken Breast with Brown Rice",
                "emoji": "🍗",
                "calories": min(target_cals, 520),
                "protein_g": 42.0,
                "carbs_g": 56.0,
                "fat_g": 12.0,
                "fiber_g": 6.0,
                "micros": {
                    "vitamin_a": 45.0,
                    "vitamin_c": 70.0,
                    "calcium": 65.0,
                    "iron": 2.2,
                    "potassium": 680.0,
                    "magnesium": 95.0,
                    "sodium": 340.0,
                    "saturated_fat": 2.0,
                },
                "prep_time_minutes": 25,
                "ingredients": [
                    {"name": "chicken breast", "amount": 180, "unit": "g"},
                    {"name": "cooked brown rice", "amount": 150, "unit": "g"},
                    {"name": "broccoli", "amount": 120, "unit": "g"},
                    {"name": "olive oil", "amount": 8, "unit": "ml"},
                    {"name": "soy sauce", "amount": 10, "unit": "ml"},
                ],
                "recipe_steps": [
                    {
                        "step": 1,
                        "instruction": "Ướp ức gà với chút tiêu và nước tương trong 10 phút."
                        if is_vi
                        else "Marinate chicken breast with pepper and soy sauce for 10 minutes.",
                        "duration_minutes": 10,
                    },
                    {
                        "step": 2,
                        "instruction": "Áp chảo ức gà mỗi mặt 6-7 phút với dầu ô liu cho chín vàng mềm."
                        if is_vi
                        else "Pan-sear chicken for 6-7 minutes each side with olive oil until golden.",
                        "duration_minutes": 12,
                    },
                    {
                        "step": 3,
                        "instruction": "Luộc chín bông cải xanh và dọn ăn kèm cơm gạo lứt ấm nóng."
                        if is_vi
                        else "Steam broccoli and serve alongside warm brown rice.",
                        "duration_minutes": 3,
                    },
                ],
            },
            {
                "name": "Cá hồi áp chảo măng tây khoai lang"
                if is_vi
                else "Seared Salmon with Asparagus and Sweet Potato",
                "english_name": "Seared Salmon with Asparagus and Sweet Potato",
                "emoji": "🐟",
                "calories": min(target_cals, 560),
                "protein_g": 38.0,
                "carbs_g": 45.0,
                "fat_g": 18.0,
                "fiber_g": 7.5,
                "micros": {
                    "vitamin_a": 720.0,
                    "vitamin_c": 35.0,
                    "vitamin_e": 3.0,
                    "calcium": 85.0,
                    "iron": 2.4,
                    "potassium": 820.0,
                    "magnesium": 65.0,
                    "sodium": 190.0,
                    "saturated_fat": 3.0,
                },
                "prep_time_minutes": 20,
                "ingredients": [
                    {"name": "salmon fillet", "amount": 160, "unit": "g"},
                    {"name": "sweet potato", "amount": 150, "unit": "g"},
                    {"name": "asparagus", "amount": 100, "unit": "g"},
                    {"name": "lemon juice", "amount": 10, "unit": "ml"},
                ],
                "recipe_steps": [
                    {
                        "step": 1,
                        "instruction": "Hấp chín hoặc nướng khoai lang cắt lát trong 15 phút."
                        if is_vi
                        else "Steam or bake sweet potato slices for 15 minutes.",
                        "duration_minutes": 15,
                    },
                    {
                        "step": 2,
                        "instruction": "Áp chảo cá hồi và măng tây với lửa vừa 4 phút mỗi mặt, vắt chanh tươi."
                        if is_vi
                        else "Pan-sear salmon and asparagus for 4 minutes each side, finish with fresh lemon juice.",
                        "duration_minutes": 5,
                    },
                ],
            },
        ]

    # Snack fallback
    return [
        {
            "name": "Sữa chua Hy Lạp mix hạnh nhân và việt quất"
            if is_vi
            else "Greek Yogurt with Almonds and Blueberries",
            "english_name": "Greek Yogurt with Almonds and Blueberries",
            "emoji": "🫐",
            "calories": min(target_cals, 220),
            "protein_g": 17.0,
            "carbs_g": 22.0,
            "fat_g": 7.0,
            "fiber_g": 3.5,
            "micros": {
                "vitamin_c": 8.0,
                "calcium": 180.0,
                "iron": 0.8,
                "potassium": 260.0,
                "magnesium": 45.0,
                "sodium": 65.0,
                "saturated_fat": 2.0,
            },
            "prep_time_minutes": 5,
            "ingredients": [
                {"name": "plain greek yogurt", "amount": 150, "unit": "g"},
                {"name": "blueberries", "amount": 60, "unit": "g"},
                {"name": "roasted almonds", "amount": 15, "unit": "g"},
            ],
            "recipe_steps": [
                {
                    "step": 1,
                    "instruction": "Cho sữa chua Hy Lạp ra bát, xếp việt quất và hạnh nhân giã dập lên trên."
                    if is_vi
                    else "Scoop Greek yogurt into a bowl, top with blueberries and crushed almonds.",
                    "duration_minutes": 5,
                },
            ],
        },
    ]
