"""A supplement named in a Ghi nhanh sentence that the person never added.

It is saved from the model's guess as the person's own private product,
marked estimated: it counts in the day's totals, never warns and is never
offered to the catalog (Tony, 1 Nov). Scanning its label replaces it.
"""

from __future__ import annotations

from collections.abc import Iterable

from src.domain.model.nutrition.upper_limits import upper_limits_for
from src.domain.model.supplement.dose_math import SupplementNutrients
from src.domain.model.supplement.label_scan import UNIT_LABELS
from src.domain.model.supplement.label_units import nutrients_from_label
from src.domain.model.supplement.supplement_product import (
    LabelRow,
    ProductSource,
    ReviewStatus,
    SupplementForm,
    SupplementProduct,
)

# A guess this many times above the adult upper limit is a unit slip (mg read
# as µg), not a real pill: high-dose D3 is about 2.5 times the limit.
_MISREAD_FACTOR = 10
# Nutrients with no upper limit get a plain per-unit ceiling, well above the
# strongest pill sold (B12 5,000 µg, B1 and B2 a few hundred mg, K2 200 µg,
# an effervescent tablet's sodium) and far below a thousandfold slip.
_NO_LIMIT_CEILINGS = {
    "thiamin": 1_000.0,
    "riboflavin": 1_000.0,
    "vitamin_b12": 10_000.0,
    "vitamin_k": 10_000.0,
    "potassium": 2_000.0,
    "sodium": 2_000.0,
}
_CEILINGS = {
    **_NO_LIMIT_CEILINGS,
    **{
        key: limit.value * _MISREAD_FACTOR
        for key, limit in upper_limits_for(30, male=True).items()
    },
}
# Supplements carry folic acid, which the label rules convert to µg DFE.
_ROW_NAMES = {"folate": "Folic acid"}


def estimate_row(nutrient: str, amount: float, unit: str) -> LabelRow:
    """One guessed nutrient as a label row, named so the unit rules apply."""
    name = _ROW_NAMES.get(nutrient, nutrient.replace("_", " ").title())
    return LabelRow(name, amount, unit, nutrient)


def estimated_product(
    *,
    product_id: str,
    owner_user_id: str,
    name: str,
    form: str,
    rows: Iterable[LabelRow],
) -> SupplementProduct:
    """The person's private product for one unit of the guess."""
    kept = tuple(rows)
    shape = SupplementForm(form)
    return SupplementProduct(
        id=product_id,
        name=name,
        form=shape,
        unit_label=UNIT_LABELS[shape],
        source=ProductSource.DESCRIBE,
        review_status=ReviewStatus.PRIVATE,
        owner_user_id=owner_user_id,
        nutrients=_without_misreads(nutrients_from_label(kept, 1.0)),
        label_rows=kept,
        estimated=True,
    )


def _without_misreads(nutrients: SupplementNutrients) -> SupplementNutrients:
    amounts = {
        key: value
        for key, value in nutrients.amounts.items()
        if value <= _CEILINGS.get(key, float("inf"))
    }
    return SupplementNutrients(
        amounts=amounts,
        vitamin_a_preformed=(
            nutrients.vitamin_a_preformed if "vitamin_a" in amounts else None
        ),
    )
