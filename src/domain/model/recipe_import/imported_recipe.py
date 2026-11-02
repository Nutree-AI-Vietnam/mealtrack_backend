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
    # Weight of the whole line; None means the line is not counted.
    grams: float | None
    note: str | None = None
    optional: bool = False
    group: str | None = None
    amount_estimated: bool = False
    # Macros cover grams * eaten_share.
    eaten_share: float = 1.0
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
    # The full-recipe page a video's caption linked to, when it was read.
    linked_recipe_url: str | None = None


@dataclass(frozen=True, slots=True)
class ImportedRecipe:
    """A recipe ready to review; nothing is saved until the user saves it.

    Ingredient amounts are always for the whole recipe; ``servings`` is how
    many people it feeds, and nutrition per serving divides by it. Changing
    the servings never rescales the ingredients.
    """

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
    # True when the source does not say how many people the recipe feeds.
    servings_estimated: bool = False
    # What one serving is ("3 pancakes"), for the servings first imported.
    serving_description: str | None = None
    # What the recipe makes when the source counts pieces ("12 pancakes").
    makes: str | None = None
    # Servings in the draft, kept when the user saves a different count.
    original_servings: int | None = None
