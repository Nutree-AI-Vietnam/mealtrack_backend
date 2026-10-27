"""Return the open vacation, or the one whose last day was yesterday."""

from datetime import timedelta
from typing import Any

from src.app.events.base import EventHandler, handles
from src.app.queries.vacation.get_vacation_query import GetVacationQuery
from src.app.services.vacation_support import vacation_payload
from src.domain.services.vacation_rules import open_vacation, status_for, to_window
from src.domain.utils.timezone_utils import resolve_user_timezone_async, user_today
from src.infra.database.uow_async import AsyncUnitOfWork


@handles(GetVacationQuery)
class GetVacationQueryHandler(EventHandler[GetVacationQuery, dict[str, Any]]):
    async def handle(self, query: GetVacationQuery) -> dict[str, Any]:
        async with AsyncUnitOfWork() as uow:
            user_tz = await resolve_user_timezone_async(query.user_id, uow)
            today = user_today(user_tz)
            vacations = await uow.vacations.find_by_user(query.user_id)

        current = open_vacation(vacations, today)
        if current is None:
            current = _ended_yesterday(vacations, today)
        if current is None:
            return {"vacation": None}
        return {"vacation": vacation_payload(current, today)}


def _ended_yesterday(vacations, today):
    yesterday = today - timedelta(days=1)
    for vacation in vacations:
        window = to_window(vacation)
        if window.end == yesterday and status_for(window, today) == "ended":
            return vacation
    return None
