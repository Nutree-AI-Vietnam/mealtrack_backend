from dataclasses import dataclass

from src.app.events.base import Query


@dataclass
class GetRecipeSummariesQuery(Query):
    recipe_ids: tuple[str, ...]
