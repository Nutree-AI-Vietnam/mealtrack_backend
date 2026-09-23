"""Controlled weekly meal planner failures."""


class WeeklyMealPlannerDomainError(Exception):
    """Base domain exception for weekly meal planner."""

    def __init__(
        self,
        message: str = "Weekly meal planner error",
        *,
        error_code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code


class WeeklyMealPlanNotFoundError(WeeklyMealPlannerDomainError):
    def __init__(self, message: str = "Weekly meal plan not found") -> None:
        super().__init__(message, error_code="WEEKLY_PLAN_NOT_FOUND")


class WeeklyMealPlanConflictError(WeeklyMealPlannerDomainError):
    def __init__(
        self,
        message: str = "Weekly meal plan cannot be changed in its current state",
        *,
        error_code: str = "WEEKLY_PLAN_CONFLICT",
    ) -> None:
        super().__init__(message, error_code=error_code)


class WeeklyMealPlanIdempotencyConflictError(WeeklyMealPlanConflictError):
    def __init__(
        self,
        message: str = "Idempotency-Key was already used for a different weekly plan request",
    ) -> None:
        super().__init__(
            message,
            error_code="IDEMPOTENCY_KEY_REUSED",
        )
