"""Derive grocery quantities from scaled recipe lines, overrides, and pantry.

Interaction flags never change the calculated quantity. Slot overrides are
applied to a copy of the lines and do not rewrite the catalog payload.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Mapping

from src.domain.services.meal_recommendation.ingredient_quantity_normalization import (
    normalize_ingredient_quantity,
)
from src.domain.services.weekly_meal_planner.recipe_publication import (
    is_nutrition_safe_ingredient,
    is_planner_eligible,
)


@dataclass(frozen=True)
class GroceryIngredient:
    position: int
    food_reference_id: int | None
    name: str
    quantity: Decimal | None
    unit: str | None
    category: str = "pantry"
    quantity_text: str | None = None
    quantity_confidence: str = "exact"

    def as_mapping(self) -> dict:
        return {
            "food_reference_id": self.food_reference_id,
            "quantity": self.quantity,
            "quantity_text": self.quantity_text,
        }


@dataclass(frozen=True)
class GrocerySlot:
    recipe_id: str | None
    publication_status: str
    nutrition_status: str
    is_active: bool
    base_servings: int | None
    serving_confidence: str
    people: int
    ingredients: tuple[GroceryIngredient, ...]
    recipe_override: Mapping | None = None


@dataclass(frozen=True)
class PantryAvailability:
    food_reference_id: int
    available_amount: Decimal | None
    available_unit: str | None = None


@dataclass(frozen=True)
class GroceryInteraction:
    food_reference_id: int
    checked: bool = False
    do_not_buy: bool = False
    manually_owned: bool = False


@dataclass(frozen=True)
class DerivedGroceryItem:
    ingredient_id: int
    name: str
    category: str
    total_needed: float
    remaining: float
    unit: str
    available_amount: float | None
    status: str
    quantity_confidence: str
    contributions: tuple[tuple[int, float], ...]
    checked: bool = False
    do_not_buy: bool = False
    manually_owned: bool = False


def apply_recipe_override(
    ingredients: tuple[GroceryIngredient, ...], override: Mapping | None
) -> tuple[GroceryIngredient, ...]:
    if not override:
        return ingredients
    removed = {
        int(item["position"])
        for item in override.get("removed_ingredients") or []
        if isinstance(item, Mapping) and item.get("position") is not None
    }
    changes: dict[int, Mapping] = {}
    for change in override.get("ingredient_changes") or []:
        if not isinstance(change, Mapping) or change.get("position") is None:
            continue
        if change.get("new_quantity") is None:
            continue
        changes[int(change["position"])] = change
    updated: list[GroceryIngredient] = []
    for line in ingredients:
        if line.position in removed:
            continue
        change = changes.get(line.position)
        if change is None:
            updated.append(line)
            continue
        updated.append(
            replace(
                line,
                quantity=Decimal(str(change["new_quantity"])),
                unit=str(change.get("unit") or line.unit or ""),
            )
        )
    return tuple(updated)


def aggregate_grocery(
    slots: tuple[GrocerySlot, ...] | list[GrocerySlot],
    pantry: tuple[PantryAvailability, ...] | list[PantryAvailability],
    interactions: tuple[GroceryInteraction, ...] | list[GroceryInteraction] = (),
) -> tuple[DerivedGroceryItem, ...]:
    totals: dict[tuple[int, str, str], dict] = defaultdict(
        lambda: {
            "name": "",
            "category": "pantry",
            "amount": Decimal("0"),
            "quantity_confidence": "verified",
            "contributions": [],
        }
    )
    for slot in slots:
        if slot.recipe_id is None:
            continue
        if not is_planner_eligible(
            publication_status=slot.publication_status,
            nutrition_status=slot.nutrition_status,
            is_active=slot.is_active,
        ):
            continue
        multiplier, serving_confidence = _serving_multiplier(slot)
        for line in apply_recipe_override(slot.ingredients, slot.recipe_override):
            if not is_nutrition_safe_ingredient(line.as_mapping()):
                continue
            normalized = normalize_ingredient_quantity(line.quantity, line.unit or "")
            key = (int(line.food_reference_id), normalized.dimension, normalized.unit)
            scaled = normalized.amount * multiplier
            bucket = totals[key]
            bucket["name"] = line.name
            bucket["category"] = line.category or "pantry"
            bucket["amount"] += scaled
            bucket["contributions"].append((line.position, float(scaled)))
            bucket["quantity_confidence"] = _confidence(
                current=bucket["quantity_confidence"],
                serving_confidence=serving_confidence,
                line_confidence=line.quantity_confidence,
            )

    pantry_by_food = {item.food_reference_id: item for item in pantry}
    interaction_by_food = {item.food_reference_id: item for item in interactions}
    items: list[DerivedGroceryItem] = []
    for (food_id, dimension, unit), value in sorted(totals.items(), key=lambda item: item[0]):
        stock = pantry_by_food.get(food_id)
        available = _available_amount(stock, dimension=dimension, unit=unit)
        status, remaining = grocery_status(value["amount"], available)
        flags = interaction_by_food.get(food_id, GroceryInteraction(food_id))
        items.append(
            DerivedGroceryItem(
                ingredient_id=food_id,
                name=value["name"],
                category=value["category"],
                total_needed=float(value["amount"]),
                remaining=float(remaining),
                unit=unit,
                available_amount=None if available is None else float(available),
                status=status,
                quantity_confidence=value["quantity_confidence"],
                contributions=tuple(sorted(value["contributions"])),
                checked=flags.checked,
                do_not_buy=flags.do_not_buy,
                manually_owned=flags.manually_owned,
            )
        )
    return tuple(items)


def grocery_status(
    required: Decimal, available: Decimal | None
) -> tuple[str, Decimal]:
    if available is None or available <= 0:
        return "needed", required
    remaining = required - available
    if remaining <= 0:
        return "owned", Decimal("0")
    return "need_more", remaining


def _serving_multiplier(slot: GrocerySlot) -> tuple[Decimal, str]:
    if slot.base_servings and slot.base_servings > 0 and slot.serving_confidence != "unknown":
        return (
            Decimal(slot.people) / Decimal(slot.base_servings),
            slot.serving_confidence,
        )
    return Decimal("1"), "unknown"


def _confidence(*, current: str, serving_confidence: str, line_confidence: str) -> str:
    if serving_confidence == "unknown":
        return "unscaled"
    if line_confidence != "exact":
        return "unconverted"
    if serving_confidence == "estimated":
        return "estimated"
    return current


def _available_amount(
    stock: PantryAvailability | None, *, dimension: str, unit: str
) -> Decimal | None:
    if stock is None or stock.available_amount is None:
        return None
    normalized = normalize_ingredient_quantity(
        stock.available_amount, stock.available_unit or unit
    )
    if normalized.dimension != dimension or normalized.unit != unit:
        return Decimal("0")
    return normalized.amount
