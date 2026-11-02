"""Command to re-analyze a scanned meal photo with a user correction."""

from dataclasses import dataclass


@dataclass(frozen=True)
class FixMealResultCommand:
    """Fix an existing scanned meal using the stored image + correction text."""

    meal_id: str
    user_id: str
    correction: str
    language: str = "en"
