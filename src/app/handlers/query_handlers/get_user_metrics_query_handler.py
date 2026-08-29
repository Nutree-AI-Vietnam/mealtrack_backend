"""
GetUserMetricsQueryHandler - Individual handler file.
Auto-extracted for better maintainability.
"""

import logging
from typing import Any, Dict, Optional

from sqlalchemy import select

from src.api.exceptions import ResourceNotFoundException
from src.app.events.base import EventHandler, handles
from src.app.queries.user import GetUserMetricsQuery
from src.domain.cache.cache_keys import CacheKeys
from src.domain.ports.cache_port import CachePort
from src.infra.database.models.user.profile import UserProfile
from src.infra.database.uow_async import AsyncUnitOfWork

logger = logging.getLogger(__name__)


@handles(GetUserMetricsQuery)
class GetUserMetricsQueryHandler(EventHandler[GetUserMetricsQuery, Dict[str, Any]]):
    """Handler for getting user's current metrics for settings display."""

    def __init__(self, cache_service: Optional[CachePort] = None):
        self.cache_service = cache_service

    async def handle(self, query: GetUserMetricsQuery) -> Dict[str, Any]:
        """Get user's current metrics."""
        cache_key, ttl = CacheKeys.user_metrics(query.user_id)
        if self.cache_service:
            cached = await self.cache_service.get_json(cache_key)
            if cached is not None:
                cached_revision = cached.get("profile_target_revision")
                if (
                    cached_revision is None
                    or cached_revision
                    == await self._current_profile_revision(query.user_id)
                ):
                    return cached
        result = await self._compute(query)
        if self.cache_service:
            await self.cache_service.set_json(
                cache_key, result, ttl, revision_field="profile_target_revision"
            )
        return result

    async def _compute(self, query: GetUserMetricsQuery) -> Dict[str, Any]:
        """Fetch user metrics from DB."""
        async with AsyncUnitOfWork() as uow:
            # Get current user profile using the UnitOfWork session
            result = await uow.session.execute(
                select(UserProfile).where(
                    UserProfile.user_id == query.user_id,
                    UserProfile.is_current.is_(True),
                )
            )
            profile = result.scalars().first()

            if not profile:
                raise ResourceNotFoundException(
                    f"Current profile for user {query.user_id} not found"
                )

            return {
                "user_id": query.user_id,
                "age": profile.age,
                "gender": profile.gender,
                "height_cm": profile.height_cm,
                "weight_kg": profile.weight_kg,
                "body_fat_percentage": profile.body_fat_percentage,
                "job_type": profile.job_type,
                "training_days_per_week": profile.training_days_per_week,
                "training_minutes_per_session": profile.training_minutes_per_session,
                "training_level": profile.training_level,
                "fitness_goal": profile.fitness_goal,
                "target_weight_kg": profile.target_weight_kg,
                "goal_start_weight_kg": profile.goal_start_weight_kg,
                "goal_started_at": profile.goal_started_at,
                "journey_progress_seed_percent": getattr(
                    profile, "journey_progress_seed_percent", 0.0
                ),
                "updated_at": profile.updated_at,
                "profile_target_revision": profile.profile_target_revision,
                "target_revision": profile.profile_target_revision,
            }

    async def _current_profile_revision(self, user_id: str) -> int | None:
        async with AsyncUnitOfWork() as uow:
            result = await uow.session.execute(
                select(UserProfile.profile_target_revision).where(
                    UserProfile.user_id == user_id, UserProfile.is_current.is_(True)
                )
            )
            return result.scalar_one_or_none()
