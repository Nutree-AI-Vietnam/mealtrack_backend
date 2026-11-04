"""Supplement request schemas."""

from typing import Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, Field, model_validator

# A dose of 0.25 (a quarter tablet) up to 100 units (ml of a syrup) covers labels.
_MAX_UNITS = 100


class AddSupplementRequest(BaseModel):
    """Put a product on the list, optionally logging today's dose at once."""

    product_id: str = Field(..., min_length=1, max_length=36)
    units_per_dose: float | None = Field(None, gt=0, le=_MAX_UNITS)
    log_now: bool = False
    intake_id: UUID | None = Field(None, description="Client id; required with log_now")
    taken_at: AwareDatetime | None = Field(None, description="UTC time of the dose")

    @model_validator(mode="after")
    def _dose_needs_an_id(self) -> Self:
        # Without a client id a retried request would log the dose twice.
        if self.log_now and self.intake_id is None:
            raise ValueError("intake_id is required when log_now is true")
        return self


class UpdateSupplementRequest(BaseModel):
    units_per_dose: float | None = Field(None, gt=0, le=_MAX_UNITS)
    active: bool | None = None
    sort_order: int | None = Field(None, ge=0, le=10_000)


class LogSupplementIntakeRequest(BaseModel):
    intake_id: UUID = Field(..., description="Client id; a reused id answers 409")
    product_id: str = Field(..., min_length=1, max_length=36)
    units: float = Field(..., gt=0, le=_MAX_UNITS)
    taken_at: AwareDatetime | None = Field(
        None, description="UTC time with an offset; defaults to now"
    )


class ScanSupplementLabelRequest(BaseModel):
    """An uploaded label photo; the dose is logged under ``intake_id``."""

    # Matches supplement_products.label_image_url.
    image_url: str = Field(..., min_length=1, max_length=512)
    image_id: str = Field(..., min_length=1, max_length=128)
    intake_id: UUID = Field(..., description="Client id for today's dose")
    taken_at: AwareDatetime | None = Field(
        None, description="UTC time with an offset; defaults to now"
    )
