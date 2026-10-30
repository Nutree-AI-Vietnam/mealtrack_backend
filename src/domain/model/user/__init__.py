"""User domain models."""

from .activity import Activity as UserActivity
from .core_user import UserDomainModel, UserProfileDomainModel
from .onboarding import OnboardingResponse, OnboardingSection
from .tdee import (
    Goal,
    JobType,
    MacroPreset,
    MacroTargets,
    PaceRange,
    Sex,
    TdeeRequest,
    TdeeResponse,
    TrainingLevel,
    UnitSystem,
)
from .user_macros import UserMacros

# Alias for backward compatibility if needed, but explicit is better
TDEE = TdeeResponse

__all__ = [
    "UserActivity",
    "JobType",
    "TrainingLevel",
    "MacroPreset",
    "OnboardingSection",
    "OnboardingResponse",
    "TDEE",
    "TdeeResponse",
    "TdeeRequest",
    "Sex",
    "Goal",
    "UnitSystem",
    "MacroTargets",
    "PaceRange",
    "UserMacros",
    "UserDomainModel",
    "UserProfileDomainModel",
]
