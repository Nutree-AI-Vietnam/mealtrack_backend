"""Pure weekly meal planner value objects and projections."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum


class WeeklyMealPlanStatus(StrEnum):
    DRAFT = "draft"
    CONFIRMED = "confirmed"


@dataclass(frozen=True)
class WeeklyMealPlanPreferences:
    """Bounded generation preferences captured with a plan."""

    people: int = 1
    diet: str = "any"
    cooking_time: str = "any"
    cuisine: str | None = None
    dislikes: tuple[str, ...] = ()
    allergies: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not 1 <= self.people <= 6:
            raise ValueError("people must be between 1 and 6")
        if self.diet not in {"any", "vegetarian", "no-pork"}:
            raise ValueError("diet must be any, vegetarian, or no-pork")
        if self.cooking_time not in {"any", "30"}:
            raise ValueError("cooking_time must be any or 30")
        if len(self.dislikes) > 32 or len(self.allergies) > 32:
            raise ValueError("preference lists are too large")

    @classmethod
    def from_payload(cls, payload: dict | None, *, people: int | None = None):
        payload = payload or {}
        return cls(
            people=people if people is not None else int(payload.get("people", 1)),
            diet=_clean_choice(payload.get("diet"), "any"),
            cooking_time=_clean_choice(payload.get("cooking_time"), "any"),
            cuisine=_clean_text(payload.get("cuisine"), 80),
            dislikes=_csv_values(payload.get("dislikes")),
            allergies=_csv_values(payload.get("allergies")),
        )

    def to_dict(self) -> dict:
        return {
            "people": self.people,
            "diet": self.diet,
            "cooking_time": self.cooking_time,
            "cuisine": self.cuisine,
            "dislikes": ", ".join(self.dislikes),
            "allergies": ", ".join(self.allergies),
        }


@dataclass(frozen=True)
class WeeklyMealPlanSlot:
    id: str
    day_index: int
    slot_index: int
    recipe_id: str | None
    is_logged: bool = False
    logged_meal_id: str | None = None
    version: int = 1


@dataclass(frozen=True)
class WeeklyMealPlan:
    id: str
    user_id: str
    week_start_date: date
    status: WeeklyMealPlanStatus
    people: int
    preferences: WeeklyMealPlanPreferences
    timezone: str
    slots: tuple[WeeklyMealPlanSlot, ...] = field(default_factory=tuple)
    daily_calories: int | None = None
    catalog_revision: str | None = None
    algorithm_version: str = "v1"
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.week_start_date.weekday() != 0:
            raise ValueError("week_start_date must be a Monday")
        if len(self.slots) != 14:
            raise ValueError("weekly plans must contain exactly 14 slots")
        coordinates = {(slot.day_index, slot.slot_index) for slot in self.slots}
        expected = {(day, slot) for day in range(7) for slot in range(2)}
        if coordinates != expected:
            raise ValueError(
                "weekly plan slots must cover all lunch and dinner coordinates"
            )


def _clean_text(value: object, max_length: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > max_length:
        raise ValueError("preference value is too long")
    return text


def _clean_choice(value: object, fallback: str) -> str:
    return _clean_text(value, 32) or fallback


def _csv_values(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    raw = value if isinstance(value, list | tuple) else str(value).split(",")
    values = tuple(item.strip().casefold() for item in raw if str(item).strip())
    if any(len(item) > 80 for item in values):
        raise ValueError("preference item is too long")
    return values
