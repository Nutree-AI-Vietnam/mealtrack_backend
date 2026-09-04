"""
Response schemas for activity endpoints.
"""

from pydantic import BaseModel


class MacrosResponse(BaseModel):
    """Macronutrient information."""

    protein: float
    carbs: float
    fat: float
    fiber: float = 0.0
    sugar: float = 0.0


class MealActivityResponse(BaseModel):
    """Response schema for meal activity."""

    id: str
    type: str = "meal"
    timestamp: str
    title: str
    emoji: str | None = None
    meal_type: str
    calories: float
    macros: MacrosResponse
    quantity: float
    status: str
    image_url: str | None = None
    nrf_quality: float | None = None
    nrf_coverage: int = 0
    meal_id: str | None = None
