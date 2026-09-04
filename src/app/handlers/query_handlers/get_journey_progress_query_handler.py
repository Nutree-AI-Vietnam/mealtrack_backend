"""Handler for active journey progress snapshots."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any
from uuid import UUID

from src.api.exceptions import ResourceNotFoundException
from src.app.events.base import EventHandler, handles
from src.app.queries.progress import GetJourneyProgressQuery
from src.app.queries.tdee import GetUserTdeeQuery
from src.domain.ports.async_unit_of_work_port import AsyncUnitOfWorkPort
from src.domain.ports.cache_port import CachePort
from src.domain.services.hydration_goal_service import resolve_hydration_goal_ml
from src.domain.services.journey_progress_rules import (
    estimate_timeline_days,
    journey_period_start,
)
from src.domain.services.journey_progress_service import (
    JourneyAction,
    calculate_journey_progress,
)
from src.domain.utils.timezone_utils import (
    ensure_utc,
    get_zone_info,
    resolve_user_timezone_async,
    utc_now,
)

TargetLoader = Callable[[str], Awaitable[tuple[float, float]]]


@handles(GetJourneyProgressQuery)
class GetJourneyProgressQueryHandler(
    EventHandler[GetJourneyProgressQuery, dict[str, Any]]
):
    """Build the server-derived action progress for the active journey period."""

    def __init__(
        self,
        uow: AsyncUnitOfWorkPort,
        cache_service: CachePort | None = None,
        now_fn: Callable[[], Any] = utc_now,
        target_loader: TargetLoader | None = None,
    ):
        self.uow = uow
        self.cache_service = cache_service
        self.now_fn = now_fn
        self.target_loader = target_loader

    async def handle(self, query: GetJourneyProgressQuery) -> dict[str, Any]:
        now = ensure_utc(self.now_fn())
        uow = self.uow

        async with uow:
            user_tz_str = await resolve_user_timezone_async(
                query.user_id, uow, query.header_timezone
            )
            user_tz = get_zone_info(user_tz_str)
            profile = await uow.users.get_profile(UUID(query.user_id))
            if profile is None:
                raise ResourceNotFoundException("Current user profile not found")

            start_weight = profile.goal_start_weight_kg or profile.weight_kg
            timeline_days = estimate_timeline_days(
                goal=profile.fitness_goal,
                start_weight_kg=start_weight,
                target_weight_kg=profile.target_weight_kg,
                challenge_duration=profile.challenge_duration,
            )
            period_start = journey_period_start(
                goal_started_at=profile.goal_started_at,
                user_tz=user_tz,
            )
            period_end = period_start + timedelta(days=timeline_days)
            actions = await self._load_actions(
                uow,
                query.user_id,
                period_start,
                min(now, period_end),
            )
            water_goal_ml = resolve_hydration_goal_ml(profile)

        target_calories, target_protein_g = await self._load_targets(query.user_id)
        return calculate_journey_progress(
            actions=actions,
            period_start=period_start,
            timeline_days=timeline_days,
            user_tz=user_tz,
            as_of=now,
            target_calories=target_calories,
            target_protein_g=target_protein_g,
            water_goal_ml=water_goal_ml,
            journey_progress_seed_percent=getattr(
                profile, "journey_progress_seed_percent", 0.0
            ),
        )

    async def _load_actions(
        self,
        uow,
        user_id: str,
        start_utc,
        end_utc,
    ) -> list[JourneyAction]:
        if end_utc <= start_utc:
            return []

        actions: list[JourneyAction] = []
        for row in await uow.meals.fetch_journey_progress_meals(
            user_id, start_utc, end_utc
        ):
            actions.append(
                JourneyAction(
                    source="meal",
                    label=row["label"],
                    logged_at=row["logged_at"],
                    calories=row["calories"],
                    protein_g=row["protein_g"],
                )
            )
        for row in await uow.hydration_entries.fetch_journey_progress_hydration(
            user_id, start_utc, end_utc
        ):
            actions.append(
                JourneyAction(
                    source="hydration",
                    label=row["label"],
                    logged_at=row["logged_at"],
                    hydration_ml=row["hydration_ml"],
                    calories=row.get("calories", 0.0),
                    protein_g=row.get("protein_g", 0.0),
                )
            )
        for row in await uow.movement_entries.fetch_journey_progress_movements(
            user_id, start_utc, end_utc
        ):
            actions.append(
                JourneyAction(
                    source="activity",
                    label=row["label"],
                    logged_at=row["logged_at"],
                )
            )
        for entry in await uow.weight_entries.find_by_recorded_range(
            user_id, start_utc, end_utc
        ):
            actions.append(
                JourneyAction(
                    source="weight",
                    label="Weight log",
                    logged_at=entry.recorded_at,
                )
            )
        return actions

    async def _load_targets(self, user_id: str) -> tuple[float, float]:
        if self.target_loader is not None:
            return await self.target_loader(user_id)

        from src.app.handlers.query_handlers.get_user_tdee_query_handler import (
            GetUserTdeeQueryHandler,
        )

        result = await GetUserTdeeQueryHandler(cache_service=self.cache_service).handle(
            GetUserTdeeQuery(user_id=user_id)
        )
        macros = result.get("macros") or {}
        return float(result.get("target_calories") or 0), float(
            macros.get("protein") or 0
        )
