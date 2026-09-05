"""
GetUserProfileQueryHandler - Individual handler file.
Auto-extracted for better maintainability.
"""

import logging
from typing import Any

from src.api.exceptions import ResourceNotFoundException
from src.app.events.base import EventHandler, handles
from src.app.queries.user import GetUserProfileQuery
from src.domain.cache.cache_keys import CacheKeys
from src.domain.mappers.activity_goal_mapper import ActivityGoalMapper
from src.domain.model.user import Sex, TdeeRequest, UnitSystem
from src.domain.ports.cache_port import CachePort
from src.domain.services.tdee_service import TdeeCalculationService
from src.infra.database.models.user.profile import UserProfile
from src.infra.database.uow_async import AsyncUnitOfWork

logger = logging.getLogger(__name__)


@handles(GetUserProfileQuery)
class GetUserProfileQueryHandler(EventHandler[GetUserProfileQuery, dict[str, Any]]):
    """Handler for getting user profile with TDEE calculation."""

    def __init__(
        self,
        tdee_service: TdeeCalculationService = None,
        cache_service: CachePort | None = None,
    ):
        self.tdee_service = tdee_service or TdeeCalculationService()
        self.cache_service = cache_service

    async def handle(self, query: GetUserProfileQuery) -> dict[str, Any]:
        """Get user profile with calculated TDEE. Reads/writes Redis cache."""
        cache_key, ttl = CacheKeys.user_profile(query.user_id)

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

        result = await self._fetch_from_db(query)

        if self.cache_service:
            await self.cache_service.set_json(
                cache_key, result, ttl, revision_field="profile_target_revision"
            )

        return result

    async def _fetch_from_db(self, query: GetUserProfileQuery) -> dict[str, Any]:
        """Fetch profile from DB and compute TDEE."""
        from sqlalchemy import select

        async with AsyncUnitOfWork() as uow:
            result = await uow.session.execute(
                select(UserProfile).where(UserProfile.user_id == query.user_id)
            )
            profile = result.scalars().first()

            if not profile:
                raise ResourceNotFoundException(
                    f"Profile for user {query.user_id} not found"
                )

            sex = Sex.MALE if profile.gender.lower() == "male" else Sex.FEMALE

            tdee_request = TdeeRequest(
                age=profile.age,
                sex=sex,
                height=profile.height_cm,
                weight=profile.weight_kg,
                job_type=ActivityGoalMapper.map_job_type(profile.job_type),
                training_days_per_week=profile.training_days_per_week,
                training_minutes_per_session=profile.training_minutes_per_session,
                training_level=ActivityGoalMapper.map_training_level(
                    profile.training_level
                ),
                goal=ActivityGoalMapper.map_goal(profile.fitness_goal),
                body_fat_pct=profile.body_fat_percentage,
                unit_system=UnitSystem.METRIC,
            )

            tdee_result = self.tdee_service.calculate_tdee(tdee_request)

            return {
                "profile": {
                    "id": profile.id,
                    "user_id": profile.user_id,
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
                    "meals_per_day": profile.meals_per_day,
                    "snacks_per_day": profile.snacks_per_day,
                    "custom_protein_g": profile.custom_protein_g,
                    "custom_carbs_g": profile.custom_carbs_g,
                    "custom_fat_g": profile.custom_fat_g,
                    "dietary_preferences": profile.dietary_preferences or [],
                    "health_conditions": profile.health_conditions or [],
                    "allergies": profile.allergies or [],
                    "created_at": (
                        profile.created_at.isoformat() if profile.created_at else None
                    ),
                    "updated_at": (
                        profile.updated_at.isoformat() if profile.updated_at else None
                    ),
                },
                "tdee": tdee_result.to_dict(),
                "profile_target_revision": profile.profile_target_revision,
                "target_revision": profile.profile_target_revision,
            }

    async def _current_profile_revision(self, user_id: str) -> int | None:
        from sqlalchemy import select

        async with AsyncUnitOfWork() as uow:
            result = await uow.session.execute(
                select(UserProfile.profile_target_revision).where(
                    UserProfile.user_id == user_id, UserProfile.is_current.is_(True)
                )
            )
            return result.scalar_one_or_none()
