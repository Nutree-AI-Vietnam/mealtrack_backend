"""The reviewable recipe draft returned to the app, priced by the backend."""

from __future__ import annotations

from dataclasses import dataclass

from src.domain.model.common.enums import MealType
from src.domain.model.recipe_import.enums import (
    IngredientNutritionSource,
    RecipeImportSourceType,
    RecipeImportWarning,
    RecipeSignal,
    RecipeSourcePlatform,
)
from src.domain.model.recipe_import.extracted_recipe import RecipeStep


@dataclass(frozen=True, slots=True)
class RecipeMacros:
    """Macros in grams; calories always come from the backend formula."""

    calories: float
    protein: float
    carbs: float
    fat: float
    fiber: float
    sugar: float

    @classmethod
    def zero(cls) -> RecipeMacros:
        return cls(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)


@dataclass(frozen=True, slots=True)
class ImportedIngredient:
    name: str
    english_name: str
    quantity: float | None
    unit: str | None
    # Weight used for nutrition; None means the line is not counted.
    grams: float | None
    note: str | None = None
    optional: bool = False
    group: str | None = None
    macros: RecipeMacros | None = None
    food_reference_id: int | None = None
    nutrition_source: IngredientNutritionSource | None = None


@dataclass(frozen=True, slots=True)
class RecipeNutrition:
    servings: int
    total: RecipeMacros
    per_serving: RecipeMacros
    # Required ingredients the backend could not weigh ("salt to taste").
    unpriced_ingredients: tuple[str, ...] = ()
    # Any ingredient priced by the AI estimate tier.
    estimated: bool = False

    @property
    def complete(self) -> bool:
        return not self.unpriced_ingredients


@dataclass(frozen=True, slots=True)
class RecipeSourceInfo:
    type: RecipeImportSourceType
    platform: RecipeSourcePlatform
    url: str | None = None
    title: str | None = None
    author: str | None = None
    site_name: str | None = None
    signals: tuple[RecipeSignal, ...] = ()


@dataclass(frozen=True, slots=True)
class ImportedRecipe:
    """A recipe ready to review; nothing is saved until the user saves it."""

    import_id: str
    language: str
    name: str
    servings: int
    ingredients: tuple[ImportedIngredient, ...]
    steps: tuple[RecipeStep, ...]
    nutrition: RecipeNutrition
    source: RecipeSourceInfo
    english_name: str | None = None
    description: str | None = None
    emoji: str | None = None
    cuisine_type: str | None = None
    origin_country: str | None = None
    meal_type: MealType | None = None
    prep_time_minutes: int | None = None
    cook_time_minutes: int | None = None
    total_time_minutes: int | None = None
    image_url: str | None = None
    warnings: tuple[RecipeImportWarning, ...] = ()
