"""Fakes shared by the recipe import application tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.domain.model.recipe_import import (
    ExtractedIngredient,
    ExtractedRecipe,
    RecipeSourceMaterial,
    RecipeSourcePlatform,
    RecipeStep,
    VideoMediaAnalysis,
)
from src.domain.services.meal_suggestion.nutrition_lookup_service import (
    IngredientMacros,
    MealMacros,
)

# Per 100 g: protein, carbs, fat, fiber, sugar; tier.
FOODS: dict[str, tuple[float, float, float, float, float, str]] = {
    "chicken wing, raw": (18.0, 0.0, 15.0, 0.0, 0.0, "T1_food_reference"),
    "fish sauce": (5.0, 3.6, 0.0, 0.0, 3.6, "T2_fatsecret"),
    "rice, uncooked": (7.0, 80.0, 0.6, 1.3, 0.1, "T1_food_reference"),
    "garlic": (6.4, 33.0, 0.5, 2.1, 1.0, "T3_ai_estimate"),
}


class FakeNutrition:
    def __init__(self) -> None:
        self.calls: list[list[dict[str, Any]]] = []

    async def calculate_meal_macros(
        self, ingredients: list[dict[str, Any]]
    ) -> MealMacros:
        self.calls.append(ingredients)
        items = []
        for line in ingredients:
            assert line["unit"] == "g"
            p, c, f, fiber, sugar, tier = FOODS.get(
                line["name"], (1, 1, 1, 0, 0, "T1_food_reference")
            )
            factor = line["amount"] / 100
            items.append(
                IngredientMacros(
                    name=line["name"],
                    quantity_g=line["amount"],
                    calories=round(
                        (p * 4 + (c - fiber) * 4 + fiber * 2 + f * 9) * factor, 1
                    ),
                    protein=round(p * factor, 1),
                    carbs=round(c * factor, 1),
                    fat=round(f * factor, 1),
                    fiber=round(fiber * factor, 1),
                    sugar=round(sugar * factor, 1),
                    source_tier=tier,
                    food_reference_id=7 if tier == "T1_food_reference" else None,
                )
            )
        return MealMacros(
            calories=sum(i.calories for i in items),
            protein=sum(i.protein for i in items),
            carbs=sum(i.carbs for i in items),
            fat=sum(i.fat for i in items),
            fiber=sum(i.fiber for i in items),
            sugar=sum(i.sugar for i in items),
            ingredients=items,
            t1_count=0,
            t2_count=0,
            t3_count=0,
        )


def ingredient(
    name: str,
    english: str,
    quantity: float | None,
    unit: str | None,
    grams: float | None = None,
    **extra: Any,
) -> ExtractedIngredient:
    return ExtractedIngredient(
        name=name,
        english_name=english,
        quantity=quantity,
        unit=unit,
        grams=grams,
        **extra,
    )


def wings_recipe(**overrides: Any) -> ExtractedRecipe:
    values: dict[str, Any] = {
        "is_recipe": True,
        "name": "Cánh gà chiên nước mắm",
        "language": "vi",
        "servings": 2,
        "servings_stated": True,
        "quantities_stated": True,
        "ingredients": (
            ingredient("cánh gà", "chicken wing, raw", 500, "g"),
            ingredient("nước mắm", "fish sauce", 2, "tbsp"),
            ingredient("tỏi", "garlic", 3, "clove", grams=9),
        ),
        "steps": (RecipeStep("Ướp gà", 15), RecipeStep("Chiên gà", 12)),
    }
    values.update(overrides)
    return ExtractedRecipe(**values)


@dataclass
class FakeExtractor:
    results: list[ExtractedRecipe] = field(default_factory=list)
    materials: list[RecipeSourceMaterial] = field(default_factory=list)
    photos: list[bytes] = field(default_factory=list)

    async def extract(
        self, material: RecipeSourceMaterial, *, language: str
    ) -> ExtractedRecipe:
        self.materials.append(material)
        return self.results.pop(0)

    async def read_photo(
        self, image_bytes: bytes, *, language: str, note: str | None = None
    ) -> ExtractedRecipe:
        self.photos.append(image_bytes)
        return self.results.pop(0)


@dataclass
class FakeWeb:
    material: RecipeSourceMaterial | None = None
    urls: list[str] = field(default_factory=list)

    async def read_page(self, url: str) -> RecipeSourceMaterial:
        self.urls.append(url)
        assert self.material is not None
        return self.material


@dataclass
class FakeVideo:
    material: RecipeSourceMaterial
    media: VideoMediaAnalysis | Exception = field(default_factory=VideoMediaAnalysis)
    media_analysis_enabled: bool = True
    calls: list[tuple[str, RecipeSourcePlatform]] = field(default_factory=list)
    analyzed: int = 0

    async def read_video(
        self, url: str, platform: RecipeSourcePlatform, *, language: str
    ) -> RecipeSourceMaterial:
        self.calls.append((url, platform))
        return self.material

    async def analyze_media(
        self, material: RecipeSourceMaterial, *, language: str
    ) -> VideoMediaAnalysis:
        self.analyzed += 1
        if isinstance(self.media, Exception):
            raise self.media
        return self.media
