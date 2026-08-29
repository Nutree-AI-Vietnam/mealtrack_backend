"""Handler for reading leftover-split daily adjustment preference."""

from src.app.events.base import EventHandler, handles
from src.app.queries.user.get_weekly_auto_adjust_query import GetWeeklyAutoAdjustQuery


@handles(GetWeeklyAutoAdjustQuery)
class GetWeeklyAutoAdjustQueryHandler(
    EventHandler[GetWeeklyAutoAdjustQuery, dict[str, bool]]
):
    """Return the persisted leftover-split flag. Missing rows stay enabled."""

    def __init__(self, uow_factory):
        self.uow_factory = uow_factory

    async def handle(self, query: GetWeeklyAutoAdjustQuery) -> dict[str, bool]:
        async with self.uow_factory() as uow:
            enabled = await uow.users.get_weekly_auto_adjust(query.user_id)
        return {"enabled": enabled is not False}
