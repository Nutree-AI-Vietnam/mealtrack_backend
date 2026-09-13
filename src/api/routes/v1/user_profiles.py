"""
User profiles API endpoints - Event-driven architecture.
Handles user profile management and TDEE calculations.
"""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException

from src.api.dependencies.auth import get_current_user_id
from src.api.dependencies.event_bus import get_configured_event_bus
from src.api.exceptions import handle_exception
from src.api.mappers.tdee_mapper import TdeeMapper
from src.api.schemas.request import OnboardingCompleteRequest
from src.api.schemas.request.body_fat_visual_requests import BodyFatVisualProfileRequest
from src.api.schemas.request.custom_macros_request import UpdateCustomMacrosRequest
from src.api.schemas.request.user_profile_update_requests import UpdateMetricsRequest
from src.api.schemas.response import (
    BodyFatVisualProfileResponse,
    TdeeCalculationResponse,
    UserMetricsResponse,
)
from src.app.commands.user import (
    SaveBodyFatVisualProfileCommand,
    SaveUserOnboardingCommand,
)
from src.app.commands.user.update_custom_macros_command import UpdateCustomMacrosCommand
from src.app.commands.user.update_user_metrics_command import UpdateUserMetricsCommand
from src.app.queries.tdee import GetUserTdeeQuery
from src.app.queries.user import GetBodyFatVisualProfileQuery, GetUserMetricsQuery
from src.domain.model.user import Goal, MacroTargets, TdeeResponse
from src.infra.event_bus import EventBus

router = APIRouter(prefix="/v1/user-profiles", tags=["User Profiles"])


@router.get("/body-fat-visual", response_model=BodyFatVisualProfileResponse)
async def get_body_fat_visual_profile(
    user_id: str = Depends(get_current_user_id),
    event_bus: EventBus = Depends(get_configured_event_bus),
):
    """Get the latest visual body-fat selection and append-only history."""
    profile = await event_bus.send(GetBodyFatVisualProfileQuery(user_id=user_id))
    if profile is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error_code": "BODY_FAT_VISUAL_UNSET",
                "message": "No visual body-fat selection has been saved",
                "details": {},
            },
        )
    return profile


@router.put("/body-fat-visual", response_model=BodyFatVisualProfileResponse)
async def save_body_fat_visual_profile(
    request: BodyFatVisualProfileRequest,
    user_id: str = Depends(get_current_user_id),
    event_bus: EventBus = Depends(get_configured_event_bus),
):
    """Append a visual estimate without changing measured body-fat or TDEE."""
    await event_bus.send(
        SaveBodyFatVisualProfileCommand(
            user_id=user_id,
            schema_version=request.schema_version,
            range_catalog_version=request.range_catalog_version,
            sex_at_selection=request.sex_at_selection,
            start_range_id=request.start_range_id,
            current_range_id=request.current_range_id,
            target_range_id=request.target_range_id,
        )
    )
    return await event_bus.send(GetBodyFatVisualProfileQuery(user_id=user_id))


@router.post("/", response_model=None)
async def save_user_onboarding(
    request: OnboardingCompleteRequest,
    user_id: str = Depends(get_current_user_id),
    event_bus: EventBus = Depends(get_configured_event_bus),
):
    """
    Save user onboarding data and return TDEE calculation.

    Creates/updates:
    - User profile with physical attributes
    - Pain points and dietary preferences
    - Fitness goals, job_type and training frequency
    - Meal preferences
    - Returns TDEE calculation and macro targets

    Authentication required: User ID is automatically extracted from the Firebase token.
    """
    try:
        # Compute age from DOB (validate date is real — e.g., reject Feb 31)
        try:
            dob = date(request.birth_year, request.birth_month, request.birth_day)
        except ValueError as e:
            from fastapi import HTTPException

            raise HTTPException(status_code=400, detail="Invalid birth date") from e
        today = date.today()
        age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))

        # Create command
        command = SaveUserOnboardingCommand(
            user_id=user_id,
            age=age,
            gender=request.gender,
            height_cm=request.height,
            weight_kg=request.weight,
            body_fat_percentage=request.body_fat_percentage,
            job_type=request.job_type,
            training_days_per_week=request.training_days_per_week,
            training_minutes_per_session=request.training_minutes_per_session,
            fitness_goal=request.goal,
            pain_points=request.pain_points,
            dietary_preferences=request.dietary_preferences,
            meals_per_day=request.meals_per_day,
            referral_sources=request.referral_sources,
            training_level=request.training_level,
            date_of_birth=dob,
            target_weight_kg=request.target_weight_kg,
            challenge_duration=request.challenge_duration,
            training_types=request.training_types,
            custom_protein_g=request.custom_protein_g,
            custom_carbs_g=request.custom_carbs_g,
            custom_fat_g=request.custom_fat_g,
        )

        await event_bus.send(command)
        return True

    except Exception as e:
        raise handle_exception(e) from e


@router.get("/metrics", response_model=UserMetricsResponse)
async def get_user_metrics(
    user_id: str = Depends(get_current_user_id),
    event_bus: EventBus = Depends(get_configured_event_bus),
):
    """
    Get user's current metrics for settings display.

    Retrieves the user's current profile metrics including:
    - Physical attributes (age, gender, height, weight, body fat)
    - Activity level
    - Fitness goal
    - Target weight

    Authentication required: User ID is automatically extracted from the Firebase token.
    """
    # Create query
    query = GetUserMetricsQuery(user_id=user_id)

    # Send query
    result = await event_bus.send(query)

    return UserMetricsResponse(**result)


@router.get("/tdee", response_model=TdeeCalculationResponse)
async def get_user_tdee(
    user_id: str = Depends(get_current_user_id),
    event_bus: EventBus = Depends(get_configured_event_bus),
):
    """
    Get user's current TDEE calculation based on their profile.

    Retrieves the user's current profile and calculates:
    - BMR using Mifflin-St Jeor or Katch-McArdle formula
    - TDEE based on job type and training frequency
    - Macro targets based on fitness goal

    Authentication required: User ID is automatically extracted from the Firebase token.
    """
    # Create query
    query = GetUserTdeeQuery(user_id=user_id)

    # Send query
    result = await event_bus.send(query)

    # Map goal string to enum
    goal_map = {"cut": Goal.CUT, "bulk": Goal.BULK, "recomp": Goal.RECOMP}

    # Create domain response
    domain_response = TdeeResponse(
        bmr=result["bmr"],
        tdee=result["tdee"],
        goal=goal_map[result["profile_data"]["fitness_goal"]],
        macros=MacroTargets(
            calories=result["macros"]["calories"],
            protein=result["macros"]["protein"],
            carbs=result["macros"]["carbs"],
            fat=result["macros"]["fat"],
        ),
    )

    # Use mapper to convert to response DTO
    mapper = TdeeMapper()
    response = mapper.to_response_dto(domain_response)

    # Add additional metadata
    response.activity_multiplier = result["activity_multiplier"]
    response.formula_used = result["formula_used"]
    response.is_custom = result.get("is_custom", False)

    return response


@router.post("/metrics", response_model=TdeeCalculationResponse)
async def update_user_metrics(
    request: UpdateMetricsRequest,
    user_id: str = Depends(get_current_user_id),
    event_bus: EventBus = Depends(get_configured_event_bus),
):
    """
    Update user metrics (weight, job_type, training_days_per_week, training_minutes_per_session, body fat, fitness goal) and return updated TDEE/macros.

    Unified endpoint for profile updates.

    Authentication required: User ID is automatically extracted from the Firebase token.
    """
    import logging

    logger = logging.getLogger(__name__)
    # Debug: log incoming request values
    logger.info(
        f"update_user_metrics: goal={request.fitness_goal}, "
        f"target_weight_kg={request.target_weight_kg}, "
        f"weight_kg={request.weight_kg}"
    )
    body_fat_provided = (
        request.body_fat_percentage is not None or request.reset_body_fat
    )

    # Update metrics (including optional fitness goal and target weight)
    command = UpdateUserMetricsCommand(
        user_id=user_id,
        age=request.age,
        height_cm=request.height_cm,
        weight_kg=request.weight_kg,
        biological_sex=request.biological_sex,
        job_type=request.job_type,
        training_days_per_week=request.training_days_per_week,
        training_minutes_per_session=request.training_minutes_per_session,
        body_fat_percent=(
            None if request.reset_body_fat else request.body_fat_percentage
        ),
        body_fat_percent_provided=body_fat_provided,
        fitness_goal=request.fitness_goal.value if request.fitness_goal else None,
        training_level=(
            request.training_level.value if request.training_level else None
        ),
        target_weight_kg=request.target_weight_kg,
        goal_start_weight_kg=request.goal_start_weight_kg,
        goal_started_at=request.goal_started_at,
        daily_water_goal_ml=request.daily_water_goal_ml,
        reset_water_goal=request.reset_water_goal,
    )

    await event_bus.send(command)

    # Return updated TDEE/macros
    query = GetUserTdeeQuery(user_id=user_id)
    result = await event_bus.send(query)

    goal_map = {"cut": Goal.CUT, "bulk": Goal.BULK, "recomp": Goal.RECOMP}

    domain_response = TdeeResponse(
        bmr=result["bmr"],
        tdee=result["tdee"],
        goal=goal_map[result["profile_data"]["fitness_goal"]],
        macros=MacroTargets(
            calories=result["macros"]["calories"],
            protein=result["macros"]["protein"],
            carbs=result["macros"]["carbs"],
            fat=result["macros"]["fat"],
        ),
    )

    mapper = TdeeMapper()
    response = mapper.to_response_dto(domain_response)
    response.activity_multiplier = result["activity_multiplier"]
    response.formula_used = result["formula_used"]
    response.is_custom = result.get("is_custom", False)
    return response


@router.put("/custom-macros", response_model=TdeeCalculationResponse)
async def update_custom_macros(
    request: UpdateCustomMacrosRequest,
    user_id: str = Depends(get_current_user_id),
    event_bus: EventBus = Depends(get_configured_event_bus),
):
    """
    Set or clear custom macro targets.

    Send all three values to set custom macros.
    Send all null to reset to algorithm-calculated values.
    """
    command = UpdateCustomMacrosCommand(
        user_id=user_id,
        protein_g=request.protein_g,
        carbs_g=request.carbs_g,
        fat_g=request.fat_g,
    )
    await event_bus.send(command)

    # Return updated TDEE (reflects custom values if set)
    query = GetUserTdeeQuery(user_id=user_id)
    result = await event_bus.send(query)

    goal_map = {
        "cut": Goal.CUT,
        "bulk": Goal.BULK,
        "recomp": Goal.RECOMP,
    }

    domain_response = TdeeResponse(
        bmr=result["bmr"],
        tdee=result["tdee"],
        goal=goal_map[result["profile_data"]["fitness_goal"]],
        macros=MacroTargets(
            calories=result["macros"]["calories"],
            protein=result["macros"]["protein"],
            carbs=result["macros"]["carbs"],
            fat=result["macros"]["fat"],
        ),
    )

    mapper = TdeeMapper()
    response = mapper.to_response_dto(domain_response)
    response.activity_multiplier = result["activity_multiplier"]
    response.formula_used = result["formula_used"]
    response.is_custom = result.get("is_custom", False)
    return response
