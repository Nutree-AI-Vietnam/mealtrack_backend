"""Request schemas for restoring missed streak days."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field


class PackClaimRequest(BaseModel):
    """A restore pack the app just bought."""

    product_id: str = Field(min_length=1, max_length=128)
    transaction_id: str = Field(min_length=1, max_length=255)


class StreakRestoreRequest(BaseModel):
    """Days to restore (ascending, at most 2) and an optional just-bought pack.

    ``dates`` may be empty only with a purchase: the pack is then credited.
    """

    dates: list[date] = Field(default_factory=list)
    purchase: PackClaimRequest | None = None
