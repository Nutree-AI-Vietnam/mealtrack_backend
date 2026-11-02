from dataclasses import dataclass

from src.app.events.base import Query


@dataclass
class GetRecipeDetailsQuery(Query):
    """Recipe details for several ids, in request order; unknown ids are left out."""

    recipe_ids: tuple[str, ...]
