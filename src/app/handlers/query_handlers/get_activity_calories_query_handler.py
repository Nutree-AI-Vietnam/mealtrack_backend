"""Handler for reading the activity calories preference."""

from src.app.events.base import EventHandler, handles
from src.app.queries.user.get_activity_calories_query import GetActivityCaloriesQuery


@handles(GetActivityCaloriesQuery)
class GetActivityCaloriesQueryHandler(
    EventHandler[GetActivityCaloriesQuery, dict[str, bool]]
):
    """Return the persisted activity calories flag. Missing rows stay enabled."""

    def __init__(self, uow_factory):
        self.uow_factory = uow_factory

    async def handle(self, query: GetActivityCaloriesQuery) -> dict[str, bool]:
        async with self.uow_factory() as uow:
            enabled = await uow.users.get_activity_calories(query.user_id)
        return {"enabled": enabled is not False}
