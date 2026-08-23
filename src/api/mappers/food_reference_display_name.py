"""Pure resolver for catalog display names by request language.

No DB access — operates only on already-loaded display projections
(``{"name": str, "name_vi": str | None}``).
"""

from typing import Any

from src.domain.constants.languages import normalize_language


def resolve_food_reference_display_name(
    projection: dict[str, Any],
    language: str | None,
) -> str:
    """Resolve one food-reference display name for the requested language.

    English (or an empty/unrecognized locale) always returns the catalog's
    canonical English name. Vietnamese uses ``name_vi`` when present, then
    falls back to English. A missing ``name_vi`` never triggers a live
    translate-on-read.
    """
    name = str(projection.get("name") or "")
    normalized = normalize_language(language)
    if normalized != "vi":
        return name

    name_vi = projection.get("name_vi")
    if name_vi:
        return str(name_vi)
    return name
