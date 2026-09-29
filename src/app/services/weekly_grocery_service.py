"""Read-time grocery aggregation for weekly plans."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from src.domain.model.weekly_meal_planner import WeeklyMealPlan
from src.domain.services.weekly_meal_planner.grocery_projection import (
    DerivedGroceryItem,
    GroceryIngredient,
    GroceryInteraction,
    GrocerySlot,
    PantryAvailability,
    aggregate_grocery,
)


@dataclass(frozen=True)
class GroceryItem:
    ingredient_id: int
    name: str
    category: str
    total_needed: float
    unit: str
    stock_amount: float | None
    status: str
    quantity_confidence: str
    remaining: float
    contributions: tuple[tuple[int, float], ...] = ()
    checked: bool = False
    do_not_buy: bool = False
    manually_owned: bool = False
    stock_kind: str | None = None
    daily_amounts: tuple[dict[str, int | float], ...] = ()


@dataclass(frozen=True)
class GroceryCategory:
    category: str
    items: tuple[GroceryItem, ...]


class WeeklyGroceryService:
    """Aggregate canonical catalog ingredient quantities without client math."""

    def project(self, plan: WeeklyMealPlan, meals) -> tuple[DerivedGroceryItem, ...]:
        """Project proposed-plan requirements without pantry deductions or writes."""
        meals_by_id = {meal.id: meal for meal in meals}
        slots = [
            _slot_from_meal(
                meals_by_id[slot.recipe_id],
                plan_people=plan.people,
                override=getattr(slot, "recipe_override", None),
                day_index=slot.day_index,
            )
            for slot in plan.slots
            if slot.recipe_id is not None and slot.recipe_id in meals_by_id
        ]
        return aggregate_grocery(slots, ())

    async def calculate(self, uow, plan: WeeklyMealPlan) -> tuple[GroceryCategory, ...]:
        slots: list[GrocerySlot] = []
        needed_ids = {
            slot.recipe_id for slot in plan.slots if slot.recipe_id is not None
        }
        meals_cache: dict[str, Any] = {}
        if hasattr(uow.catalog_recipes, "get_meals"):
            fetched = await uow.catalog_recipes.get_meals(needed_ids)
            meals_cache = {meal.id: meal for meal in fetched if meal is not None}
        for slot in plan.slots:
            if slot.recipe_id is None:
                continue
            if slot.recipe_id not in meals_cache:
                meals_cache[slot.recipe_id] = await uow.catalog_recipes.get_meal(
                    slot.recipe_id
                )
            meal = meals_cache[slot.recipe_id]
            if meal is None:
                continue
            slots.append(
                _slot_from_meal(
                    meal,
                    plan_people=plan.people,
                    override=getattr(slot, "recipe_override", None),
                    day_index=slot.day_index,
                )
            )
        pantry_rows = await uow.weekly_meal_plans.list_pantry(
            user_id=plan.user_id, plan_id=plan.id
        )
        interaction_loader = getattr(
            uow.weekly_meal_plans, "list_grocery_interactions", None
        )
        interaction_rows = (
            await interaction_loader(user_id=plan.user_id, plan_id=plan.id)
            if interaction_loader is not None
            else []
        )
        derived = aggregate_grocery(
            slots,
            _pantry(pantry_rows),
            _interactions(interaction_rows),
        )
        return _categories(derived)


def _resolve_grocery_category(name: str, category: str | None) -> str:
    cat = (category or "").strip().lower()
    if cat in {"produce", "fresh_produce", "vegetable", "fruit"}:
        return "fresh_produce"
    if cat in {"protein", "meat", "seafood", "poultry"}:
        return "protein"

    lower = name.lower()
    protein_keywords = (
        "chicken",
        "gà",
        "pork",
        "heo",
        "lợn",
        "beef",
        "bò",
        "fish",
        "cá",
        "trứng",
        "tofu",
        "đậu hũ",
        "đậu phụ",
        "shrimp",
        "tôm",
        "meat",
        "thịt",
        "salmon",
        "tuna",
        "turkey",
        "duck",
        "vịt",
        "bacon",
        "sausage",
        "xúc xích",
        "ham",
        "seafood",
        "hải sản",
        "chops",
        "cutlet",
        "steak",
        "meatloaf",
    )
    if re.search(r"\beggs?\b", lower) or any(k in lower for k in protein_keywords):
        return "protein"

    produce_keywords = (
        "cabbage",
        "bắp cải",
        "slaw",
        "broccoli",
        "súp lơ",
        "bông cải",
        "tomato",
        "cà chua",
        "potato",
        "khoai tây",
        "french fries",
        "fries",
        "carrot",
        "cà rốt",
        "onion",
        "hành",
        "shallot",
        "garlic",
        "tỏi",
        "ginger",
        "gừng",
        "lemongrass",
        "sả",
        "herb",
        "rau",
        "lettuce",
        "xà lách",
        "spinach",
        "cucumber",
        "dưa chuột",
        "dưa leo",
        "mushroom",
        "nấm",
        "pepper",
        "ớt",
        "bell pepper",
        "cilantro",
        "coriander",
        "ngò",
        "parsley",
        "basil",
        "húng",
        "lime",
        "chanh",
        "lemon",
        "apple",
        "táo",
        "banana",
        "chuối",
        "sprout",
        "giá đỗ",
        "celery",
        "cần tây",
        "corn",
        "ngô",
        "bắp",
        "avocado",
        "bơ sáp",
        "zucchini",
        "bí ngòi",
        "eggplant",
        "cà tím",
    )
    if any(k in lower for k in produce_keywords):
        return "fresh_produce"

    return "pantry"


_NON_FOOD_CATEGORIES = frozenset(
    {"equipment", "kitchen_equipment", "cookware", "tool", "utensil", "non_food"}
)
_NON_FOOD_TERMS = (
    "tô",
    "bát",
    "chén",
    "dụng cụ",
    "bowl",
    "equipment",
    "tool",
    "utensil",
)


def _is_non_food_ingredient(name: str, category: str | None) -> bool:
    if (category or "").strip().casefold().replace(" ", "_") in _NON_FOOD_CATEGORIES:
        return True
    normalized = " ".join(name.casefold().split())
    return any(
        re.search(rf"(?<!\w){re.escape(term)}(?!\w)", normalized)
        for term in _NON_FOOD_TERMS
    )


def _slot_from_meal(
    meal, *, plan_people: int, override, day_index: int = 0
) -> GrocerySlot:
    ingredients = [
        GroceryIngredient(
            position=int(getattr(ingredient, "position", None) or index),
            food_reference_id=ingredient.food_reference_id,
            name=ingredient.name,
            quantity=ingredient.quantity,
            unit=ingredient.unit,
            category=_resolve_grocery_category(ingredient.name, ingredient.category),
            quantity_confidence=getattr(ingredient, "quantity_confidence", "exact"),
        )
        for index, ingredient in enumerate(meal.ingredients, start=1)
        if not _is_non_food_ingredient(ingredient.name, ingredient.category)
    ]
    existing_positions = {item.position for item in ingredients}
    existing_names = {item.name.strip().casefold() for item in ingredients}
    payload = getattr(meal, "recipe_payload", None) or {}
    for raw in payload.get("ingredients") or []:
        raw_name = str(raw.get("name") or "").strip()
        if not raw_name or raw_name.casefold() in existing_names:
            continue
        raw_pos = int(raw.get("position") or 0)
        if raw_pos and raw_pos in existing_positions:
            continue
        if raw.get("food_reference_id") is not None and raw.get("quantity") not in (
            None,
            "",
        ):
            continue
        raw_category = raw.get("category") or raw.get("type") or raw.get("kind")
        if raw.get("is_equipment") or _is_non_food_ingredient(raw_name, raw_category):
            continue
        ingredients.append(
            GroceryIngredient(
                position=int(raw.get("position") or len(ingredients) + 1),
                food_reference_id=raw.get("food_reference_id"),
                name=raw_name,
                quantity=_decimal_or_none(raw.get("quantity")),
                unit=raw.get("unit"),
                category=_resolve_grocery_category(raw_name, raw_category),
                quantity_text=raw.get("quantity_text"),
            )
        )
    return GrocerySlot(
        recipe_id=meal.id,
        publication_status=getattr(meal, "publication_status", "published"),
        nutrition_status=getattr(meal, "nutrition_status", "ready"),
        is_active=getattr(meal, "is_active", True),
        base_servings=getattr(meal, "base_servings", None),
        serving_confidence=getattr(meal, "serving_confidence", "unknown"),
        people=plan_people,
        ingredients=tuple(ingredients),
        recipe_override=override,
        day_index=day_index,
    )


def _pantry(rows: list[dict]) -> tuple[PantryAvailability, ...]:
    items: list[PantryAvailability] = []
    for row in rows:
        amount = row.get("available_amount")
        items.append(
            PantryAvailability(
                food_reference_id=int(row["food_reference_id"]),
                available_amount=None if amount is None else Decimal(str(amount)),
                available_unit=row.get("available_unit"),
            )
        )
    return tuple(items)


def _interactions(rows: list[dict]) -> tuple[GroceryInteraction, ...]:
    return tuple(
        GroceryInteraction(
            food_reference_id=int(row["food_reference_id"]),
            checked=bool(row.get("checked", False)),
            do_not_buy=bool(row.get("do_not_buy", False)),
            manually_owned=bool(row.get("manually_owned", False)),
        )
        for row in rows
    )


def _categories(items: tuple[DerivedGroceryItem, ...]) -> tuple[GroceryCategory, ...]:
    grouped: dict[str, list[GroceryItem]] = defaultdict(list)
    for item in items:
        grouped[item.category].append(
            GroceryItem(
                ingredient_id=item.ingredient_id,
                name=item.name,
                category=item.category,
                total_needed=item.total_needed,
                unit=item.unit,
                stock_amount=item.available_amount,
                status=item.status,
                quantity_confidence=item.quantity_confidence,
                remaining=item.remaining,
                contributions=item.contributions,
                checked=item.checked,
                do_not_buy=item.do_not_buy,
                manually_owned=item.manually_owned,
                stock_kind=(
                    "owned"
                    if item.manually_owned
                    else "bought"
                    if item.checked
                    else None
                ),
                daily_amounts=tuple(
                    {
                        "day_index": day_index,
                        "total_needed": total_needed,
                        "remaining": daily_remaining,
                    }
                    for day_index, total_needed, daily_remaining in item.daily_amounts
                ),
            )
        )
    return tuple(
        GroceryCategory(category=category, items=tuple(grouped[category]))
        for category in sorted(grouped)
    )


def _decimal_or_none(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    return Decimal(str(value))
