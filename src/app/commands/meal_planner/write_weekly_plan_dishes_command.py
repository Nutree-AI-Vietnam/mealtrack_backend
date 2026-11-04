"""Fill a week that is still being written with dishes a model invents."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class WriteWeeklyPlanDishesCommand:
    """Write the dishes of one plan that is waiting in 'preparing'.

    Carried by a background job rather than a request: a week is twenty-one
    dishes, and asking for them takes longer than anybody should hold a
    connection open.
    """

    user_id: str
    plan_id: str
    week_start_date: date
    daily_calories: int
    timezone: str | None = None
    language: str | None = None
    daily_protein: float | None = None
    daily_carbs: float | None = None
    daily_fat: float | None = None
