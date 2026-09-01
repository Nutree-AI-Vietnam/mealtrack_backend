"""Async Unit of Work backed by asyncpg + AsyncSession."""

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.model.meal_suggestion import MealSuggestion, SuggestionSession
from src.domain.ports.async_unit_of_work_port import AsyncUnitOfWorkPort
from src.domain.ports.meal_suggestion_repository_port import (
    MealSuggestionRepositoryPort,
)
from src.infra.database.config_async import AsyncSessionLocal
from src.infra.repositories.body_fat_visual_profile_repository_async import (
    AsyncBodyFatVisualProfileRepository,
)
from src.infra.repositories.catalog_recipe_repository_async import (
    AsyncCatalogMealRepository,
)
from src.infra.repositories.chat_repository_async import AsyncChatRepository
from src.infra.repositories.cheat_day_repository_async import AsyncCheatDayRepository
from src.infra.repositories.food_reference_integrity_repository import (
    FoodReferenceIntegrityRepository,
)
from src.infra.repositories.food_reference_repository_async import (
    AsyncFoodReferenceRepository,
)
from src.infra.repositories.hydration_repository_async import AsyncHydrationRepository
from src.infra.repositories.meal_recommendation_plan_repository_async import (
    AsyncMealRecommendationPlanRepository,
)
from src.infra.repositories.meal_repository_async import AsyncMealRepository
from src.infra.repositories.meal_translation_repository_async import (
    AsyncMealTranslationRepository,
)
from src.infra.repositories.meal_write_operation_repository_async import (
    AsyncMealWriteOperationRepository,
)
from src.infra.repositories.movement_repository_async import AsyncMovementRepository
from src.infra.repositories.notification_repository_async import (
    AsyncNotificationRepository,
)
from src.infra.repositories.promo_code_repository import PromoCodeRepository
from src.infra.repositories.referral_repository import ReferralRepository
from src.infra.repositories.saved_suggestion_db_repository_async import (
    AsyncSavedSuggestionDbRepository,
)
from src.infra.repositories.subscription_repository_async import (
    AsyncSubscriptionRepository,
)
from src.infra.repositories.user_repository_async import AsyncUserRepository
from src.infra.repositories.weekly_budget_repository_async import (
    AsyncWeeklyBudgetRepository,
)
from src.infra.repositories.weight_repository_async import AsyncWeightRepository

logger = logging.getLogger(__name__)


class UnavailableMealSuggestionSessionStore(MealSuggestionRepositoryPort):
    """Placeholder for Redis-backed suggestion sessions outside DB transactions."""

    @staticmethod
    def _raise() -> None:
        raise RuntimeError(
            "Meal suggestion sessions are Redis-backed transient state. "
            "Use src.api.base_dependencies.get_meal_suggestion_repository() "
            "instead of AsyncUnitOfWork.meal_suggestions."
        )

    async def save_session(self, session: SuggestionSession) -> None:
        self._raise()

    async def get_session(self, session_id: str) -> SuggestionSession | None:
        self._raise()

    async def update_session(self, session: SuggestionSession) -> None:
        self._raise()

    async def delete_session(self, session_id: str) -> None:
        self._raise()

    async def save_suggestions(self, suggestions: list[MealSuggestion]) -> None:
        self._raise()

    async def get_suggestion(self, suggestion_id: str) -> MealSuggestion | None:
        self._raise()

    async def update_suggestion(self, suggestion: MealSuggestion) -> None:
        self._raise()


class AsyncUnitOfWork(AsyncUnitOfWorkPort):
    """SQLAlchemy AsyncSession Unit of Work.

    Usage:
        async with AsyncUnitOfWork() as uow:
            result = await uow.meals.find_by_id(meal_id)
    """

    def __init__(self):
        self.session: AsyncSession | None = None
        self._session_lock = asyncio.Lock()

    async def __aenter__(self) -> "AsyncUnitOfWork":
        await self._session_lock.acquire()
        if AsyncSessionLocal is None:
            self._session_lock.release()
            raise RuntimeError(
                "AsyncSessionLocal is not initialized. Async engine setup failed; "
                "check async DB configuration."
            )
        self.session = AsyncSessionLocal()
        self._init_repositories()
        return self

    def _init_repositories(self):
        session = self._require_session()
        self.meals = AsyncMealRepository(session)
        self.meal_recommendation_plans = AsyncMealRecommendationPlanRepository(session)
        self.meal_suggestions = UnavailableMealSuggestionSessionStore()
        self.body_fat_visual_profiles = AsyncBodyFatVisualProfileRepository(session)
        self.hydration_entries = AsyncHydrationRepository(session)
        self.users = AsyncUserRepository(session)
        self.weekly_budgets = AsyncWeeklyBudgetRepository(session)
        self.cheat_days = AsyncCheatDayRepository(session)
        self.subscriptions = AsyncSubscriptionRepository(session)
        self.notifications = AsyncNotificationRepository(session)
        self.saved_suggestions = AsyncSavedSuggestionDbRepository(session)
        self.saved_suggestions_db = (
            self.saved_suggestions
        )  # alias for handlers using this name
        self.weight_entries = AsyncWeightRepository(session)
        self.movement_entries = AsyncMovementRepository(session)
        self.food_references = AsyncFoodReferenceRepository(session)
        self.food_reference_integrity = FoodReferenceIntegrityRepository(session)
        self.catalog_recipes = AsyncCatalogMealRepository(session)
        self.meal_translations = AsyncMealTranslationRepository(session)
        self.promo_codes = PromoCodeRepository(session)
        self.referrals = ReferralRepository(session)
        self.meal_write_operations = AsyncMealWriteOperationRepository(session)
        self.chat = AsyncChatRepository(session)

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        session = self.session
        if session is None:
            return
        try:
            if exc_type:
                try:
                    await self.rollback()
                except Exception:
                    logger.warning(
                        "Rollback failed; connection will be discarded", exc_info=True
                    )
            else:
                try:
                    await self.commit()
                except Exception:
                    try:
                        await self.rollback()
                    except Exception:
                        logger.warning(
                            "Rollback failed after commit error; connection will be discarded",
                            exc_info=True,
                        )
                    raise
        finally:
            await session.close()
            self.session = None
            self._session_lock.release()

    async def commit(self) -> None:
        await self._require_session().commit()

    async def rollback(self) -> None:
        await self._require_session().rollback()

    async def refresh(self, obj) -> None:
        await self._require_session().refresh(obj)

    def _require_session(self) -> AsyncSession:
        if self.session is None:
            raise RuntimeError("AsyncUnitOfWork session is not initialized.")
        return self.session
