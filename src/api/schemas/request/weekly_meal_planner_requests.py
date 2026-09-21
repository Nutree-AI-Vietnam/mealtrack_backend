"""Pydantic contracts for weekly meal planner writes."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class WeeklyMealPlanPreferencesRequest(BaseModel):
    diet: Literal["any", "vegetarian", "no-pork"] = "any"
    cooking_time: Literal["any", "30"] = "any"
    cuisine: str | None = Field(default=None, max_length=80)
    dislikes: str = Field(default="", max_length=1000)
    allergies: str = Field(default="", max_length=1000)


class GenerateWeeklyMealPlanRequest(BaseModel):
    week_start_date: date
    people: int = Field(default=1, ge=1, le=6)
    preferences: WeeklyMealPlanPreferencesRequest = Field(
        default_factory=WeeklyMealPlanPreferencesRequest
    )


class WeeklyMealPlanSlotUpdateRequest(BaseModel):
    day_index: int = Field(ge=0, le=6)
    slot_index: int = Field(ge=0, le=1)
    recipe_id: str | None = Field(default=None, max_length=36)


class UpdateWeeklyMealPlanRequest(BaseModel):
    people: int | None = Field(default=None, ge=1, le=6)
    status: Literal["draft", "confirmed"] | None = None
    preferences: WeeklyMealPlanPreferencesRequest | None = None
    slots: list[WeeklyMealPlanSlotUpdateRequest] = Field(
        default_factory=list, max_length=14
    )

    @field_validator("slots")
    @classmethod
    def unique_slots(cls, value):
        coordinates = [(item.day_index, item.slot_index) for item in value]
        if len(coordinates) != len(set(coordinates)):
            raise ValueError("duplicate weekly slot coordinate")
        return value


class AiAdjustMealPlanRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=1000)


class PantryStockUpdateRequest(BaseModel):
    ingredient_id: int = Field(ge=1)
    amount: float = Field(ge=0, le=100000)
    kind: Literal["bought", "owned"]


class UpdateMealPlanPantryRequest(BaseModel):
    stock_updates: list[PantryStockUpdateRequest] = Field(max_length=100)


class LogMealPlanSlotRequest(BaseModel):
    date: date
    meal_type: Literal["lunch", "dinner"]
    portion_multiplier: Literal[0.5, 1.0, 1.5, 2.0] = 1.0
