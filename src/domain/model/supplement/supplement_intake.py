"""A person's supplement list and the doses they take."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime

from src.domain.model.supplement.dose_math import SupplementNutrients, dose
from src.domain.model.supplement.supplement_product import SupplementProduct


def _positive(name: str, value: float) -> None:
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be more than zero")


@dataclass(frozen=True)
class UserSupplement:
    """A product on a person's list, with the dose one tap logs."""

    id: str
    user_id: str
    product_id: str
    units_per_dose: float
    active: bool = True
    sort_order: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def __post_init__(self) -> None:
        _positive("units_per_dose", self.units_per_dose)


@dataclass(frozen=True)
class SupplementIntake:
    """One dose taken; ``nutrients`` is the absolute snapshot at log time."""

    id: str
    user_id: str
    product_id: str
    units: float
    taken_at: datetime
    local_date: date
    logged_on_day: bool
    nutrients: SupplementNutrients
    estimated: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None
    deleted_at: datetime | None = None

    def __post_init__(self) -> None:
        _positive("units", self.units)
        if self.taken_at.tzinfo is None:
            raise ValueError("taken_at must carry a timezone")

    @classmethod
    def log(
        cls,
        *,
        id: str,
        user_id: str,
        product: SupplementProduct,
        units: float,
        taken_at: datetime,
        local_date: date,
        logged_on_day: bool,
    ) -> SupplementIntake:
        """A dose of ``product``: its per-unit nutrients times ``units``."""
        return cls(
            id=id,
            user_id=user_id,
            product_id=product.id,
            units=units,
            taken_at=taken_at,
            local_date=local_date,
            logged_on_day=logged_on_day,
            nutrients=dose(product.nutrients, units),
            estimated=product.estimated,
        )
