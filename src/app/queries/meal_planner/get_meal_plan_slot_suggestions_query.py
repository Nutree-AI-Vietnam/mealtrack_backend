from dataclasses import dataclass

from src.app.events.base import Query


@dataclass
class GetMealPlanSlotSuggestionsQuery(Query):
    """Recipes that fit one weekly slot under the plan's preferences."""

    user_id: str
    plan_id: str
    slot_id: str
    diet: str | None = None
    max_cook_time: int | None = None
    min_protein: float | None = None
    limit: int = 10
