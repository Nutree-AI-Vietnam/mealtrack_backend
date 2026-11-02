"""Printed label amounts to the app's canonical nutrient units.

Only fixed conversions are applied: mass units; IU for vitamin D (40 IU per
µg), vitamin A (0.3 µg RAE per IU, the FDA's factor for retinol and for
supplemental beta-carotene) and vitamin E (only when the label names d- or
dl-alpha tocopherol); folic acid to µg DFE (x1.7). Anything that would need a guess
stays an untracked label row, so a product never claims more than its label.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable

from src.domain.model.nutrition.nutrient_units import convert_nutrient_amount
from src.domain.model.supplement.dose_math import (
    TRACKED_NUTRIENTS,
    SupplementNutrients,
)
from src.domain.model.supplement.supplement_product import LabelRow

_PREFORMED = re.compile(r"retinol|retinyl|retinal")
_CAROTENOID = re.compile(r"carotene|carotenoid")
_FOLIC = re.compile(r"folic|methylfolate|tetrahydrofolate|folinate|mthf")
_D_ALPHA = re.compile(r"\bd-alpha|\brrr-alpha|natural")
_DL_ALPHA = re.compile(r"\bdl-alpha|all-rac|synthetic")
# "40% as beta-carotene": a split the label states but this module does not parse.
_PART_PERCENT = re.compile(r"\b(?!100\s*%)\d+(?:[.,]\d+)?\s*%")
# "mcg RAE", "mg α-TE", "mcg DFE", "mg NE": the qualifier names the basis the
# label already used, so the mass part converts as is.
_QUALIFIERS = re.compile(r"\s*(rae|re|dfe|ne|α-te|alpha-te|a-te|te)$")


def _unit(unit: str | None) -> str:
    u = (unit or "").strip().casefold().replace("μ", "u").replace("µ", "u")
    return re.sub(r"\s+", " ", u)


def preformed_share(form: str) -> float | None:
    """1.0 when vitamin A is all retinol or retinyl esters, 0.0 when all
    carotenoids, None when the label mixes them, splits them by percentage or
    names no form."""
    text = form.casefold()
    if _PART_PERCENT.search(text):
        return None
    preformed, carotenoid = (
        bool(_PREFORMED.search(text)),
        bool(_CAROTENOID.search(text)),
    )
    if preformed != carotenoid:
        return 1.0 if preformed else 0.0
    return None


def _from_iu(nutrient: str, amount: float, form: str) -> float | None:
    text = form.casefold()
    if nutrient == "vitamin_d":
        return amount / 40
    if nutrient == "vitamin_a":
        return amount * 0.3
    if nutrient == "vitamin_e":
        if _DL_ALPHA.search(text):
            return amount * 0.45
        if _D_ALPHA.search(text):
            return amount * 0.67
    return None


def canonical_amount(
    nutrient: str, amount: float, unit: str | None, form: str = ""
) -> float | None:
    """The amount in the nutrient's canonical unit, or None when it would be a guess.

    ``form`` is the printed row text ("Vitamin A (as retinyl palmitate)").
    """
    if nutrient not in TRACKED_NUTRIENTS or not math.isfinite(amount) or amount < 0:
        return None
    u = _unit(unit)
    if not u:
        return None
    if u in ("iu", "ui"):
        return _from_iu(nutrient, amount, form)
    qualified = _QUALIFIERS.search(u)
    mass_unit = _QUALIFIERS.sub("", u)
    if nutrient == "folate" and not (qualified and qualified.group(1) == "dfe"):
        if not _FOLIC.search(form.casefold()):
            return None  # food folate without a DFE basis: no safe factor
        amount *= 1.7
    return convert_nutrient_amount(amount, mass_unit, nutrient)


def nutrients_from_label(
    rows: Iterable[LabelRow], units_per_serving: float
) -> SupplementNutrients:
    """Per-unit nutrients from a label's rows for one serving of ``units_per_serving`` units.

    Rows without a tracked ``nutrient`` (iodine, omega-3, creatine) and rows
    whose unit cannot be converted stay label rows only.
    """
    if not math.isfinite(units_per_serving) or units_per_serving <= 0:
        raise ValueError("units_per_serving must be more than zero")
    totals: dict[str, float] = {}
    preformed: float | None = 0.0
    for row in rows:
        if row.nutrient is None or row.amount is None:
            continue
        value = canonical_amount(row.nutrient, row.amount, row.unit, row.name)
        if not value:
            continue  # unconvertible, or "0 mg" printed for a nutrient it lacks
        totals[row.nutrient] = totals.get(row.nutrient, 0.0) + value
        if row.nutrient == "vitamin_a" and preformed is not None:
            share = preformed_share(row.name)
            preformed = None if share is None else preformed + value * share
    # Per-unit amounts keep full precision; only a dose is rounded, so three
    # tablets of a 55 µg-per-3 label log 55 µg, not 54.9999.
    per_unit = {key: value / units_per_serving for key, value in totals.items()}
    return SupplementNutrients(
        amounts=per_unit,
        vitamin_a_preformed=(
            preformed / units_per_serving
            if "vitamin_a" in per_unit and preformed is not None
            else None
        ),
    )
