"""Responses for POST /v1/hydration/describe and /v1/movement/describe."""

from pydantic import BaseModel, Field


class DescribedDrinkEntry(BaseModel):
    """One saved drink, with what the app needs to cache it without a catalog drink."""

    id: str
    drink_id: str = Field(..., description='Catalog id, or "other" for unlisted drinks')
    name: str = Field(
        ..., description="Localised catalog name, or the person's words when unlisted"
    )
    volume_ml: int
    credited_ml: int
    protein_g: float
    carbs_g: float
    fat_g: float
    fiber_g: float
    sugar_g: float
    kcal: float = Field(..., description="Derived from the macros")
    logged_as_caloric: bool = Field(
        ..., description="False for water-category drinks, which also save a meal"
    )
    logged_at: str
    meal_id: str | None = Field(None, description="Legacy meal id of a water drink")


class DescribeHydrationResponse(BaseModel):
    entries: list[DescribedDrinkEntry] = Field(..., description="In sentence order")


class DescribedMovementEntry(BaseModel):
    """One saved activity, in the POST /v1/movement/log entry shape."""

    id: str
    activity_id: str | None = Field(None, description="Absent for unlisted activities")
    activity_name: str
    duration_min: int
    kcal_burned: float
    intensity: str
    source: str
    include_in_balance: bool
    logged_at: str | None


class DescribeMovementResponse(BaseModel):
    entries: list[DescribedMovementEntry] = Field(..., description="In sentence order")
