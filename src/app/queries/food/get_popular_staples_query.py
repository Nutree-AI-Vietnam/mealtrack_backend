"""Query for curated popular staple foods from food_reference."""

from dataclasses import dataclass

# Stable FatSecret identities (beef, pork, white rice, egg, whole milk).
# Do not key staples by auto-increment food_reference PKs — those diverge
# across local/staging/prod. Verified via FatSecret food.get.v5 2026-08-25.
POPULAR_STAPLE_SOURCE_IDENTITIES: tuple[tuple[str, str], ...] = (
    ("fatsecret", "1350"),
    ("fatsecret", "1421"),
    ("fatsecret", "4501"),
    ("fatsecret", "3092"),
    ("fatsecret", "794"),
)


@dataclass(frozen=True)
class GetPopularStaplesQuery:
    """Load fixed staple foods for the catalog popular list."""

    language: str = "en"
