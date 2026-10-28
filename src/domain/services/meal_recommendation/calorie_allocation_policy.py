"""Deterministic calorie allocation for three daily recommendation slots."""

from __future__ import annotations

MEAL_TYPE_ORDER = ("breakfast", "lunch", "dinner")
_MEAL_WEIGHTS = {
    "breakfast": 0.25,
    "lunch": 0.375,
    "dinner": 0.375,
}


class CalorieAllocationPolicy:
    """Allocate daily calories across supported meal slots deterministically."""

    def __init__(self, weights: tuple[float, float, float] | None = None):
        """Use breakfast, lunch, dinner ``weights`` in place of the default split."""
        if weights is None:
            self._weights = _MEAL_WEIGHTS
            return
        if len(weights) != len(MEAL_TYPE_ORDER) or any(w <= 0 for w in weights):
            raise ValueError("weights must be three positive shares")
        if abs(sum(weights) - 1) > 0.001:
            raise ValueError("weights must add up to 1")
        self._weights = dict(zip(MEAL_TYPE_ORDER, weights, strict=True))

    def allocate(self, daily_calories: int) -> dict[str, int]:
        if daily_calories <= 0:
            raise ValueError("daily_calories must be positive")

        allocations = {
            meal_type: round(daily_calories * weight)
            for meal_type, weight in self._weights.items()
        }
        drift = daily_calories - sum(allocations.values())
        allocations["dinner"] += drift
        return {meal_type: allocations[meal_type] for meal_type in MEAL_TYPE_ORDER}

    def target_for(self, daily_calories: int, meal_type: str) -> int:
        """Return the browse target for a supported meal type."""
        if meal_type == "snack":
            if daily_calories <= 0:
                raise ValueError("daily_calories must be positive")
            return max(1, round(daily_calories * 0.10))
        allocations = self.allocate(daily_calories)
        try:
            return allocations[meal_type]
        except KeyError:
            raise ValueError(f"unsupported meal_type: {meal_type}") from None
