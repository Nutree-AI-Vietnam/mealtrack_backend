"""Shared response shapes for a catalog meal and its ingredients."""

from __future__ import annotations

from pydantic import BaseModel


class MealRecommendationMacrosResponse(BaseModel):
    protein_g: float
    carbs_g: float
    fat_g: float
    fiber_g: float
    sugar_g: float


class MealRecommendationIngredientResponse(BaseModel):
    food_reference_id: int | None = None
    display_name: str
    quantity: float
    unit: str


class MealRecommendationCatalogMealResponse(BaseModel):
    id: str
    name: str
    cuisine: str
    description: str | None = None
    image_url: str | None = None
    calories: int
    macros: MealRecommendationMacrosResponse
    ingredients: list[MealRecommendationIngredientResponse]
