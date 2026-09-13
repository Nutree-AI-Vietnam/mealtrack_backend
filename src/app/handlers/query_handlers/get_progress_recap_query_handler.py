"""GET /v1/progress/recap — cached recap for one timeline window."""

from __future__ import annotations

from datetime import date
from typing import Any

from src.app.events.base import EventHandler, handles
from src.app.handlers.query_handlers.get_progress_summary_query_handler import (
    GetProgressSummaryQueryHandler,
)
from src.app.queries.progress.get_progress_recap_query import GetProgressRecapQuery
from src.app.queries.progress.get_progress_summary_query import GetProgressSummaryQuery
from src.domain.cache.cache_keys import CacheKeys
from src.domain.ports.cache_port import CachePort
from src.domain.services.progress_recap_contract import missing_recap
from src.domain.services.progress_recap_facts import build_recap_facts


@handles(GetProgressRecapQuery)
class GetProgressRecapQueryHandler(EventHandler[GetProgressRecapQuery, dict[str, Any]]):
    def __init__(
        self,
        cache_service: CachePort | None = None,
        summary_handler: GetProgressSummaryQueryHandler | None = None,
    ):
        self.cache_service = cache_service
        self.summary_handler = summary_handler or GetProgressSummaryQueryHandler(
            cache_service=cache_service
        )

    async def handle(self, query: GetProgressRecapQuery) -> dict[str, Any]:
        summary = await self.summary_handler.handle(
            GetProgressSummaryQuery(
                user_id=query.user_id,
                start_date=query.start_date,
                end_date=query.end_date,
                header_timezone=query.header_timezone,
            )
        )
        start = date.fromisoformat(summary["effective_start"])
        end = date.fromisoformat(summary["effective_end"])
        facts = build_recap_facts(
            list(summary.get("days") or []),
            horizon=query.horizon,
            start=start,
            end=end,
        )
        if self.cache_service is not None:
            key, _ = CacheKeys.progress_recap(
                query.user_id,
                query.horizon,
                start,
                end,
                query.locale,
                facts.stamp(),
            )
            cached = await self.cache_service.get(key)
            if isinstance(cached, dict) and cached.get("status"):
                return cached
        return missing_recap(query.horizon, start.isoformat(), end.isoformat())
