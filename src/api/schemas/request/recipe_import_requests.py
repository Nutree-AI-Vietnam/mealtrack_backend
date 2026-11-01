"""Request bodies for recipe import."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

MealTypeName = Literal["breakfast", "lunch", "dinner", "snack"]
PlatformName = Literal[
    "website", "youtube", "tiktok", "instagram", "facebook", "text", "image"
]


class RecipeImportSourceRequest(BaseModel):
    """Exactly one source. Also the ``input`` of a ``recipe_import`` AI job."""

    url: str | None = Field(
        default=None,
        max_length=4096,
        description="A link, or share-sheet text that contains one",
    )
    text: str | None = Field(
        default=None,
        max_length=20_000,
        description="A recipe typed or pasted by the user",
    )
    image_url: str | None = Field(
        default=None, max_length=2048, description="Uploaded photo of a recipe"
    )
    image_id: str | None = Field(default=None, max_length=255)
    note: str | None = Field(
        default=None, max_length=300, description="Optional hint for a photo"
    )

    @model_validator(mode="after")
    def exactly_one_source(self) -> RecipeImportSourceRequest:
        given = [
            name
            for name, value in (
                ("url", self.url),
                ("text", self.text),
                ("image_url", self.image_url),
            )
            if value and value.strip()
        ]
        if len(given) != 1:
            raise ValueError("Send exactly one of url, text or image_url")
        if self.image_url and not self.image_id:
            raise ValueError("image_id is required with image_url")
        return self


class ImportedIngredientRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    english_name: str | None = Field(default=None, max_length=200)
    quantity: float | None = Field(default=None, ge=0, le=100_000)
    unit: str | None = Field(default=None, max_length=40)
    grams: float | None = Field(
        default=None,
        ge=0,
        le=20_000,
        description="Weight of the whole line; scale it when changing a count amount",
    )
    amount_estimated: bool = False
    eaten_share: float | None = Field(
        default=None, gt=0, le=1, description="Part of the line that is eaten; 1 when omitted"
    )
    note: str | None = Field(default=None, max_length=200)
    optional: bool = False
    group: str | None = Field(default=None, max_length=80)


class ImportedStepRequest(BaseModel):
    instruction: str = Field(..., min_length=1, max_length=1500)
    duration_minutes: int | None = Field(default=None, ge=0, le=1440)


class ImportedSourceRequest(BaseModel):
    type: Literal["url", "text", "image"] = "url"
    platform: PlatformName = "website"
    url: str | None = Field(default=None, max_length=2048)
    title: str | None = Field(default=None, max_length=200)
    author: str | None = Field(default=None, max_length=80)
    site_name: str | None = Field(default=None, max_length=80)


class SaveImportedRecipeRequest(BaseModel):
    """The reviewed draft. Nutrition is recomputed by the server."""

    import_id: str = Field(..., pattern=r"^[A-Za-z0-9_-]{8,48}$")
    meal_type: MealTypeName = "dinner"
    name: str = Field(..., min_length=1, max_length=200)
    english_name: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=600)
    emoji: str | None = Field(default=None, max_length=8)
    cuisine_type: str | None = Field(default=None, max_length=80)
    origin_country: str | None = Field(default=None, max_length=80)
    servings: int | None = Field(default=None, ge=1, le=100)
    prep_time_minutes: int | None = Field(default=None, ge=0, le=2880)
    cook_time_minutes: int | None = Field(default=None, ge=0, le=2880)
    total_time_minutes: int | None = Field(default=None, ge=0, le=2880)
    image_url: str | None = Field(default=None, max_length=2048)
    ingredients: list[ImportedIngredientRequest] = Field(
        ..., min_length=1, max_length=80
    )
    steps: list[ImportedStepRequest] = Field(default_factory=list, max_length=60)
    source: ImportedSourceRequest | None = None
