"""Read-time grocery aggregation for weekly plans."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from src.domain.model.weekly_meal_planner import WeeklyMealPlan


@dataclass(frozen=True)
class GroceryItem:
    ingredient_id: int
    name: str
    category: str
    total_needed: float
    unit: str
    stock_amount: float
    stock_kind: str | None
    status: str


@dataclass(frozen=True)
class GroceryCategory:
    category: str
    items: tuple[GroceryItem, ...]


class WeeklyGroceryService:
    """Aggregate canonical catalog ingredient quantities without client math."""

    async def calculate(self, uow, plan: WeeklyMealPlan) -> tuple[GroceryCategory, ...]:
        totals: dict[tuple[int, str], dict] = defaultdict(
            lambda: {"name": "", "category": "pantry", "amount": 0.0}
        )
        for slot in plan.slots:
            if slot.recipe_id is None:
                continue
            meal = await uow.catalog_recipes.get_meal(slot.recipe_id)
            if meal is None:
                continue
            for ingredient in meal.ingredients:
                key = (ingredient.food_reference_id, ingredient.unit.casefold())
                totals[key]["name"] = ingredient.name
                totals[key]["category"] = ingredient.category or "pantry"
                totals[key]["amount"] += float(ingredient.quantity) * plan.people

        pantry = await uow.weekly_meal_plans.list_pantry(
            user_id=plan.user_id, plan_id=plan.id
        )
        pantry_by_food = {int(item["food_reference_id"]): item for item in pantry}
        grouped: dict[str, list[GroceryItem]] = defaultdict(list)
        for (food_id, unit), value in sorted(
            totals.items(), key=lambda item: (item[0][0], item[0][1])
        ):
            stock = pantry_by_food.get(food_id, {})
            stock_amount = float(stock.get("custom_amount") or 0)
            needed = float(value["amount"])
            remaining = max(needed - stock_amount, 0.0)
            status = (
                "owned"
                if remaining == 0
                else ("need_more" if stock_amount else "needed")
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
                )
            )
        return tuple(
            GroceryCategory(category=category, items=tuple(items))
            for category, items in sorted(grouped.items())
        )
