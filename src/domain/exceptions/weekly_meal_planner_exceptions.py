"""Controlled weekly meal planner failures."""

from src.api.exceptions import ConflictException, ResourceNotFoundException


class WeeklyMealPlanNotFoundError(ResourceNotFoundException):
    def __init__(self) -> None:
        super().__init__("Weekly meal plan not found")


class WeeklyMealPlanConflictError(ConflictException):
    def __init__(self, message: str = "Weekly meal plan cannot be changed in its current state") -> None:
        super().__init__(message, error_code="WEEKLY_PLAN_CONFLICT")


class WeeklyMealPlanIdempotencyConflictError(ConflictException):
    def __init__(self) -> None:
        super().__init__(
            "Idempotency-Key was already used for a different weekly plan request",
            error_code="IDEMPOTENCY_KEY_REUSED",
        )
