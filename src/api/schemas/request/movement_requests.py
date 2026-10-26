"""Movement request schemas."""

from typing import Optional

from pydantic import BaseModel, Field


class UpdateMovementRequest(BaseModel):
    duration_min: int = Field(..., ge=1, le=600)
    kcal_burned: float = Field(..., ge=0)
    intensity: str
    include_in_balance: bool


class LogMovementRequest(BaseModel):
    activity_id: Optional[str] = Field(None, min_length=1, max_length=64)
    activity_name: str = Field(..., min_length=1, max_length=100)
    duration_min: int = Field(..., ge=1, le=600)
    kcal_burned: float = Field(..., ge=0)
    intensity: str
    include_in_balance: bool
    target_date: Optional[str] = None


class DescribeMovementRequest(BaseModel):
    """One sentence listing the activities to log (Ghi nhanh)."""

    text: str = Field(..., min_length=1, max_length=300)
    target_date: str | None = None
    weight_kg: float | None = Field(
        None,
        description="The weight the app prices activities with; clamped to 30-300 kg",
    )
