"""Abstract port for the async Unit of Work."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.domain.ports.body_fat_visual_profile_repository_port import (
        BodyFatVisualProfileRepositoryPort,
    )
    from src.domain.ports.favorite_meal_repository_port import (
        FavoriteMealRepositoryPort,
    )
    from src.domain.ports.meal_repository_port import MealRepositoryPort
    from src.domain.ports.meal_suggestion_repository_port import (
        MealSuggestionRepositoryPort,
    )
    from src.domain.ports.notification_repository_port import NotificationRepositoryPort
    from src.domain.ports.saved_suggestion_repository_port import (
        SavedSuggestionRepositoryPort,
    )
    from src.domain.ports.subscription_repository_port import SubscriptionRepositoryPort
    from src.domain.ports.user_repository_port import UserRepositoryPort


class AsyncUnitOfWorkPort(ABC):
    """Async context manager interface for database transactions."""

    session: Any

    users: UserRepositoryPort
    meals: MealRepositoryPort
    favorite_meals: FavoriteMealRepositoryPort
    meal_recommendation_plans: Any
    meal_suggestions: MealSuggestionRepositoryPort
    subscriptions: SubscriptionRepositoryPort
    notifications: NotificationRepositoryPort
    saved_suggestions: SavedSuggestionRepositoryPort
    saved_suggestions_db: SavedSuggestionRepositoryPort
    body_fat_visual_profiles: BodyFatVisualProfileRepositoryPort

    weekly_budgets: Any
    cheat_days: Any
    hydration_entries: Any
    weight_entries: Any
    movement_entries: Any
    food_references: Any
    catalog_recipes: Any
    meal_translations: Any
    promo_codes: Any
    referrals: Any
    meal_write_operations: Any

    @abstractmethod
    async def __aenter__(self) -> AsyncUnitOfWorkPort:
        pass

    @abstractmethod
    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        pass

    @abstractmethod
    async def commit(self) -> None:
        pass

    @abstractmethod
    async def rollback(self) -> None:
        pass

    @abstractmethod
    async def refresh(self, obj: Any) -> None:
        pass
