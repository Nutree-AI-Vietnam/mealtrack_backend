"""POST /v1/progress/recap — AI recap for one timeline window."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime
from typing import Any

from src.app.commands.progress.generate_progress_recap_command import (
    GenerateProgressRecapCommand,
)
from src.app.events.base import EventHandler, handles
from src.app.handlers.query_handlers.get_progress_summary_query_handler import (
    GetProgressSummaryQueryHandler,
)
from src.app.queries.progress.get_progress_summary_query import GetProgressSummaryQuery
from src.domain.cache.cache_keys import CacheKeys
from src.domain.ports.cache_port import CachePort
from src.domain.services.progress_recap_contract import (
    empty_recap,
    fallback_recap,
    parse_ai_recap,
)
from src.domain.services.progress_recap_facts import build_recap_facts
from src.domain.services.progress_recap_prompt import (
    build_system_prompt,
    build_user_prompt,
)

GenerateFn = Callable[[str, str], Awaitable[dict[str, Any]]]


@handles(GenerateProgressRecapCommand)
class GenerateProgressRecapCommandHandler(
    EventHandler[GenerateProgressRecapCommand, dict[str, Any]]
):
    def __init__(
        self,
        cache_service: CachePort | None = None,
        summary_handler: GetProgressSummaryQueryHandler | None = None,
        generate: GenerateFn | None = None,
    ):
        self.cache_service = cache_service
        self.summary_handler = summary_handler or GetProgressSummaryQueryHandler(
            cache_service=cache_service
        )
        self._generate = generate

    async def handle(self, command: GenerateProgressRecapCommand) -> dict[str, Any]:
        summary = await self.summary_handler.handle(
            GetProgressSummaryQuery(
                user_id=command.user_id,
                start_date=command.start_date,
                end_date=command.end_date,
                header_timezone=command.header_timezone,
            )
        )
        start = date.fromisoformat(summary["effective_start"])
        end = date.fromisoformat(summary["effective_end"])
        facts = build_recap_facts(
            list(summary.get("days") or []),
            horizon=command.horizon,
            start=start,
            end=end,
        )
        key, ttl = CacheKeys.progress_recap(
            command.user_id,
            command.horizon,
            start,
            end,
            command.locale,
            facts.stamp(),
        )
        if not command.force:
            cached = await self._read(key)
            if cached and cached.get("status") == "ready":
                return cached
        if facts.logged_days == 0:
            payload = empty_recap(facts)
        else:
            payload = await self._write_copy(facts, command.locale)
        payload["generated_at"] = datetime.now(UTC).isoformat()
        await self._write(key, payload, ttl)
        return payload

    async def _write_copy(self, facts: Any, locale: str) -> dict[str, Any]:
        if self._generate is None:
            return fallback_recap(facts, locale)
        try:
            raw = await self._call_ai(facts, locale)
            return parse_ai_recap(raw, facts) or fallback_recap(facts, locale)
        except Exception:
            return fallback_recap(facts, locale)

    async def _call_ai(self, facts: Any, locale: str) -> dict[str, Any]:
        if self._generate is None:
            raise RuntimeError("progress recap generate is not wired")
        prompt = build_user_prompt(facts, locale)
        system = build_system_prompt(locale)
        return await self._generate(prompt, system)

    async def _read(self, key: str) -> dict[str, Any] | None:
        if self.cache_service is None:
            return None
        cached = await self.cache_service.get(key)
        return cached if isinstance(cached, dict) else None

    async def _write(self, key: str, value: dict[str, Any], ttl: int) -> None:
        if self.cache_service is None:
            return
        await self.cache_service.set(key, value, ttl)
