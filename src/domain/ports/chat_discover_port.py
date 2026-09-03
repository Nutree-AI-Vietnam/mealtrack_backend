"""Discover adapter used by chat next-meal cards."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ChatDiscoverBatch:
    session_id: str | None
    meals: tuple[dict[str, Any], ...] = ()


class ChatDiscoverPort(ABC):
    """Existing meal-suggestions discover. Chat must not invent macros."""

    @abstractmethod
    async def discover_meals(
        self,
        *,
        user_id: str,
        meal_type: str,
        meal_portion_type: str,
        language: str,
        calorie_target: int | None,
        protein_target: float | None,
        carbs_target: float | None,
        fat_target: float | None,
        session_id: str | None,
        count: int,
    ) -> ChatDiscoverBatch:
        """Return a discover batch. Raise on provider/timeout errors."""
