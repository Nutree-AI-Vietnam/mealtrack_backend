from dataclasses import dataclass

from src.app.events.base import Query


@dataclass
class ListRecipesQuery(Query):
    query: str | None = None
    diet: str | None = None
    max_cook_time: int | None = None
    cuisine: str | None = None
    allergies: tuple[str, ...] = ()
    dislikes: tuple[str, ...] = ()
    limit: int = 20
    offset: int = 0
