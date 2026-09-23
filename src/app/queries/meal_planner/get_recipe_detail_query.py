from dataclasses import dataclass

from src.app.events.base import Query


@dataclass
class GetRecipeDetailQuery(Query):
    recipe_id: str
