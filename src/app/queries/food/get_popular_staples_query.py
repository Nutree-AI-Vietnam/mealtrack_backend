"""Query for curated popular staple foods from food_reference."""

from dataclasses import dataclass


# Curated food_reference primary keys (beef, pork, white rice, egg, whole milk).
POPULAR_STAPLE_FOOD_REFERENCE_IDS: tuple[int, ...] = (205, 294, 348, 363, 440)


@dataclass(frozen=True)
class GetPopularStaplesQuery:
    """Load fixed staple foods for the catalog popular list."""

    language: str = "en"
