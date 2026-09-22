"""Drink catalog item — value object."""

from dataclasses import dataclass

from src.domain.model.hydration.hydration_enums import DrinkCategory


@dataclass(frozen=True)
class Drink:
    """
    Catalog entry for a drink type.

    Immutable value object used by the hydration feature.
    """

    id: str
    name: str
    sub: str | None
    default_ml: int
    kcal_per_100ml: float
    sugar_per_100ml: float
    hydration_weight: float
    brand_color: str
    category: DrinkCategory

    def kcal_for_volume(self, ml: int) -> float:
        """Return calories for the given volume in ml."""
        return round(self.kcal_per_100ml * ml / 100, 1)

    def sugar_for_volume(self, ml: int) -> float:
        """Return sugar grams for the given volume in ml."""
        return round(self.sugar_per_100ml * ml / 100, 1)

    def credited_ml_for_volume(self, ml: int) -> int:
        """Return water-equivalent ml credited for the given volume."""
        return int(ml * self.hydration_weight + 0.5)
