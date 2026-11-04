"""Exceptions raised while serving the meal catalog."""


class MealRecommendationCreationError(Exception):
    """Base error for catalog-backed meal failures.

    The event bus maps this to an HTTP response rather than a 500, so every
    catalog error below inherits it.
    """

    public_detail = "Unable to create meal recommendations"
    status_code = 503

    def __init__(self, message: str | None = None):
        super().__init__(message or self.public_detail)


class MealRecommendationCatalogUnavailableError(MealRecommendationCreationError):
    """Raised when no usable catalog release is available."""

    public_detail = "Meal recommendation catalog is unavailable"
    status_code = 503
