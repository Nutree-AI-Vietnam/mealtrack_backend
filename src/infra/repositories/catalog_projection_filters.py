"""SQL filters over stored Python casefold values, with canonical allergens."""

from sqlalchemy import exists, false, select

from src.domain.model.weekly_meal_planner import SLOT_MEAL_TYPES
from src.domain.services.weekly_meal_planner.allergen_constraint import (
    normalize_allergen_code,
)
from src.infra.database.models.meal_recommendation.catalog_projection import (
    MealCatalogProjectionAllergenORM as Allergen,
)
from src.infra.database.models.meal_recommendation.catalog_projection import (
    MealCatalogProjectionORM as Projection,
)


def _contains(column, value: str):
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return column.like(f"%{escaped}%", escape="\\")


def recipe_filters(
    *,
    query=None,
    diet=None,
    max_cook_time=None,
    cuisine=None,
    meal_type=None,
    dislikes=(),
    allergen_codes=(),
):
    clauses = []
    needle = (query or "").strip().casefold()
    if needle:
        clauses.append(_contains(Projection.search_text, needle))
    if cuisine and cuisine.strip():
        clauses.append(Projection.cuisine_casefold == cuisine.strip().casefold())
    if meal_type is not None:
        if meal_type not in {"breakfast", "lunch", "dinner", "snack"}:
            raise ValueError(f"Unsupported meal_type: {meal_type}")
        clauses.append(getattr(Projection, f"{meal_type}_eligible").is_(True))
        if meal_type in SLOT_MEAL_TYPES:
            clauses.append(Projection.non_meal.is_(False))
    if diet and diet != "any":
        if diet == "vegetarian":
            clauses.append(Projection.browse_vegetarian.is_(True))
        elif diet == "no-pork":
            clauses.append(Projection.browse_no_pork.is_(True))
        else:
            clauses.append(false())
    if max_cook_time is not None:
        clauses.append(Projection.total_minutes <= max_cook_time)
    clauses.extend(
        ~_contains(Projection.browse_constraint_text, term.strip().casefold())
        for term in dislikes
        if term.strip()
    )
    codes = tuple(normalize_allergen_code(code) for code in allergen_codes)
    if codes:
        linked = select(Allergen.catalog_meal_id).where(
            Allergen.catalog_meal_id == Projection.catalog_meal_id
        )
        clauses.extend(
            [exists(linked), ~exists(linked.where(Allergen.code_normalized.in_(codes)))]
        )
    return clauses


def recipe_order(*, postgres: bool = True):
    """C collation matches Python Unicode code point ordering in PostgreSQL."""
    name = (
        Projection.name_casefold.collate("C") if postgres else Projection.name_casefold
    )
    identity = (
        Projection.catalog_meal_id.collate("C")
        if postgres
        else Projection.catalog_meal_id
    )
    return (
        Projection.popularity_rank.is_(None),
        Projection.popularity_rank,
        name,
        identity,
    )
