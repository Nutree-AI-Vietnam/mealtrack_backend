"""Hydration entry domain entity."""

import uuid
from dataclasses import dataclass, field
from datetime import datetime

from src.domain.model.nutrition.macros import Macros
from src.domain.model.nutrition.micros import Micros


def _hydration_entry_id() -> str:
    return f"hydr_{uuid.uuid4().hex}"


@dataclass
class HydrationEntry:
    user_id: str
    drink_name_snapshot: str
    volume_ml: int
    credited_ml: int
    logged_at: datetime
    id: str = field(default_factory=_hydration_entry_id)
    drink_id: str | None = None
    emoji_snapshot: str | None = None
    protein_g: float = 0.0
    carbs_g: float = 0.0
    fat_g: float = 0.0
    fiber_g: float = 0.0
    sugar_g: float = 0.0
    source: str = "hydration"
    legacy_meal_id: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    image_url: str | None = None  # URL of scanned beverage image
    micros: Micros | None = None

    @property
    def calories(self) -> float:
        return Macros(
            protein=self.protein_g,
            carbs=self.carbs_g,
            fat=self.fat_g,
            fiber=self.fiber_g,
            sugar=self.sugar_g,
        ).total_calories
