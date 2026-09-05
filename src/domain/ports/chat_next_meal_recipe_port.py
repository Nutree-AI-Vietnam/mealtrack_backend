"""Structured next-meal recipes from the chat completion provider."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ChatNextMealRecipePort(ABC):
    """One structured-output call. Must not store conversation state."""

    @abstractmethod
    async def generate_next_meal_recipes(
        self,
        *,
        model: str,
        locale: str,
        slot: str,
        user_message: str,
        remaining_calories: float | None,
        remaining_protein_g: float | None,
        remaining_carbs_g: float | None,
        remaining_fat_g: float | None,
        allergies: list[str],
        dietary_preferences: list[str],
    ) -> list[dict[str, Any]]:
        """Return 0–3 recipe dicts. Empty list on failure."""
