"""Mappers for converting between domain models and persistence models."""

from collections.abc import Iterable

from sqlalchemy.exc import InvalidRequestError

from src.domain.model.user import UserDomainModel, UserProfileDomainModel
from src.infra.database.models.user.profile import UserProfile
from src.infra.database.models.user.profile_preference import UserProfilePreference
from src.infra.database.models.user.user import User

PROFILE_PREFERENCE_FIELDS = (
    "dietary_preferences",
    "health_conditions",
    "allergies",
    "pain_points",
    "referral_sources",
    "training_types",
)


def _preference_values(values: Iterable[object] | None) -> list[str]:
    """Return cleaned, de-duplicated values while preserving first order."""
    if not values:
        return []

    cleaned: list[str] = []
    seen: set[str] = set()
    for raw_value in values:
        if raw_value is None:
            continue
        value = str(raw_value).strip()
        if not value:
            continue
        normalized = value.casefold()
        if normalized in seen:
            continue
        seen.add(normalized)
        cleaned.append(value)
    return cleaned


def _loaded_preference_entries(
    profile_entity: UserProfile,
) -> list[UserProfilePreference] | None:
    try:
        return list(profile_entity.preference_entries)
    except InvalidRequestError:
        return None


def _values_from_normalized(
    profile_entity: UserProfile, field_name: str
) -> list[str] | None:
    entries = _loaded_preference_entries(profile_entity)
    if entries is None:
        return None

    values = [
        entry.value
        for entry in sorted(
            entries, key=lambda row: (row.preference_type, row.position)
        )
        if entry.preference_type == field_name
    ]
    return _preference_values(values) if values else None


def _profile_values(profile_entity: UserProfile, field_name: str) -> list[str]:
    normalized_values = _values_from_normalized(profile_entity, field_name)
    if normalized_values is not None:
        return normalized_values
    return _preference_values(getattr(profile_entity, field_name, None))


def build_profile_preference_entries(
    profile_domain: UserProfileDomainModel,
) -> list[UserProfilePreference]:
    entries: list[UserProfilePreference] = []
    profile_id = str(profile_domain.id) if profile_domain.id else None

    for field_name in PROFILE_PREFERENCE_FIELDS:
        values = _preference_values(getattr(profile_domain, field_name, None))
        for position, value in enumerate(values):
            entries.append(
                UserProfilePreference(
                    profile_id=profile_id,
                    preference_type=field_name,
                    value=value,
                    position=position,
                )
            )

    return entries


class UserMapper:
    """Handles mapping between User domain and persistence models."""

    @staticmethod
    def to_domain(user_entity: User) -> UserDomainModel:
        """Map a User persistence entity to a UserDomainModel."""
        return UserDomainModel(
            id=user_entity.id,
            firebase_uid=user_entity.firebase_uid,
            email=user_entity.email,
            username=user_entity.username,
            password_hash=user_entity.password_hash,
            provider=user_entity.provider,
            is_active=user_entity.is_active,
            onboarding_completed=user_entity.onboarding_completed,
            last_accessed=user_entity.last_accessed,
            timezone=user_entity.timezone,
            first_name=user_entity.first_name,
            last_name=user_entity.last_name,
            phone_number=user_entity.phone_number,
            display_name=user_entity.display_name,
            photo_url=user_entity.photo_url,
            deleted_at=user_entity.deleted_at,
            language_code=user_entity.language_code,
            created_at=user_entity.created_at,
            updated_at=user_entity.updated_at,
            profiles=[UserProfileMapper.to_domain(p) for p in user_entity.profiles],
        )

    @staticmethod
    def to_persistence(user_domain: UserDomainModel) -> User:
        """Map a UserDomainModel to a User persistence entity."""
        values = {
            "id": str(user_domain.id) if user_domain.id else None,
            "firebase_uid": user_domain.firebase_uid,
            "email": user_domain.email,
            "username": user_domain.username,
            "password_hash": user_domain.password_hash,
            "provider": user_domain.provider,
            "is_active": user_domain.is_active,
            "onboarding_completed": user_domain.onboarding_completed,
            "last_accessed": user_domain.last_accessed,
            "timezone": user_domain.timezone,
            "first_name": user_domain.first_name,
            "last_name": user_domain.last_name,
            "phone_number": user_domain.phone_number,
            "display_name": user_domain.display_name,
            "photo_url": user_domain.photo_url,
            "deleted_at": user_domain.deleted_at,
        }
        # Omit a missing language so INSERT keeps the column default and
        # updates do not null out a language this mapper did not load.
        if user_domain.language_code:
            values["language_code"] = user_domain.language_code
        return User(**values)


class UserProfileMapper:
    """Handles mapping between UserProfile domain and persistence models."""

    @staticmethod
    def to_domain(profile_entity: UserProfile) -> UserProfileDomainModel:
        """Map a UserProfile persistence entity to a UserProfileDomainModel."""
        return UserProfileDomainModel(
            id=profile_entity.id,
            user_id=profile_entity.user_id,
            age=profile_entity.age,
            gender=profile_entity.gender,
            height_cm=profile_entity.height_cm,
            weight_kg=profile_entity.weight_kg,
            job_type=profile_entity.job_type,
            training_days_per_week=profile_entity.training_days_per_week,
            training_minutes_per_session=profile_entity.training_minutes_per_session,
            fitness_goal=profile_entity.fitness_goal,
            meals_per_day=profile_entity.meals_per_day,
            is_current=profile_entity.is_current,
            body_fat_percentage=profile_entity.body_fat_percentage,
            target_weight_kg=profile_entity.target_weight_kg,
            snacks_per_day=profile_entity.snacks_per_day,
            dietary_preferences=_profile_values(profile_entity, "dietary_preferences"),
            health_conditions=_profile_values(profile_entity, "health_conditions"),
            allergies=_profile_values(profile_entity, "allergies"),
            pain_points=_profile_values(profile_entity, "pain_points"),
            training_level=profile_entity.training_level,
            date_of_birth=profile_entity.date_of_birth,
            referral_sources=_profile_values(profile_entity, "referral_sources"),
            challenge_duration=profile_entity.challenge_duration,
            training_types=_profile_values(profile_entity, "training_types"),
            custom_protein_g=profile_entity.custom_protein_g,
            custom_carbs_g=profile_entity.custom_carbs_g,
            custom_fat_g=profile_entity.custom_fat_g,
            goal_start_weight_kg=profile_entity.goal_start_weight_kg,
            goal_started_at=profile_entity.goal_started_at,
            journey_progress_seed_percent=float(
                profile_entity.journey_progress_seed_percent or 0.0
            ),
            daily_water_goal_ml=profile_entity.daily_water_goal_ml,
            profile_target_revision=profile_entity.profile_target_revision,
            created_at=profile_entity.created_at,
            updated_at=profile_entity.updated_at,
        )

    @staticmethod
    def to_persistence(profile_domain: UserProfileDomainModel) -> UserProfile:
        """Map a UserProfileDomainModel to a UserProfile persistence entity."""
        profile = UserProfile(
            id=str(profile_domain.id) if profile_domain.id else None,
            user_id=str(profile_domain.user_id) if profile_domain.user_id else None,
            age=profile_domain.age,
            gender=profile_domain.gender,
            height_cm=profile_domain.height_cm,
            weight_kg=profile_domain.weight_kg,
            job_type=profile_domain.job_type,
            training_days_per_week=profile_domain.training_days_per_week,
            training_minutes_per_session=profile_domain.training_minutes_per_session,
            fitness_goal=profile_domain.fitness_goal,
            meals_per_day=profile_domain.meals_per_day,
            is_current=profile_domain.is_current,
            body_fat_percentage=profile_domain.body_fat_percentage,
            target_weight_kg=profile_domain.target_weight_kg,
            snacks_per_day=profile_domain.snacks_per_day,
            dietary_preferences=profile_domain.dietary_preferences,
            health_conditions=profile_domain.health_conditions,
            allergies=profile_domain.allergies,
            pain_points=profile_domain.pain_points,
            training_level=profile_domain.training_level,
            date_of_birth=profile_domain.date_of_birth,
            referral_sources=profile_domain.referral_sources or [],
            challenge_duration=profile_domain.challenge_duration,
            training_types=profile_domain.training_types,
            custom_protein_g=profile_domain.custom_protein_g,
            custom_carbs_g=profile_domain.custom_carbs_g,
            custom_fat_g=profile_domain.custom_fat_g,
            goal_start_weight_kg=profile_domain.goal_start_weight_kg,
            goal_started_at=profile_domain.goal_started_at,
            journey_progress_seed_percent=profile_domain.journey_progress_seed_percent,
            daily_water_goal_ml=profile_domain.daily_water_goal_ml,
            profile_target_revision=profile_domain.profile_target_revision,
        )
        profile.preference_entries = build_profile_preference_entries(profile_domain)
        return profile
