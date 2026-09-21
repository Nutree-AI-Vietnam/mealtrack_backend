"""Read-time grocery aggregation for weekly plans."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from src.domain.model.weekly_meal_planner import WeeklyMealPlan
from src.domain.services.meal_recommendation.ingredient_quantity_normalization import (
    normalize_ingredient_quantity,
)


@dataclass(frozen=True)
class GroceryItem:
    ingredient_id: int
    name: str
    category: str
    total_needed: float
    unit: str
    stock_amount: float | None
    stock_kind: str | None
    status: str
    quantity_confidence: str


@dataclass(frozen=True)
class GroceryCategory:
    category: str
    items: tuple[GroceryItem, ...]


class WeeklyGroceryService:
    """Aggregate canonical catalog ingredient quantities without client math."""

    async def calculate(self, uow, plan: WeeklyMealPlan) -> tuple[GroceryCategory, ...]:
        totals: dict[tuple[int, str, str], dict] = defaultdict(
            lambda: {
                "name": "",
                "category": "pantry",
                "amount": Decimal("0"),
                "quantity_confidence": "verified",
            }
        )
        for slot in plan.slots:
            if slot.recipe_id is None:
                continue
            meal = await uow.catalog_recipes.get_meal(slot.recipe_id)
            if meal is None:
                continue
            for ingredient in meal.ingredients:
                canonical_unit = ingredient.canonical_unit or ingredient.unit.casefold()
                dimension = ingredient.quantity_dimension or f"raw:{canonical_unit}"
                key = (ingredient.food_reference_id, dimension, canonical_unit)
                totals[key]["name"] = ingredient.name
                totals[key]["category"] = ingredient.category or "pantry"
                base_servings = getattr(meal, "base_servings", None)
                serving_confidence = getattr(meal, "serving_confidence", "unknown")
                if (
                    base_servings
                    and base_servings > 0
                    and serving_confidence != "unknown"
                ):
                    multiplier = Decimal(plan.people) / Decimal(base_servings)
                else:
                    multiplier = Decimal("1")
                    serving_confidence = "unknown"
                totals[key]["amount"] += (
                    ingredient.canonical_amount or Decimal(str(ingredient.quantity))
                ) * multiplier
                if serving_confidence == "unknown":
                    totals[key]["quantity_confidence"] = "unscaled"
                elif ingredient.quantity_confidence != "exact":
                    totals[key]["quantity_confidence"] = "unconverted"
                elif serving_confidence == "estimated":
                    totals[key]["quantity_confidence"] = "estimated"

        pantry = await uow.weekly_meal_plans.list_pantry(
            user_id=plan.user_id, plan_id=plan.id
        )
        pantry_by_food = {int(item["food_reference_id"]): item for item in pantry}
        grouped: dict[str, list[GroceryItem]] = defaultdict(list)
        for (food_id, dimension, unit), value in sorted(
            totals.items(), key=lambda item: (item[0][0], item[0][1])
        ):
            stock = pantry_by_food.get(food_id, {})
            needed = float(value["amount"])
            stock_amount = _stock_amount(stock, dimension=dimension, unit=unit)
            if stock_amount is None and stock.get("stock_kind"):
                status = "owned"
            else:
                available = stock_amount or 0.0
                status = (
                    "owned"
                    if max(needed - available, 0.0) == 0
                    else ("need_more" if available else "needed")
                )
            grouped[value["category"]].append(
                GroceryItem(
                    ingredient_id=food_id,
                    name=value["name"],
                    category=value["category"],
                    total_needed=needed,
                    unit=unit,
                    stock_amount=stock_amount,
                    stock_kind=stock.get("stock_kind"),
                    status=status,
                    quantity_confidence=value["quantity_confidence"],
                )
            )
        return tuple(
            GroceryCategory(category=category, items=tuple(items))
            for category, items in sorted(grouped.items())
        )


def _stock_amount(stock: dict, *, dimension: str, unit: str) -> float | None:
    raw_amount = stock.get("custom_amount")
    if raw_amount is None:
        return None
    stock_unit = stock.get("custom_unit") or unit
    normalized = normalize_ingredient_quantity(raw_amount, stock_unit)
    if normalized.dimension != dimension or normalized.unit != unit:
        return 0.0
    return float(normalized.amount)
