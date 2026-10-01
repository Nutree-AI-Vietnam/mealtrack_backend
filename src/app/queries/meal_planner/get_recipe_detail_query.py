from dataclasses import dataclass

from src.app.events.base import Query


@dataclass
class GetRecipeDetailQuery(Query):
    recipe_id: str
    # Kept for compatibility with clients of the deprecated POST route. Detail
    # queries are read-only and never start micronutrient enrichment.
    enrich_micronutrients: bool = False
    include_cached_micronutrients: bool = True
