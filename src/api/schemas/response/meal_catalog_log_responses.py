"""Responses for catalog meal logging and logged history."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from src.api.schemas.response.meal_catalog_responses import MealCatalogItemResponse


class LogCatalogMealResponse(BaseModel):
    meal_id: str
    catalog_meal_id: str
    logged_via: Literal["catalog"]
    plan_id: str | None
    slot_id: str | None
    logged_meal_id: str
    meal_date: date
    meal_type: str


class MealCatalogLoggedListResponse(BaseModel):
    items: list[MealCatalogItemResponse]
    limit: int = Field(..., ge=1, le=50)
