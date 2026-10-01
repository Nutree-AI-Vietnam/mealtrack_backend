"""Hard planner exclusion uses normalized allergen codes only.

Free-text allergen copy on a recipe is informational. Matching a code excludes
the recipe from planning. It is not a medical-grade safety guarantee.
"""

from __future__ import annotations

import re
from collections.abc import Iterable


def normalize_allergen_code(value: str) -> str:
    return value.strip().casefold().replace("-", "_").replace(" ", "_")


def matched_allergen_codes(
    disclosures: Iterable[str], known_codes: Iterable[str]
) -> tuple[str, ...]:
    """Keep disclosures that are the same code as an allergen reference row."""

    catalog: dict[str, str] = {}
    for code in known_codes:
        normalized = normalize_allergen_code(str(code))
        if normalized:
            catalog.setdefault(normalized, str(code).strip())
    matched: list[str] = []
    seen: set[str] = set()
    for disclosure in disclosures:
        code = catalog.get(normalize_allergen_code(str(disclosure)))
        if code is None or code in seen:
            continue
        seen.add(code)
        matched.append(code)
    return tuple(matched)


_TREE_NUT_ALIASES = (
    "almond",
    "almonds",
    "cashew",
    "cashews",
    "hazelnut",
    "hazelnuts",
    "walnut",
    "walnuts",
    "pecan",
    "pecans",
    "pistachio",
    "pistachios",
)

_TREE_NUT_CODES = {"tree_nut", "tree_nuts", *_TREE_NUT_ALIASES}

_ALLERGEN_ALIASES = {
    "nut": {
        "peanut",
        "peanuts",
        *_TREE_NUT_CODES,
    },
    "nuts": {
        "peanut",
        "peanuts",
        *_TREE_NUT_CODES,
    },
    **{alias: {"tree_nut", "tree_nuts", alias} for alias in _TREE_NUT_ALIASES},
    "dairy": {"milk", "dairy", "lactose"},
    "lactose": {"milk", "dairy", "lactose"},
    "shellfish": {"shellfish", "crustacean", "mollusk", "shrimp", "crab", "lobster"},
    "crustacean": {"shellfish", "crustacean"},
    "mollusk": {"shellfish", "mollusk"},
    "shrimp": {"shellfish", "shrimp"},
    "crab": {"shellfish", "crab"},
    "lobster": {"shellfish", "lobster"},
    "gluten": {"gluten", "wheat", "barley", "rye"},
}


def resolve_allergen_preferences(
    preferences: Iterable[str], known_codes: Iterable[str]
) -> tuple[str, ...] | None:
    """Map common saved preference aliases to known codes or fail closed."""
    known: dict[str, str] = {}
    for raw_code in known_codes:
        code = str(raw_code).strip()
        normalized = normalize_allergen_code(code)
        if not normalized:
            continue
        existing = known.get(normalized)
        if existing is None or (code == normalized and existing != normalized):
            known[normalized] = code
    resolved: set[str] = set()
    for value in preferences:
        cleaned = re.sub(r"\ballerg(?:y|ic)\b", "", str(value), flags=re.IGNORECASE)
        normalized = normalize_allergen_code(cleaned)
        if not normalized:
            continue
        singular = (
            normalized[:-1]
            if normalized.endswith("s") and not normalized.endswith("ss")
            else normalized
        )
        candidates = set(
            _ALLERGEN_ALIASES.get(normalized)
            or _ALLERGEN_ALIASES.get(singular)
            or {normalized}
        )
        if singular != normalized:
            candidates.add(singular)
        matches = {known[code] for code in candidates if code in known}
        if not matches:
            return None
        resolved.update(matches)
    return tuple(sorted(resolved))


def recipe_excluded_by_allergen(
    recipe_codes: Iterable[str], user_allergies: Iterable[str]
) -> bool:
    recipe = {
        normalize_allergen_code(code) for code in recipe_codes if str(code).strip()
    }
    requested = {
        normalize_allergen_code(code) for code in user_allergies if str(code).strip()
    }
    if not requested:
        return False
    # Missing recipe disclosure data is unknown, not evidence of safety.
    if not recipe:
        return True
    return bool(recipe & requested)
