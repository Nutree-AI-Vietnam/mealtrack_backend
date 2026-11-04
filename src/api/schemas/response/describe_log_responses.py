"""Responses for the Ghi nhanh routes: water, activity and the + panel."""

from typing import Any, Literal

from pydantic import BaseModel, Field

from src.api.schemas.response.meal_responses import DetailedMealResponse


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


class DescribeAnySavedItem(BaseModel):
    """One saved entry, with the payload its kind's own route returns."""

    kind: Literal["food", "drink", "activity", "supplement"]
    id: str = Field(..., description="Meal, drink entry, activity entry or dose id")
    title: str
    summary: str = Field(..., description='One line, e.g. "520 kcal" or "250 ml"')
    estimated: bool = Field(
        False, description="A supplement saved from the model's guess (ước tính)"
    )
    meal: DetailedMealResponse | None = Field(
        None, description="Food: the meal as POST /v1/meals/manual returns it"
    )
    drink: DescribedDrinkEntry | None = None
    activity: DescribedMovementEntry | None = None
    intake: dict[str, Any] | None = Field(
        None, description="Supplement: the dose, as POST /v1/supplements/intakes"
    )
    entry: dict[str, Any] | None = Field(
        None, description="Supplement: the list row with the day's doses"
    )
    product: dict[str, Any] | None = None


class DescribeAnySkippedItem(BaseModel):
    kind: Literal["food", "drink", "activity", "supplement"]
    title: str
    reason: Literal["not_requested", "supplements_unavailable", "not_recognized"]


class DescribeAnyResponse(BaseModel):
    saved: list[DescribeAnySavedItem] = Field(
        ..., description="Food, then drinks, activities and supplements"
    )
    skipped: list[DescribeAnySkippedItem] = Field(
        ..., description="Read but not saved; never counted as saved"
    )
    nothing_found: bool = Field(
        ..., description="The sentence logged nothing Nutree recognised"
    )


class UndoDescribeAnyResponse(BaseModel):
    request_id: str
    deleted: dict[str, int] = Field(..., description="Entries deleted per kind")
