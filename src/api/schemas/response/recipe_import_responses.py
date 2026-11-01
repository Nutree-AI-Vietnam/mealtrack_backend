"""Response bodies for recipe import."""

from __future__ import annotations

from pydantic import BaseModel, Field

from src.domain.model.recipe_import import (
    ImportedIngredient,
    ImportedRecipe,
    RecipeMacros,
)


class RecipeMacrosResponse(BaseModel):
    calories: float = Field(..., description="Backend formula from the macros")
    protein: float
    carbs: float
    fat: float
    fiber: float
    sugar: float

    @classmethod
    def from_domain(cls, macros: RecipeMacros) -> RecipeMacrosResponse:
        return cls(
            calories=macros.calories,
            protein=macros.protein,
            carbs=macros.carbs,
            fat=macros.fat,
            fiber=macros.fiber,
            sugar=macros.sugar,
        )


class ImportedIngredientResponse(BaseModel):
    name: str
    english_name: str
    quantity: float | None
    unit: str | None
    grams: float | None = Field(None, description="Weight used for nutrition")
    note: str | None
    optional: bool
    group: str | None
    macros: RecipeMacrosResponse | None = Field(
        None, description="Null when the line could not be weighed"
    )
    food_reference_id: int | None
    nutrition_source: str | None = Field(
        None, description="food_reference, fatsecret or ai_estimate"
    )

    @classmethod
    def from_domain(cls, item: ImportedIngredient) -> ImportedIngredientResponse:
        return cls(
            name=item.name,
            english_name=item.english_name,
            quantity=item.quantity,
            unit=item.unit,
            grams=item.grams,
            note=item.note,
            optional=item.optional,
            group=item.group,
            macros=RecipeMacrosResponse.from_domain(item.macros)
            if item.macros
            else None,
            food_reference_id=item.food_reference_id,
            nutrition_source=item.nutrition_source.value
            if item.nutrition_source
            else None,
        )


class ImportedStepResponse(BaseModel):
    step: int
    instruction: str
    duration_minutes: int | None


class RecipeNutritionResponse(BaseModel):
    servings: int
    per_serving: RecipeMacrosResponse
    total: RecipeMacrosResponse
    complete: bool = Field(..., description="Every required ingredient was weighed")
    estimated: bool = Field(
        ..., description="Some ingredient used the AI estimate tier"
    )
    unpriced_ingredients: list[str]


class RecipeSourceResponse(BaseModel):
    type: str = Field(..., description="url, text or image")
    platform: str
    url: str | None
    title: str | None
    author: str | None
    site_name: str | None
    signals: list[str] = Field(..., description="What the recipe was read from")


class ImportedRecipeResponse(BaseModel):
    """An unsaved draft; send it (edited) to POST /v1/recipe-imports/save."""

    import_id: str
    language: str
    name: str
    english_name: str | None
    description: str | None
    emoji: str | None
    cuisine_type: str | None
    origin_country: str | None
    meal_type: str | None
    servings: int
    prep_time_minutes: int | None
    cook_time_minutes: int | None
    total_time_minutes: int | None
    image_url: str | None
    ingredients: list[ImportedIngredientResponse]
    steps: list[ImportedStepResponse]
    nutrition: RecipeNutritionResponse
    source: RecipeSourceResponse
    warnings: list[str] = Field(
        ...,
        description=(
            "servings_estimated, quantities_estimated, nutrition_partial, "
            "nutrition_estimated, video_not_analyzed"
        ),
    )

    @classmethod
    def from_domain(cls, recipe: ImportedRecipe) -> ImportedRecipeResponse:
        nutrition = recipe.nutrition
        return cls(
            import_id=recipe.import_id,
            language=recipe.language,
            name=recipe.name,
            english_name=recipe.english_name,
            description=recipe.description,
            emoji=recipe.emoji,
            cuisine_type=recipe.cuisine_type,
            origin_country=recipe.origin_country,
            meal_type=recipe.meal_type.value if recipe.meal_type else None,
            servings=recipe.servings,
            prep_time_minutes=recipe.prep_time_minutes,
            cook_time_minutes=recipe.cook_time_minutes,
            total_time_minutes=recipe.total_time_minutes,
            image_url=recipe.image_url,
            ingredients=[
                ImportedIngredientResponse.from_domain(i) for i in recipe.ingredients
            ],
            steps=[
                ImportedStepResponse(
                    step=index,
                    instruction=step.instruction,
                    duration_minutes=step.duration_minutes,
                )
                for index, step in enumerate(recipe.steps, start=1)
            ],
            nutrition=RecipeNutritionResponse(
                servings=nutrition.servings,
                per_serving=RecipeMacrosResponse.from_domain(nutrition.per_serving),
                total=RecipeMacrosResponse.from_domain(nutrition.total),
                complete=nutrition.complete,
                estimated=nutrition.estimated,
                unpriced_ingredients=list(nutrition.unpriced_ingredients),
            ),
            source=RecipeSourceResponse(
                type=recipe.source.type.value,
                platform=recipe.source.platform.value,
                url=recipe.source.url,
                title=recipe.source.title,
                author=recipe.source.author,
                site_name=recipe.source.site_name,
                signals=[signal.value for signal in recipe.source.signals],
            ),
            warnings=[warning.value for warning in recipe.warnings],
        )


class RecipeImportCapabilitiesResponse(BaseModel):
    enabled: bool
    sources: list[str]
    video_platforms: list[str]
    video_captions: bool
    video_media_analysis: bool = Field(
        ...,
        description="Speech and on-screen text are read when a caption is not enough",
    )
    daily_limit: int
    timeout_seconds: float
