"""Structured output for reading a Supplement Facts panel from a photo.

Amounts and units stay as printed; ``label_units`` converts them. Each row
names the tracked nutrient it counts toward, or none (iodine, omega-3,
creatine), so untracked rows never reach the day totals.

The OpenAI schema normalizer drops length and range limits, so the model never
sees them. Text is trimmed and odd numbers are set aside here instead of
being rejected; only types and enum values can fail a reply.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

TrackedNutrientKey = Literal[
    "vitamin_a",
    "vitamin_c",
    "vitamin_d",
    "vitamin_e",
    "vitamin_k",
    "thiamin",
    "riboflavin",
    "niacin",
    "vitamin_b6",
    "vitamin_b12",
    "folate",
    "calcium",
    "iron",
    "magnesium",
    "phosphorus",
    "potassium",
    "sodium",
    "zinc",
    "selenium",
]
LabelForm = Literal[
    "capsule",
    "softgel",
    "tablet",
    "chewable",
    "gummy",
    "effervescent",
    "powder",
    "liquid",
    "drops",
    "sachet",
]
# Protein powders, gainers and meal shakes are food (Tony, 31 Oct).
ProductKind = Literal["supplement", "protein_powder", "mass_gainer", "meal_replacement"]

_MAX_ROWS = 80
_DEFAULT_NAME = "Thực phẩm bổ sung"


def _trimmed(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()[:limit].strip()
    return text or None


def _amount(value: Any) -> Any:
    # A negative or non-finite amount is a misread; drop it, keep the row.
    if isinstance(value, int | float) and not value >= 0:
        return None
    return value


class SupplementLabelRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field("", description="As printed")
    amount: float | None = None
    unit: str | None = Field(None, description="As printed")
    nutrient: TrackedNutrientKey | None = None

    @field_validator("name", mode="before")
    @classmethod
    def _name(cls, value: Any) -> str:
        return _trimmed(value, 200) or ""

    @field_validator("unit", mode="before")
    @classmethod
    def _unit(cls, value: Any) -> str | None:
        return _trimmed(value, 40)

    @field_validator("amount", mode="before")
    @classmethod
    def _non_negative(cls, value: Any) -> Any:
        return _amount(value)


class SupplementLabelMacros(BaseModel):
    model_config = ConfigDict(extra="forbid")

    calories_kcal: float | None = None
    protein_g: float | None = None
    carbs_g: float | None = None
    fat_g: float | None = None

    @field_validator("*", mode="before")
    @classmethod
    def _non_negative(cls, value: Any) -> Any:
        return _amount(value)


class SupplementLabelResponse(BaseModel):
    """One supplement label; amounts are per serving of ``serving_units`` units."""

    model_config = ConfigDict(extra="forbid")

    is_supplement_label: bool = Field(
        ..., description="A Supplement Facts or nutrient table is visible and readable"
    )
    product_kind: ProductKind = "supplement"
    brand: str | None = None
    product_name: str = ""
    form: LabelForm = "tablet"
    serving_units: float = Field(1, description="Units the amounts are for")
    serving_grams: float | None = None
    rows: list[SupplementLabelRow] = Field(default_factory=list)
    macros_per_serving: SupplementLabelMacros | None = None
    barcode: str | None = None
    confidence: float = 0.5

    @field_validator("brand", mode="before")
    @classmethod
    def _brand(cls, value: Any) -> str | None:
        return _trimmed(value, 100)

    @field_validator("product_name", mode="before")
    @classmethod
    def _name(cls, value: Any) -> str:
        return _trimmed(value, 200) or _DEFAULT_NAME

    @field_validator("barcode", mode="before")
    @classmethod
    def _barcode(cls, value: Any) -> str | None:
        return _trimmed(value, 20)

    @field_validator("serving_units", mode="before")
    @classmethod
    def _units(cls, value: Any) -> Any:
        # A serving is at least one unit's worth; nonsense falls back to one.
        if not isinstance(value, int | float) or not 0 < value <= 1000:
            return 1
        return value

    @field_validator("serving_grams", mode="before")
    @classmethod
    def _grams(cls, value: Any) -> Any:
        if isinstance(value, int | float) and not 0 < value <= 2000:
            return None
        return value

    @field_validator("rows", mode="after")
    @classmethod
    def _rows(cls, rows: list[SupplementLabelRow]) -> list[SupplementLabelRow]:
        return [row for row in rows if row.name][:_MAX_ROWS]

    @field_validator("confidence", mode="before")
    @classmethod
    def _confidence(cls, value: Any) -> Any:
        if isinstance(value, int | float):
            return min(max(value, 0), 1)
        return value
