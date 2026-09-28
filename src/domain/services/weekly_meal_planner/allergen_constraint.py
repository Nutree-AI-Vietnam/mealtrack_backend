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


_ALLERGEN_ALIASES = {
    "nut": {
        "peanut",
        "peanuts",
        "tree_nut",
        "tree_nuts",
        "almond",
        "cashew",
        "hazelnut",
        "walnut",
        "pecan",
        "pistachio",
    },
    "nuts": {
        "peanut",
        "peanuts",
        "tree_nut",
        "tree_nuts",
        "almond",
        "cashew",
        "hazelnut",
        "walnut",
        "pecan",
        "pistachio",
    },
    "dairy": {"milk", "dairy", "lactose"},
    "shellfish": {"shellfish", "crustacean", "mollusk", "shrimp", "crab", "lobster"},
    "gluten": {"gluten", "wheat", "barley", "rye"},
}


def resolve_allergen_preferences(
    preferences: Iterable[str], known_codes: Iterable[str]
) -> tuple[str, ...] | None:
    """Map common saved preference aliases to known codes or fail closed."""
    known = {
        normalize_allergen_code(str(code)): str(code).strip()
        for code in known_codes
        if str(code).strip()
    }
    resolved: set[str] = set()
    for value in preferences:
        cleaned = re.sub(r"\ballerg(?:y|ic)\b", "", str(value), flags=re.IGNORECASE)
        normalized = normalize_allergen_code(cleaned)
        if not normalized:
            continue
        candidates = set(_ALLERGEN_ALIASES.get(normalized, {normalized}))
        if normalized.endswith("s") and not normalized.endswith("ss"):
            candidates.add(normalized[:-1])
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
