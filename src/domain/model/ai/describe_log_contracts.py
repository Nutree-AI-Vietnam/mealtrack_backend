"""AI contracts for Ghi nhanh: one sentence that lists drinks or activities.

These contracts are deliberately loose. OpenAI structured outputs drop
min/max/length/default from the schema it receives, and the adapter validates
the reply against these models, so tight bounds would reject whole sentences
(a "2,5 lít" glass would fail instead of being clamped). Shapes and types are
checked here; the describe handlers clamp or drop out-of-range items in code.
"""

from pydantic import BaseModel, ConfigDict, Field


class UnlistedDrinkNutrition(BaseModel):
    """Formula inputs for a drink that is not in the catalog."""

    model_config = ConfigDict(extra="ignore")

    protein_g: float = Field(..., description="Protein grams per 100 ml")
    carbs_g: float = Field(
        ..., description="Carbohydrate grams per 100 ml, including sugar and fiber"
    )
    fat_g: float = Field(..., description="Fat grams per 100 ml")
    sugar_g: float = Field(..., description="Sugar grams per 100 ml")
    fiber_g: float = Field(..., description="Fiber grams per 100 ml")
    hydration_weight: float = Field(
        ...,
        description="Share of the volume that counts as water intake, from 0 to 1",
    )


class DrinkLogItem(BaseModel):
    """One drink read from the sentence."""

    model_config = ConfigDict(extra="ignore")

    catalog_id: str | None = Field(
        None, description="Catalog drink id, or null when no catalog drink matches"
    )
    name: str = Field(..., description="The drink in the person's own words")
    volume_ml_per_serving: float | None = Field(
        None,
        description=(
            "Millilitres in ONE serving, only when a size or amount is said; "
            "never multiplied by servings"
        ),
    )
    servings: float | None = Field(
        None,
        description="Number of servings; fractions allowed (half a glass = 0.5)",
    )
    unlisted: UnlistedDrinkNutrition | None = Field(
        None, description="Per-100 ml estimate, only when catalog_id is null"
    )


class DrinkLogParse(BaseModel):
    """Every drink in the sentence, in sentence order."""

    model_config = ConfigDict(extra="ignore")

    items: list[DrinkLogItem] = Field(
        default_factory=list, description="Empty when the sentence names no drink"
    )


class ActivityLogItem(BaseModel):
    """One activity read from the sentence."""

    model_config = ConfigDict(extra="ignore")

    catalog_id: str | None = Field(
        None,
        description="Catalog activity id, or null when no catalog activity matches",
    )
    name: str = Field(..., description="The activity in the person's own words")
    duration_min: float | None = Field(
        None, description="Minutes, only when the sentence says how long"
    )
    intensity: str | None = Field(
        None, description="light, moderate or hard, only when the sentence says so"
    )
    distance_km: float | None = Field(
        None, description="Kilometres, only when the sentence says a distance"
    )
    met: float | None = Field(
        None,
        description="MET of the activity at the stated effort, only when catalog_id is null",
    )


class ActivityLogParse(BaseModel):
    """Every activity in the sentence, in sentence order."""

    model_config = ConfigDict(extra="ignore")

    items: list[ActivityLogItem] = Field(
        default_factory=list, description="Empty when the sentence names no activity"
    )


class UnlistedActivityEstimate(BaseModel):
    """The MET of one activity the catalog does not list."""

    model_config = ConfigDict(extra="ignore")

    ref: str = Field(..., description="The line's ref, unchanged")
    met: float = Field(
        ...,
        description="MET at the stated effort from the Compendium of Physical Activities",
    )


class UnlistedDrinkEstimate(BaseModel):
    """Per-100 ml values of one drink the catalog does not list."""

    model_config = ConfigDict(extra="ignore")

    ref: str = Field(..., description="The line's ref, unchanged")
    unlisted: UnlistedDrinkNutrition = Field(
        ..., description="The drink's typical values per 100 ml"
    )


class UnlistedEstimateParse(BaseModel):
    """Numbers for the items a first read named but left blank."""

    model_config = ConfigDict(extra="ignore")

    activities: list[UnlistedActivityEstimate] = Field(
        default_factory=list, description="One entry per activity line"
    )
    drinks: list[UnlistedDrinkEstimate] = Field(
        default_factory=list, description="One entry per drink line"
    )
