"""A recipe as the AI read it from a source, before nutrition is priced."""

from __future__ import annotations

from dataclasses import dataclass

from src.domain.model.common.enums import MealType


@dataclass(frozen=True, slots=True)
class ExtractedIngredient:
    """One ingredient line.

    ``name`` is for display in the requested language. ``english_name`` is
    the canonical food the nutrition lookup searches for. ``grams`` is the
    weight of the whole line, estimated when the unit is not a weight or a
    volume (2 cloves, 1 can).
    """

    name: str
    english_name: str
    quantity: float | None = None
    unit: str | None = None
    grams: float | None = None
    note: str | None = None
    optional: bool = False
    group: str | None = None


@dataclass(frozen=True, slots=True)
class RecipeStep:
    instruction: str
    duration_minutes: int | None = None


@dataclass(frozen=True, slots=True)
class ExtractedRecipe:
    """Structured recipe text; ``is_recipe`` False means the source had none."""

    is_recipe: bool
    name: str
    language: str
    rejection_reason: str | None = None
    english_name: str | None = None
    description: str | None = None
    servings: int | None = None
    servings_stated: bool = False
    quantities_stated: bool = False
    prep_time_minutes: int | None = None
    cook_time_minutes: int | None = None
    total_time_minutes: int | None = None
    ingredients: tuple[ExtractedIngredient, ...] = ()
    steps: tuple[RecipeStep, ...] = ()
    cuisine_type: str | None = None
    origin_country: str | None = None
    meal_type: MealType | None = None
    emoji: str | None = None

    @property
    def quantified_share(self) -> float:
        """Share of required ingredients with an amount or weight."""
        required = [item for item in self.ingredients if not item.optional]
        if not required:
            return 0.0
        quantified = [
            item
            for item in required
            if item.quantity is not None or item.grams is not None
        ]
        return len(quantified) / len(required)
