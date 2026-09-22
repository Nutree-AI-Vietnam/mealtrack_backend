"""Hard planner exclusion uses normalized allergen codes only.

Free-text allergen copy on a recipe is informational. Matching a code excludes
the recipe from planning. It is not a medical-grade safety guarantee.
"""

from __future__ import annotations

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


def recipe_excluded_by_allergen(
    recipe_codes: Iterable[str], user_allergies: Iterable[str]
) -> bool:
    recipe = {normalize_allergen_code(code) for code in recipe_codes if str(code).strip()}
    requested = {
        normalize_allergen_code(code) for code in user_allergies if str(code).strip()
    }
    if not recipe or not requested:
        return False
    return bool(recipe & requested)
