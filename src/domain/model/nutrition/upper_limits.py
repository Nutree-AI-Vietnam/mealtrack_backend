"""Tolerable upper intakes: above these, a nutrient warns instead of staying green.

Values come from the 2016 Vietnamese book (see ``vn_nutrient_tables``) where it
has one. Vitamin C, zinc and magnesium have none there, so the US Institute of
Medicine limits fill in, on the US age bands. No limit exists for vitamin K,
thiamin, riboflavin, vitamin B12 or potassium.

Scope says which intake the limit covers. Vitamin A counts preformed retinol
only (Table 27). Magnesium, niacin, folate and vitamin E count supplements and
fortified foods only, per the US notes, so food totals never trip them.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from src.domain.model.nutrition.vn_nutrient_tables import AgeTable, lookup


class UpperLimitScope(StrEnum):
    ALL_SOURCES = "all_sources"
    SUPPLEMENTS_ONLY = "supplements_only"
    PREFORMED_ONLY = "preformed_only"


@dataclass(frozen=True)
class UpperLimit:
    value: float
    scope: UpperLimitScope


_ALL = UpperLimitScope.ALL_SOURCES
_SUPPLEMENTS = UpperLimitScope.SUPPLEMENTS_ONLY

# Each entry: (age table, scope). Rows as in ``vn_nutrient_tables``.
_UPPER_LIMIT_TABLES: Final[dict[str, tuple[AgeTable, UpperLimitScope]]] = {
    # Table 27 (µg RAE).
    "vitamin_a": (
        ((14, 2100, 2100), (17, 2600, 2600), (None, 2700, 2700)),
        UpperLimitScope.PREFORMED_ONLY,
    ),
    # Table 28 (µg).
    "vitamin_d": (((None, 100, 100),), _ALL),
    # Table 29 (mg).
    "vitamin_e": (
        (
            (14, 650, 600),
            (17, 750, 650),
            (29, 800, 650),
            (49, 900, 700),
            (69, 850, 700),
            (None, 750, 650),
        ),
        _SUPPLEMENTS,
    ),
    # Table 35 (mg).
    "niacin": (((14, 20, 20), (19, 30, 30), (None, 35, 35)), _SUPPLEMENTS),
    # Table 37 (mg).
    "vitamin_b6": (((14, 60, 60), (19, 80, 80), (None, 100, 100)), _ALL),
    # Table 38 (µg DFE).
    "folate": (((14, 600, 600), (19, 800, 800), (None, 1000, 1000)), _SUPPLEMENTS),
    # Table 16 (mg).
    "calcium": (((19, 3000, 3000), (49, 2500, 2500), (None, 2000, 2000)), _ALL),
    # Table 22 (µg).
    "selenium": (
        (
            (14, 330, 320),
            (19, 400, 350),
            (29, 420, 330),
            (49, 460, 350),
            (69, 440, 350),
            (None, 400, 330),
        ),
        _ALL,
    ),
    # Iron chapter text: 45 mg for adolescents and adults (IOM 2006).
    "iron": (((None, 45, 45),), _ALL),
    # US IOM, ages 9–13, 14–18 and adults (mg).
    "vitamin_c": (((13, 1200, 1200), (18, 1800, 1800), (None, 2000, 2000)), _ALL),
    "zinc": (((13, 23, 23), (18, 34, 34), (None, 40, 40)), _ALL),
    "magnesium": (((None, 350, 350),), _SUPPLEMENTS),
}

# Table 17 sets 3,000 mg from age 20 and no limit before it.
_PHOSPHORUS_LIMIT_FROM_AGE: Final = 20
_PHOSPHORUS_LIMIT_MG: Final = 3000.0


def upper_limits_for(age: int, male: bool) -> dict[str, UpperLimit]:
    limits = {
        key: UpperLimit(lookup(table, age, male), scope)
        for key, (table, scope) in _UPPER_LIMIT_TABLES.items()
    }
    if age >= _PHOSPHORUS_LIMIT_FROM_AGE:
        limits["phosphorus"] = UpperLimit(_PHOSPHORUS_LIMIT_MG, _ALL)
    return limits
