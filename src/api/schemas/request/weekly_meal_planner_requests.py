"""Pydantic contracts for weekly meal planner writes."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


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
    expected_revision: int | None = Field(default=None, ge=1)
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

    @model_validator(mode="after")
    def preferences_are_complete(self):
        if self.preferences is not None:
            required = {"diet", "cooking_time", "cuisine", "dislikes", "allergies"}
            if not required.issubset(self.preferences.model_fields_set):
                raise ValueError("all preference fields must be provided together")
            if self.people is None:
                raise ValueError("people is required when preferences are updated")
        return self


class AiAdjustMealPlanRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=1000)
    target_day_index: int | None = Field(default=None, ge=0, le=6)
    target_slot_index: int | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def target_coordinates_are_paired(self):
        if (self.target_day_index is None) != (self.target_slot_index is None):
            raise ValueError("target day and meal slot must be provided together")
        return self


class PantryStockUpdateRequest(BaseModel):
    ingredient_id: int = Field(ge=1)
    available_amount: float | None = Field(default=None, ge=0, le=100000)
    available_unit: str | None = Field(default=None, max_length=80)
    checked: bool = False
    do_not_buy: bool = False
    manually_owned: bool = False


class UpdateMealPlanPantryRequest(BaseModel):
    stock_updates: list[PantryStockUpdateRequest] = Field(max_length=100)


class GroceryDayLineRequest(BaseModel):
    day_index: int = Field(ge=0, le=6)
    needed_amount: float | None = Field(default=None, ge=0, le=100000)
    covered: bool = False


class UpdateGroceryDayLinesRequest(BaseModel):
    ingredient_id: int = Field(ge=1)
    lines: list[GroceryDayLineRequest] = Field(max_length=7)


class LogMealPlanSlotRequest(BaseModel):
    date: date
    meal_type: Literal["lunch", "dinner"]
    expected_recipe_id: str = Field(min_length=1, max_length=128)
    portion_multiplier: Literal[0.5, 1.0, 1.5, 2.0] = 1.0
