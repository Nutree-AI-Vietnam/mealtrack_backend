"""Read-time grocery aggregation for weekly plans."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

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


@dataclass(frozen=True)
class GroceryCategory:
    category: str
    items: tuple[GroceryItem, ...]


class WeeklyGroceryService:
    """Aggregate canonical catalog ingredient quantities without client math."""

    async def calculate(self, uow, plan: WeeklyMealPlan) -> tuple[GroceryCategory, ...]:
        slots: list[GrocerySlot] = []
        for slot in plan.slots:
            if slot.recipe_id is None:
                continue
            meal = await uow.catalog_recipes.get_meal(slot.recipe_id)
            if meal is None:
                continue
            slots.append(_slot_from_meal(meal, plan_people=plan.people, override=getattr(slot, "recipe_override", None)))
        pantry_rows = await uow.weekly_meal_plans.list_pantry(
            user_id=plan.user_id, plan_id=plan.id
        )
        interaction_loader = getattr(uow.weekly_meal_plans, "list_grocery_interactions", None)
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


def _slot_from_meal(meal, *, plan_people: int, override) -> GrocerySlot:
    ingredients = [
        GroceryIngredient(
            position=int(getattr(ingredient, "position", None) or index),
            food_reference_id=ingredient.food_reference_id,
            name=ingredient.name,
            quantity=ingredient.quantity,
            unit=ingredient.unit,
            category=ingredient.category or "pantry",
            quantity_confidence=ingredient.quantity_confidence,
        )
        for index, ingredient in enumerate(meal.ingredients, start=1)
    ]
    payload = getattr(meal, "recipe_payload", None) or {}
    for raw in payload.get("ingredients") or []:
        if raw.get("food_reference_id") is not None and raw.get("quantity") not in (None, ""):
            continue
        ingredients.append(
            GroceryIngredient(
                position=int(raw.get("position") or len(ingredients) + 1),
                food_reference_id=raw.get("food_reference_id"),
                name=str(raw.get("name") or ""),
                quantity=_decimal_or_none(raw.get("quantity")),
                unit=raw.get("unit"),
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
