"""
Shared user profile service for meal plan handlers.
Follows clean architecture principles.
"""

import logging
from typing import Dict, Any

from src.domain.mappers.activity_goal_mapper import ActivityGoalMapper
from src.domain.model.meal_planning import (
    UserPreferences,
    DietaryPreference,
    FitnessGoal,
    PlanDuration,
)
from src.domain.model.user import TdeeRequest, Sex, Goal, UnitSystem
from src.domain.ports.user_repository_port import UserRepositoryPort
from src.domain.services.tdee_service import TdeeCalculationService

logger = logging.getLogger(__name__)


class UserProfileService:
    """
    Domain service for user profile operations.
    Orchestrates profile retrieval and TDEE calculation.
    """

    def __init__(
        self, user_repo: UserRepositoryPort, tdee_service: TdeeCalculationService
    ):
        self.user_repo = user_repo
        self.tdee_service = tdee_service

    async def get_user_profile_or_defaults(self, user_id: str) -> Dict[str, Any]:
        """Get user profile data or provide sensible defaults."""
        # get_profile is synchronous, not async
        profile = self.user_repo.get_profile(user_id)

        if profile:
            # Calculate TDEE using domain service with centralized mapper
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
                weight_loss_kg_per_week=profile.weight_loss_kg_per_week,
                weight_gain_kg_per_week=profile.weight_gain_kg_per_week,
                health_conditions=tuple(profile.health_conditions or ()),
            )

            tdee_result = self.tdee_service.calculate_tdee(tdee_request)

            return {
                "dietary_preferences": profile.dietary_preferences or [],
                "allergies": profile.allergies or [],
                "target_calories": tdee_result.macros.total_calories,
                "target_protein": tdee_result.macros.protein,
                "target_carbs": tdee_result.macros.carbs,
                "target_fat": tdee_result.macros.fat,
                "meals_per_day": profile.meals_per_day,
                "include_snacks": profile.snacks_per_day > 0,
                "age": profile.age,
                "gender": profile.gender,
                "job_type": profile.job_type,
                "training_days_per_week": profile.training_days_per_week,
                "training_minutes_per_session": profile.training_minutes_per_session,
                "training_level": profile.training_level,
                "fitness_goal": profile.fitness_goal,
                "health_conditions": profile.health_conditions or [],
            }
        else:
            # Default values when no profile exists
            return {
                "dietary_preferences": [],
                "allergies": [],
                "target_calories": 2000,
                "target_protein": 150,
                "target_carbs": 250,
                "target_fat": 70,
                "meals_per_day": 3,
                "include_snacks": True,
                "age": 30,
                "gender": "male",
                "job_type": "desk",
                "training_days_per_week": 4,
                "training_minutes_per_session": 60,
                "training_level": None,
                "fitness_goal": "recomp",
                "health_conditions": [],
            }

    def create_user_preferences_from_data(
        self, data: Dict[str, Any], plan_duration: PlanDuration = PlanDuration.DAILY
    ) -> UserPreferences:
        """Create UserPreferences domain object from profile data."""
        # Convert string dietary preferences to domain enums
        valid_prefs = []
        for pref in data.get("dietary_preferences", []):
            try:
                valid_prefs.append(DietaryPreference(pref))
            except ValueError:
                logger.warning(f"Unknown dietary preference: {pref} – skipped")

        return UserPreferences(
            dietary_preferences=valid_prefs,
            allergies=data.get("allergies", []),
            fitness_goal=FitnessGoal(data.get("fitness_goal", "recomp")),
            meals_per_day=data.get("meals_per_day", 3),
            snacks_per_day=1 if data.get("include_snacks", False) else 0,
            cooking_time_weekday=30,  # Default
            cooking_time_weekend=45,  # Default
            favorite_cuisines=data.get("favorite_cuisines", []),
            disliked_ingredients=data.get("disliked_ingredients", []),
            plan_duration=plan_duration,
        )
