"""GET /v1/recaps/latest — the user's newest weekly recap ("Weekly Wrapped")."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any, cast
from uuid import UUID

from src.api.exceptions import ResourceNotFoundException
from src.app.events.base import EventHandler, handles
from src.app.handlers.query_handlers.get_progress_summary_query_handler import (
    GetProgressSummaryQueryHandler,
)
from src.app.queries.progress import GetLatestWeeklyRecapQuery, GetProgressSummaryQuery
from src.app.services.weekly_recap_stats_builder import build_week_stats
from src.app.services.weekly_recap_week_logs import load_week_logs
from src.domain.model.weekly_recap import (
    WeeklyRecap,
    week_stats_from_dict,
    week_stats_to_dict,
)
from src.domain.ports.async_unit_of_work_port import AsyncUnitOfWorkPort
from src.domain.services.weekly_recap import (
    RecapConfig,
    generate_recap,
    latest_week_start,
)
from src.domain.utils.timezone_utils import (
    ensure_utc,
    get_zone_info,
    resolve_user_timezone_async,
    utc_now,
)

logger = logging.getLogger(__name__)

# Recaps come out Monday 08:00 local. The app schedules around the same
# hour (RecapSchedule.publishHour), so change both together.
PUBLISH_HOUR = 8


@handles(GetLatestWeeklyRecapQuery)
class GetLatestWeeklyRecapQueryHandler(
    EventHandler[GetLatestWeeklyRecapQuery, dict[str, Any] | None]
):
    """The newest published week's recap, built on first read and then kept.

    A week's recap comes out the following Monday at ``PUBLISH_HOUR`` local
    time. The first read after that builds it from the week's logs and stores
    it; later reads return the stored story, re-rendered from the stored
    numbers when asked for in another language. ``None`` when the week has
    nothing to recap.
    """

    def __init__(
        self,
        uow: AsyncUnitOfWorkPort | None = None,
        uow_factory: Any = None,
        summary_handler: GetProgressSummaryQueryHandler | None = None,
        config: RecapConfig | None = None,
        now_fn: Callable[[], datetime] = utc_now,
    ):
        self.uow_factory: Any = uow_factory or (lambda: uow)
        self.summary_handler = summary_handler or GetProgressSummaryQueryHandler()
        self.config = config or RecapConfig()
        self.now_fn = now_fn

    async def handle(self, query: GetLatestWeeklyRecapQuery) -> dict[str, Any] | None:
        now = cast(datetime, ensure_utc(self.now_fn()))
        async with self.uow_factory() as uow:
            tz_name = await resolve_user_timezone_async(
                query.user_id, uow, query.header_timezone
            )
            local_now = now.astimezone(get_zone_info(tz_name))
            week_start = latest_week_start(local_now, PUBLISH_HOUR)
            stored = await uow.weekly_recaps.get(query.user_id, week_start)
            if stored is not None:
                return self._render(stored, query.locale)
            user = await uow.users.find_by_id(UUID(query.user_id))
            if user is None:
                raise ResourceNotFoundException("User not found")
            logs = await load_week_logs(
                uow,
                query.user_id,
                week_start,
                tz_name,
                user.created_at,
                self.config.min_week_days,
            )
        if logs is None:
            return None
        summary = await self.summary_handler.handle(
            GetProgressSummaryQuery(
                user_id=query.user_id,
                start_date=week_start,
                end_date=week_start + timedelta(days=6),
                header_timezone=query.header_timezone,
            )
        )
        stats = build_week_stats(logs, list(summary.get("days") or []), self.config)
        if stats is None:
            return None
        payload = generate_recap(stats, self.config, query.locale)
        recap = WeeklyRecap(
            user_id=query.user_id,
            week_start=week_start,
            case_id=payload["case_id"],
            locale=query.locale,
            stats=week_stats_to_dict(stats),
            payload=payload,
        )
        async with self.uow_factory() as uow:
            if await uow.weekly_recaps.insert_if_absent(recap):
                return payload
            # Another request stored this week first; everyone gets that one.
            stored = await uow.weekly_recaps.get(query.user_id, week_start)
        return self._render(stored, query.locale) if stored is not None else payload

    def _render(self, recap: WeeklyRecap, locale: str) -> dict[str, Any]:
        """The stored story, in ``locale`` when it can be told the same way."""
        if recap.locale == locale:
            return recap.payload
        try:
            payload = generate_recap(
                week_stats_from_dict(recap.stats), self.config, locale
            )
        except (KeyError, TypeError, ValueError):
            logger.warning(
                "Weekly recap %s for week %s could not be re-rendered in %s",
                recap.user_id,
                recap.week_start,
                locale,
                exc_info=True,
            )
            return recap.payload
        # A config change since storing can pick another story. The stored one
        # stands, so keep its words rather than tell a different week.
        if payload["case_id"] != recap.case_id:
            return recap.payload
        return payload
